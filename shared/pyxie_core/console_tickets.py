"""One-time tickets, limits and origin checks for the embedded VM console.

The browser never receives PVE's own vnc ticket. PyXie keeps it in Redis under a hash of a
random one-time ticket (30 s, consumed on first use) that is bound to the requesting user and
workload. Pure functions over a small redis-like object (set/getdel/zadd/zremrangebyscore/
zcard/zrem/incr/expire) so they are unit-testable without Redis.
"""

import hashlib
import json
import secrets
import time
from typing import Optional
from urllib.parse import urlsplit

TICKET_TTL_SECONDS = 30
TICKETS_PER_MINUTE = 10
MAX_CONSOLES_PER_USER = 2
MAX_CONSOLES_TOTAL = 10
MAX_SESSION_SECONDS = 4 * 3600
MAX_CLIENT_MESSAGE_BYTES = 1024 * 1024


def new_ticket() -> str:
    return secrets.token_urlsafe(32)


def _ticket_key(ticket: str) -> str:
    return "pyxie:console:ticket:" + hashlib.sha256(ticket.encode()).hexdigest()


def store_ticket(r, ticket: str, payload: dict) -> None:
    r.set(_ticket_key(ticket), json.dumps(payload), ex=TICKET_TTL_SECONDS)


def consume_ticket(r, ticket: str) -> Optional[dict]:
    """Atomic get-and-delete: a ticket works once, a replay finds nothing."""
    if not ticket or len(ticket) > 128:
        return None
    raw = r.getdel(_ticket_key(ticket))
    if not raw:
        return None
    return json.loads(raw)


def ticket_rate_ok(r, user_id: str, limit: int = TICKETS_PER_MINUTE) -> bool:
    key = f"pyxie:console:rate:{user_id}"
    n = r.incr(key)
    if n == 1:
        r.expire(key, 60)
    return n <= limit


_ACTIVE_ALL = "pyxie:console:active"


def _user_key(user_id: str) -> str:
    return f"pyxie:console:active:{user_id}"


def acquire_slot(r, user_id: str, conn_id: str, *, now: Optional[float] = None,
                 per_user: int = MAX_CONSOLES_PER_USER, total: int = MAX_CONSOLES_TOTAL) -> bool:
    """Concurrency limit. Slots carry an expiry score slightly past the hard session cap, so a
    crashed API process cannot leak a slot forever."""
    now = time.time() if now is None else now
    for key in (_ACTIVE_ALL, _user_key(user_id)):
        r.zremrangebyscore(key, "-inf", now)
    if r.zcard(_user_key(user_id)) >= per_user or r.zcard(_ACTIVE_ALL) >= total:
        return False
    expiry = now + MAX_SESSION_SECONDS + 300
    r.zadd(_ACTIVE_ALL, {conn_id: expiry})
    r.zadd(_user_key(user_id), {conn_id: expiry})
    return True


def release_slot(r, user_id: str, conn_id: str) -> None:
    r.zrem(_ACTIVE_ALL, conn_id)
    r.zrem(_user_key(user_id), conn_id)


def origin_allowed(origin: Optional[str], host_header: Optional[str], extra_hosts=()) -> bool:
    """Cross-site websocket hijacking guard: the upgrade must come from a page served by this
    very host. A missing Origin is refused (browsers always send it on websocket upgrades)."""
    if not origin or not host_header:
        return False
    try:
        netloc = urlsplit(origin).netloc.lower()
    except ValueError:
        return False
    if not netloc:
        return False
    allowed = {host_header.lower(), *[h.lower() for h in extra_hosts if h]}
    return netloc in allowed


def pve_kind_path(kind: str) -> str:
    return "qemu" if kind == "vm" else "lxc"
