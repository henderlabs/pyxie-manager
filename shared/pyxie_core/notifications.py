"""Alert email delivery for findings-driven notifications.

The in-app Notification rows are written by findings.py in the same
transaction as the finding itself. Email is deliberately a separate,
best-effort step that runs AFTER that transaction commits: a slow or broken
mail server must never hold a DB transaction open or stop findings from
being recorded. Every failure in dispatch_event is logged and swallowed.

Who gets emailed, and for what, is decided by NotificationRule rows
(Settings > Notifications). With no enabled rule matching an event, nothing
is emailed -- the in-app notification is still recorded.
"""

import logging
import re

from sqlalchemy.orm import Session

from .mail import send_email
from .models import AppSettings, NotificationRule, User

log = logging.getLogger(__name__)

# The event categories findings.py can raise, with operator-facing wording.
# The keys are the Finding.category values; keep in sync with findings.py.
CATEGORIES = [
    {"key": "quorum", "label": "Cluster quorum", "description": "A cluster has lost quorum."},
    {"key": "node", "label": "Node health", "description": "A node is offline or its state is unknown / not connected."},
    {"key": "update", "label": "Pending updates", "description": "A node has package updates waiting."},
    {"key": "version", "label": "Version drift", "description": "Nodes in a cluster run different PVE versions."},
    {"key": "storage", "label": "Storage", "description": "A storage pool is unavailable or nearly full."},
    {"key": "capacity", "label": "Capacity", "description": "A node has more memory allocated to workloads than it physically has."},
    {"key": "task", "label": "Failed tasks", "description": "The latest run of a PVE task failed."},
    {"key": "protection", "label": "Backup protection", "description": "A workload has no confirmed backup protection."},
    {"key": "placement", "label": "Placement & affinity", "description": "An affinity rule is violated."},
    {"key": "balance", "label": "Automatic balancing", "description": "Automatic balancing has prepared a Balance Load plan that is waiting for review. Use a rule with minimum severity Warning to get these by email."},
    {"key": "liveness", "label": "VM not responding", "description": "A running VM's QEMU has stopped answering: PVE still shows it as running, but live migration of it will hang."},
    {"key": "connectivity", "label": "Proxmox connectivity", "description": "PyXie cannot reach a Proxmox target, or has had to fall back to another cluster member."},
]
CATEGORY_KEYS = {c["key"] for c in CATEGORIES}
SEVERITY_RANK = {"warning": 1, "critical": 2}

_EMAIL_RE = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")


def is_valid_email(address: str) -> bool:
    return bool(_EMAIL_RE.match(address))


def resolve_recipients(db: Session, rule: NotificationRule) -> list[str]:
    """The rule's explicit addresses, plus every active admin if asked to,
    de-duplicated case-insensitively while keeping first-seen order."""
    addresses = list(rule.recipients or [])
    if rule.include_admins:
        admins = db.query(User.email).filter(User.is_admin.is_(True), User.is_active.is_(True)).all()
        addresses.extend(a for (a,) in admins)
    seen: set[str] = set()
    out: list[str] = []
    for a in addresses:
        key = a.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(a.strip())
    return out


def rule_matches(rule: NotificationRule, *, category: str, severity: str, recovered: bool) -> bool:
    """severity is the severity of the underlying finding (for a recovery,
    the severity it had before clearing)."""
    if not rule.enabled:
        return False
    if rule.categories and category not in rule.categories:
        return False
    if SEVERITY_RANK.get(severity, 0) < SEVERITY_RANK.get(rule.min_severity, 2):
        return False
    if recovered and not rule.send_recovery:
        return False
    return True


def dispatch_event(db: Session, *, severity: str, category: str, title: str, recovered: bool, observed_at) -> int:
    """Email everyone whose enabled rules match this event. Returns the
    number of messages sent. Never raises."""
    try:
        settings = db.query(AppSettings).filter(AppSettings.id == 1).one_or_none()
        if settings is None or not settings.smtp_enabled:
            return 0
        rules = db.query(NotificationRule).filter(NotificationRule.enabled.is_(True)).all()
        recipients: list[str] = []
        seen: set[str] = set()
        for rule in rules:
            if not rule_matches(rule, category=category, severity=severity, recovered=recovered):
                continue
            for address in resolve_recipients(db, rule):
                if address.lower() not in seen:
                    seen.add(address.lower())
                    recipients.append(address)
        if not recipients:
            return 0
        label = "RECOVERED" if recovered else severity.upper()
        subject = f"[PyXie] {label}: {title}"
        body = f"{title}\n\nseverity: {severity}\ncategory: {category}\nobserved: {observed_at.isoformat()}\n"
        sent = 0
        for address in recipients:
            try:
                send_email(settings, address, subject, body)
                sent += 1
            except Exception as e:  # noqa: BLE001 -- one bad address must not block the rest
                log.warning("alert email to %s failed: %s", address, e)
        return sent
    except Exception as e:  # noqa: BLE001
        log.warning("alert email skipped: %s", e)
        return 0
