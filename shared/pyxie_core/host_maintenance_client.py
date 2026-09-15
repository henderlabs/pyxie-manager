"""SSH client for Stage W4 host package-management. This is the ONLY module
in this codebase allowed to open an SSH connection to a PVE host, and it
talks to nothing but the restricted /usr/local/sbin/pyxie-maint wrapper via
`sudo` -- there is no code path here that runs an arbitrary command on a
host. Mirrors pve_write_client.py's own role for PVE API mutations: grep
for HostMaintenanceClient is a complete answer to "what can touch a host's
OS/package state from PyXie."

Contract (kept in sync with the wrapper script itself -- see
deploy/host-maintenance/pyxie-maint): six subcommands (version, status,
refresh, plan, apply, verify), each printing exactly one line of JSON to
stdout and exiting 0 on success. Anything else -- non-zero exit, non-JSON
output -- is a hard failure, never partially parsed or guessed at.

Two independent guardrails on apply(), matching every other mutation in
this codebase:
  1. PVE_MUTATIONS_ENABLED must be exactly "true" -- the SAME global kill
     switch pve_write_client.py uses, so there is one switch for every
     mutation PyXie can make, PVE API or host SSH alike.
  2. Everything else in the Safety Contract (approval, revalidation,
     locking, re-diffing the plan immediately before calling apply()) is
     the caller's job -- see host_update_workflow.py.

Host-key handling is deliberately strict, unlike the (looser) posture this
app has taken with PVE's own self-signed TLS certs: a HostMaintenanceClient
refuses to connect at all unless it has been given the node's EXPECTED
host key ahead of time (Node.ssh_host_key_*, set only via an explicit
operator-confirmed pin action -- see probe_host_key() below and
credentials.py) -- an unknown or a since-CHANGED key both hard-fail rather
than being silently trusted or auto-added, per the reviewed security
requirements. This is a real, deliberate asymmetry with the PVE API
client's tls_verify=False: SSH here grants a much more powerful
capability (root-equivalent command execution) than a read-mostly API
token does, so it gets the stricter treatment.
"""

import base64
import hashlib
import io
import json
import os
import re
import socket
from dataclasses import dataclass
from typing import Optional

import paramiko

WRAPPER_PATH = "/usr/local/sbin/pyxie-maint"
SUPPORTED_CONTRACT_VERSION = 1
_ALLOWED_COMMANDS = ("version", "status", "refresh", "plan", "apply", "verify")
_TIMEOUT_BY_COMMAND = {
    "version": 15, "status": 30, "refresh": 120, "plan": 90,
    "apply": 3600, "verify": 30,
}
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\][^\x07]*\x07|[\x00-\x08\x0b\x0c\x0e-\x1f]")


class HostMaintenanceConnectionError(Exception):
    """Network/auth-level failure (host unreachable, key rejected, timeout)."""


class HostKeyMismatchError(HostMaintenanceConnectionError):
    """The host presented a key that does NOT match what's pinned for this
    node -- possible MITM, or a legitimately reinstalled host. Either way
    this never auto-resolves; an operator must explicitly re-probe and
    re-pin after confirming out-of-band that the change is expected."""


class HostKeyNotPinnedError(HostMaintenanceConnectionError):
    """No expected host key is on record for this node yet -- refuses to
    connect rather than trust-on-first-use. Call probe_host_key() and pin
    it explicitly first."""


class HostMaintenanceProtocolError(Exception):
    """The wrapper ran but didn't hold up its end of the contract -- non-zero
    exit or non-JSON output. Never guess at partial success here."""


class MutationsDisabledError(Exception):
    pass


def _mutations_enabled() -> bool:
    return os.environ.get("PVE_MUTATIONS_ENABLED", "false").strip().lower() == "true"


def sanitize_captured_output(text: str, *, max_chars: int = 4000) -> str:
    """Strip ANSI escape/control sequences and cap length before any
    wrapper-captured output (apply's log tail, etc.) is stored in the
    database or shown in the UI/audit log -- raw terminal output can carry
    control sequences that garble a later render or (in a naive terminal)
    do worse than that."""
    if not text:
        return text
    cleaned = _ANSI_ESCAPE_RE.sub("", text)
    return cleaned[:max_chars]


@dataclass
class HostMaintenanceCredentials:
    hostname: str
    port: int
    username: str
    private_key_pem: str
    expected_host_key_type: Optional[str] = None
    expected_host_key_base64: Optional[str] = None


def _load_private_key(pem: str) -> paramiko.PKey:
    last_exc = None
    for key_cls in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
        try:
            return key_cls.from_private_key(io.StringIO(pem))
        except paramiko.SSHException as exc:
            last_exc = exc
    raise HostMaintenanceConnectionError(f"could not parse SSH private key: {last_exc}")


def derive_public_key_line(private_key_pem: str, comment: str = "pyxie-manager-hostmaint") -> str:
    """The OpenSSH authorized_keys-style public key line for a stored
    private key -- used to hand the public half to an admin (for the
    provisioning kit download) without ever exposing the private key
    itself. The private key never needs to leave the server at all."""
    pkey = _load_private_key(private_key_pem)
    return f"{pkey.get_name()} {pkey.get_base64()} {comment}"


def probe_host_key(hostname: str, port: int = 22, timeout: float = 10.0) -> tuple[str, str, str]:
    """Connect at the transport level ONLY -- no authentication attempted
    at all -- and return the server's host key as (key_type, base64,
    sha256_fingerprint). Used exclusively by the explicit, operator-driven
    'pin this node's host key' action; the result is never trusted
    automatically by anything else in this module. The operator is
    expected to confirm the returned fingerprint out-of-band (e.g. against
    `ssh-keygen -lf` run directly on the host's console) before pinning."""
    sock = socket.create_connection((hostname, port), timeout=timeout)
    try:
        transport = paramiko.Transport(sock)
        try:
            transport.start_client(timeout=timeout)
            key = transport.get_remote_server_key()
        finally:
            transport.close()
    except (paramiko.SSHException, socket.error, socket.timeout, EOFError) as exc:
        raise HostMaintenanceConnectionError(str(exc)) from exc
    finally:
        sock.close()
    fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
    return key.get_name(), key.get_base64(), fingerprint


class _PinnedHostKeyPolicy(paramiko.MissingHostKeyPolicy):
    """Used only as a fallback for a key TYPE we didn't pre-load into the
    client's host-key store (e.g. the host actually offers a different key
    algorithm than what's pinned) -- always rejects. A key that matches
    the pinned type is verified by paramiko itself before this is ever
    consulted; a key that matches the type but differs in content raises
    paramiko's own BadHostKeyException automatically, never reaching here
    either."""

    def missing_host_key(self, client, hostname, key):
        raise HostKeyMismatchError(
            f"host at {hostname} offered a {key.get_name()} key that was never pinned for this node "
            f"(expected key type differs or nothing is pinned) -- refusing to connect"
        )


class HostMaintenanceClient:
    def __init__(self, creds: HostMaintenanceCredentials, connect_timeout: float = 15.0):
        self._creds = creds
        self._connect_timeout = connect_timeout
        self._ssh: Optional[paramiko.SSHClient] = None

    def __enter__(self) -> "HostMaintenanceClient":
        if not self._creds.expected_host_key_type or not self._creds.expected_host_key_base64:
            raise HostKeyNotPinnedError(
                f"no SSH host key pinned for {self._creds.hostname} -- probe and pin it explicitly first"
            )
        pkey = _load_private_key(self._creds.private_key_pem)
        client = paramiko.SSHClient()
        # Strict, pinned host-key verification: pre-load ONLY the expected
        # key, then reject anything else outright (see _PinnedHostKeyPolicy
        # and the module docstring). A key that matches what's pre-loaded
        # verifies normally; a key of the same type that has since CHANGED
        # is caught by paramiko itself as BadHostKeyException before our
        # policy class is even consulted.
        host_keys = client.get_host_keys()
        pinned_key = paramiko.PKey.from_type_string(
            self._creds.expected_host_key_type, base64.b64decode(self._creds.expected_host_key_base64)
        )
        host_keys.add(self._creds.hostname, self._creds.expected_host_key_type, pinned_key)
        client.set_missing_host_key_policy(_PinnedHostKeyPolicy())
        try:
            client.connect(
                hostname=self._creds.hostname,
                port=self._creds.port,
                username=self._creds.username,
                pkey=pkey,
                timeout=self._connect_timeout,
                banner_timeout=self._connect_timeout,
                auth_timeout=self._connect_timeout,
                allow_agent=False,
                look_for_keys=False,
            )
        except paramiko.BadHostKeyException as exc:
            raise HostKeyMismatchError(
                f"host key for {self._creds.hostname} does NOT match what's pinned -- possible MITM or a "
                f"reinstalled host; re-probe and re-pin explicitly ONLY after confirming this is expected: {exc}"
            ) from exc
        except (paramiko.SSHException, socket.error, socket.timeout, EOFError) as exc:
            raise HostMaintenanceConnectionError(str(exc)) from exc
        self._ssh = client
        return self

    def __exit__(self, *exc):
        if self._ssh:
            self._ssh.close()
        return False

    def _run(self, subcommand: str) -> dict:
        if subcommand not in _ALLOWED_COMMANDS:
            raise ValueError(f"unknown wrapper subcommand {subcommand!r}")
        if self._ssh is None:
            raise HostMaintenanceConnectionError("client used outside a `with` block")

        command = f"sudo {WRAPPER_PATH} {subcommand}"
        run_timeout = _TIMEOUT_BY_COMMAND[subcommand]
        try:
            _stdin, stdout, stderr = self._ssh.exec_command(command, timeout=run_timeout)
            exit_status = stdout.channel.recv_exit_status()
            out = stdout.read().decode(errors="replace")
            err = stderr.read().decode(errors="replace")
        except (paramiko.SSHException, socket.error, socket.timeout) as exc:
            raise HostMaintenanceConnectionError(str(exc)) from exc

        if exit_status != 0:
            raise HostMaintenanceProtocolError(
                f"{WRAPPER_PATH} {subcommand} exited {exit_status}: {(err or out).strip()[:1000]}"
            )
        try:
            return json.loads(out)
        except json.JSONDecodeError as exc:
            raise HostMaintenanceProtocolError(
                f"{WRAPPER_PATH} {subcommand} did not return valid JSON: {out[:500]!r}"
            ) from exc

    def version(self) -> dict:
        return self._run("version")

    def status(self) -> dict:
        return self._run("status")

    def refresh(self) -> dict:
        return self._run("refresh")

    def plan(self) -> dict:
        return self._run("plan")

    def apply(self) -> dict:
        if not _mutations_enabled():
            raise MutationsDisabledError("PVE_MUTATIONS_ENABLED is not 'true' -- refusing to apply host updates")
        return self._run("apply")

    def verify(self) -> dict:
        return self._run("verify")
