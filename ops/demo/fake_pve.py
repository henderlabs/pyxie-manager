"""A tiny stand-in for the Proxmox VE API, for the DEMO stack only.

The fictional demo cluster has no real PVE behind it. While it scores destinations, PyXie reads each VM's
config (CPU type, disks) and the cluster's HA rules; this answers those reads with plausible, invented values
so Balance Load can produce a real plan. It answers GET only and holds nothing worth protecting.

    python fake_pve.py 8006     # https on 0.0.0.0:8006 with a throwaway self-signed certificate
"""
import datetime, json, ssl, sys, tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


class H(BaseHTTPRequestHandler):
    def _send(self, data):
        body = json.dumps({"data": data}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path.split("?")[0]
        parts = p.strip("/").split("/")
        if "qemu" in parts and p.endswith("/config"):
            vmid = parts[parts.index("qemu") + 1]
            return self._send({"cpu": "x86-64-v2-AES", "cores": 2, "memory": 4096, "scsi0": f"nas-nfs:vm-{vmid}-disk-0,size=32G"})
        if p.endswith("/version"):
            return self._send({"version": "8.4.1", "release": "8.4"})
        self._send([])

    def log_message(self, *a):
        pass


def self_signed(dirpath):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "demo")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=2))
            .sign(key, hashes.SHA256()))
    with open(f"{dirpath}/k.pem", "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
    with open(f"{dirpath}/c.pem", "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))


if __name__ == "__main__":
    d = tempfile.mkdtemp()
    self_signed(d)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(f"{d}/c.pem", f"{d}/k.pem")
    srv = HTTPServer(("0.0.0.0", int(sys.argv[1]) if len(sys.argv) > 1 else 8006), H)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    srv.serve_forever()
