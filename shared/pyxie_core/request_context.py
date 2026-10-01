"""Request-scoped context shared by the API and the audit writer.

The API sets ``current_client_ip`` per request (see ClientIpMiddleware in
api/app/main.py); background jobs in the worker never set it, so system events
carry no client IP.
"""

from contextvars import ContextVar
from typing import Optional

current_client_ip: ContextVar[Optional[str]] = ContextVar("current_client_ip", default=None)
