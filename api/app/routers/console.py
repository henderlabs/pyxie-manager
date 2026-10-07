"""Embedded VM console: a ticketed websocket proxy to PVE's noVNC websocket.

Flow (see docs/console.md): an admin POSTs /api/workloads/{id}/console; PyXie asks PVE for a
vncproxy session with the dedicated 'console' token and keeps PVE's ticket server-side in Redis
behind a one-time 30 s PyXie ticket. The browser then opens wss://<host>/console-ws/<ticket>
(Caddy sends that path straight here -- the Next.js layer cannot proxy websockets) and this
module pipes bytes to PVE. Everything is re-checked on connect and every 60 s after.
"""

import asyncio
import os
import ssl
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import quote

import redis
import websockets
import websockets.asyncio.client
import websockets.exceptions
from fastapi import APIRouter, Depends, Header, HTTPException, WebSocket
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from pyxie_core import console_tickets as ct
from pyxie_core.audit import write_audit_event
from pyxie_core.credentials import CredentialNotConfigured, load_pve_credentials, resolve_pve_endpoints
from pyxie_core.db import SessionLocal
from pyxie_core.models import (
    AppSettings, Cluster, Node, PveTarget, Session as SessionModel, User, Workload,
)
from pyxie_core.pve_write_client import PveConsoleClient

from ..auth_deps import get_current_user, require_admin
from ..deps import get_db

router = APIRouter(tags=["console"])

_redis = None


def _r():
    global _redis
    if _redis is None:
        _redis = redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    return _redis


def _load_workload_ctx(db: Session, workload_id):
    w = db.query(Workload).filter(Workload.id == workload_id).one_or_none()
    if w is None:
        raise HTTPException(404, "workload not found")
    node = db.query(Node).filter(Node.id == w.node_id).one_or_none()
    cluster = db.query(Cluster).filter(Cluster.id == w.cluster_id).one_or_none()
    target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one_or_none() if cluster else None
    if node is None or target is None:
        raise HTTPException(409, "the node or PVE target for this workload is not known to PyXie")
    return w, node, cluster, target


def _console_creds(db: Session, target: PveTarget):
    """The dedicated 'console' token when one is saved, otherwise the 'maintenance' token
    (which then needs VM.Console added in PVE). Raises CredentialNotConfigured if neither exists."""
    try:
        return load_pve_credentials(db, target, "console")
    except CredentialNotConfigured:
        return load_pve_credentials(db, target, "maintenance")


def _console_enabled(db: Session) -> bool:
    return bool(db.query(AppSettings.console_enabled).filter(AppSettings.id == 1).scalar())


def _pve_console_url(host: str, api_port: int, w: Workload, node: Node) -> str:
    kind = "kvm" if w.type == "vm" else "lxc"
    name = quote(w.name or "")
    return (f"https://{host}:{api_port}/?console={kind}&novnc=1&vmid={w.vmid}"
            f"&vmname={name}&node={quote(node.name)}")


@router.get("/api/workloads/{workload_id}/console-info")
def console_info(workload_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """What the Console tab needs: the 'Open in PVE' link (always available) and whether the
    embedded console can be offered (switch on + 'console' credential saved + admin)."""
    w, node, _cluster, target = _load_workload_ctx(db, workload_id)
    hosts, api_port = resolve_pve_endpoints(db, target)
    embedded = "ready"
    if not user.is_admin:
        embedded = "admin_only"
    elif not _console_enabled(db):
        embedded = "disabled"
    else:
        try:
            _console_creds(db, target)
        except CredentialNotConfigured:
            embedded = "no_credential"
    if w.is_missing:
        embedded = "unavailable"
    elif w.status != "running" and embedded == "ready":
        embedded = "not_running"
    return {
        "kind": w.type, "vmid": w.vmid, "node": node.name, "status": w.status,
        "pve_url": _pve_console_url(hosts[0], api_port, w, node),
        "embedded": embedded,
    }


@router.post("/api/workloads/{workload_id}/console", dependencies=[Depends(require_admin)])
def create_console_ticket(
    workload_id: uuid.UUID,
    user: User = Depends(get_current_user),
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    if not _console_enabled(db):
        raise HTTPException(403, "The console is switched off. An admin can enable it in Settings.")
    w, node, cluster, target = _load_workload_ctx(db, workload_id)
    if w.is_missing or w.status != "running":
        raise HTTPException(409, "The console is only available while the guest is running.")
    try:
        creds = _console_creds(db, target)
    except CredentialNotConfigured:
        raise HTTPException(409, "No 'console' or 'maintenance' credential is saved for this PVE target (Credentials page).")
    if not ct.ticket_rate_ok(_r(), str(user.id)):
        raise HTTPException(429, "Too many console requests. Wait a minute and try again.")

    try:
        with PveConsoleClient(creds) as client:
            proxy = client.vncproxy(node.name, w.vmid, w.type)
    except Exception as exc:  # noqa: BLE001
        write_audit_event(
            db, event_category="console", event_type="console.denied", actor=user.email, actor_type="user",
            workload_id=w.id, node_id=node.id, cluster_id=cluster.id, result="failure", severity="warning",
            error=str(exc)[:300],
        )
        raise HTTPException(502, f"PVE refused the console request: {str(exc)[:200]}")

    ticket = ct.new_ticket()
    ct.store_ticket(_r(), ticket, {
        "user_id": str(user.id),
        "session_id": (authorization or "").removeprefix("Bearer ").strip(),
        "workload_id": str(w.id), "node": node.name, "vmid": w.vmid, "kind": w.type,
        "host": proxy["host"], "api_port": proxy["api_port"], "port": proxy["port"],
        "vncticket": proxy["vncticket"],
    })
    write_audit_event(
        db, event_category="console", event_type="console.requested", actor=user.email, actor_type="user",
        workload_id=w.id, node_id=node.id, cluster_id=cluster.id,
        metadata={"vmid": w.vmid, "kind": w.type, "node": node.name},
    )
    return {
        "ticket": ticket, "password": proxy["password"], "kind": w.type,
        "expires_in": ct.TICKET_TTL_SECONDS, "ws_path": f"/console-ws/{ticket}",
        "max_session_seconds": ct.MAX_SESSION_SECONDS,
    }


def _revalidate(session_id: str, user_id: str, workload_id: str) -> tuple[bool, str, dict]:
    """Everything that must still be true for a console to stay open. Returns (ok, reason, ctx)."""
    db = SessionLocal()
    try:
        s = db.query(SessionModel).filter(SessionModel.id == session_id).one_or_none()
        if s is None or s.expires_at < datetime.now(timezone.utc):
            return False, "session ended", {}
        user = db.query(User).filter(User.id == s.user_id, User.is_active.is_(True)).one_or_none()
        if user is None or str(user.id) != user_id or not user.is_admin:
            return False, "not authorised", {}
        if not _console_enabled(db):
            return False, "console switched off", {}
        w = db.query(Workload).filter(Workload.id == workload_id).one_or_none()
        if w is None or w.is_missing or w.status != "running":
            return False, "guest is not running", {}
        node = db.query(Node).filter(Node.id == w.node_id).one_or_none()
        cluster = db.query(Cluster).filter(Cluster.id == w.cluster_id).one_or_none()
        target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one_or_none()
        creds = _console_creds(db, target)
        return True, "", {
            "email": user.email, "workload_id": w.id, "node_id": node.id, "cluster_id": cluster.id,
            "auth": f"PVEAPIToken={creds.token_user}!{creds.token_id}={creds.token_secret}",
            "tls_verify": target.tls_verify,
        }
    except CredentialNotConfigured:
        return False, "console credential removed", {}
    finally:
        db.close()


def _audit(**kw):
    db = SessionLocal()
    try:
        write_audit_event(db, event_category="console", actor_type="user", **kw)
    finally:
        db.close()


@router.websocket("/console-ws/{ticket}")
async def console_ws(websocket: WebSocket, ticket: str):
    if not ct.origin_allowed(websocket.headers.get("origin"), websocket.headers.get("host")):
        await websocket.close(code=1008)
        return
    payload = await run_in_threadpool(ct.consume_ticket, _r(), ticket)
    if payload is None:
        await websocket.close(code=1008)
        return
    ok, reason, ctx = await run_in_threadpool(
        _revalidate, payload["session_id"], payload["user_id"], payload["workload_id"])
    if not ok:
        await websocket.close(code=1008, reason=reason)
        return
    conn_id = uuid.uuid4().hex
    got_slot = await run_in_threadpool(ct.acquire_slot, _r(), payload["user_id"], conn_id)
    if not got_slot:
        await websocket.close(code=1013, reason="too many open consoles")
        return

    started = time.monotonic()
    sent = {"to_pve": 0, "to_browser": 0}
    end_reason = "closed"
    audit_common = dict(
        actor=ctx["email"], workload_id=ctx["workload_id"], node_id=ctx["node_id"], cluster_id=ctx["cluster_id"],
    )
    try:
        url = (f"wss://{payload['host']}:{payload['api_port']}/api2/json/nodes/{quote(payload['node'])}/"
               f"{ct.pve_kind_path(payload['kind'])}/{int(payload['vmid'])}/vncwebsocket"
               f"?port={int(payload['port'])}&vncticket={quote(payload['vncticket'], safe='')}")
        sslctx = ssl.create_default_context()
        if not ctx["tls_verify"]:
            sslctx.check_hostname = False
            sslctx.verify_mode = ssl.CERT_NONE
        try:
            upstream = await websockets.asyncio.client.connect(
                url, additional_headers={"Authorization": ctx["auth"]}, subprotocols=["binary"],
                ssl=sslctx, max_size=ct.MAX_CLIENT_MESSAGE_BYTES * 4, open_timeout=10, ping_interval=None,
            )
        except Exception as exc:  # noqa: BLE001
            end_reason = f"pve connect failed: {str(exc)[:120]}"
            await websocket.close(code=1011, reason="could not reach the console on PVE")
            return
        offered = websocket.scope.get("subprotocols") or []
        await websocket.accept(subprotocol="binary" if "binary" in offered else None)
        await run_in_threadpool(
            lambda: _audit(event_type="console.opened", metadata={"vmid": payload["vmid"], "kind": payload["kind"], "node": payload["node"]}, **audit_common))

        async def browser_to_pve():
            while True:
                msg = await websocket.receive()
                if msg["type"] == "websocket.disconnect":
                    return "browser closed"
                data = msg.get("bytes") if msg.get("bytes") is not None else msg.get("text")
                if data is None:
                    continue
                if len(data) > ct.MAX_CLIENT_MESSAGE_BYTES:
                    return "message too large"
                sent["to_pve"] += len(data)
                await upstream.send(data)

        async def pve_to_browser():
            try:
                async for data in upstream:
                    sent["to_browser"] += len(data)
                    if isinstance(data, bytes):
                        await websocket.send_bytes(data)
                    else:
                        await websocket.send_text(data)
            except websockets.exceptions.ConnectionClosed:
                pass
            return "pve closed"

        async def watchdog():
            while True:
                await asyncio.sleep(60)
                if time.monotonic() - started > ct.MAX_SESSION_SECONDS:
                    return "time limit reached"
                good, why, _ = await run_in_threadpool(
                    _revalidate, payload["session_id"], payload["user_id"], payload["workload_id"])
                if not good:
                    return why

        tasks = [asyncio.create_task(f()) for f in (browser_to_pve, pve_to_browser, watchdog)]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for t in done:
            exc = t.exception()
            end_reason = str(t.result()) if exc is None else f"error: {type(exc).__name__}"
        await upstream.close()
        try:
            await websocket.close(code=1000)
        except Exception:  # noqa: BLE001
            pass
    finally:
        await run_in_threadpool(ct.release_slot, _r(), payload["user_id"], conn_id)
        await run_in_threadpool(
            lambda: _audit(event_type="console.closed", metadata={
                "vmid": payload["vmid"], "node": payload["node"], "reason": end_reason,
                "seconds": int(time.monotonic() - started), "bytes_to_pve": sent["to_pve"],
                "bytes_to_browser": sent["to_browser"]}, **audit_common))
