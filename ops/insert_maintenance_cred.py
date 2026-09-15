"""Run INSIDE the pyxie-manager-api container: reads the new 'maintenance'
PVE token secret from stdin (never from argv, never logged) and stores it
Fernet-encrypted in pve_credentials, matching how every other credential in
this app is stored. Prints only non-secret confirmation to stdout.
"""
import sys

from pyxie_core.audit import write_audit_event
from pyxie_core.crypto import encrypt_secret, mask_secret
from pyxie_core.db import SessionLocal
from pyxie_core.models import PveCredential, PveTarget

secret = sys.stdin.read().strip()
if not secret:
    print("ERROR: no secret received on stdin", file=sys.stderr)
    sys.exit(1)

db = SessionLocal()
try:
    target = db.query(PveTarget).one()  # exactly one PVE target configured in this deployment

    existing = (
        db.query(PveCredential)
        .filter(PveCredential.pve_target_id == target.id, PveCredential.slot_name == "maintenance")
        .one_or_none()
    )
    if existing:
        existing.encrypted_secret = encrypt_secret(secret)
        existing.token_user = "pyxie-manager@pve"
        existing.token_id = "maintenance"
        existing.status = "untested"
        existing.created_by = "w0-setup"
        cred = existing
        action = "updated"
    else:
        cred = PveCredential(
            pve_target_id=target.id,
            slot_name="maintenance",
            token_user="pyxie-manager@pve",
            token_id="maintenance",
            encrypted_secret=encrypt_secret(secret),
            status="untested",
            created_by="w0-setup",
        )
        db.add(cred)
        action = "created"

    write_audit_event(
        db,
        event_category="credential",
        event_type=f"credential.{action}",
        actor="w0-setup",
        actor_type="system",
        site_id=target.site_id,
        provider_id=target.provider_id,
        metadata={"slot_name": "maintenance", "token_user": "pyxie-manager@pve", "masked_secret": mask_secret(secret)},
    )
    db.commit()
    print(f"OK: maintenance credential {action} for target {target.name!r} (masked={mask_secret(secret)})")
finally:
    db.close()
