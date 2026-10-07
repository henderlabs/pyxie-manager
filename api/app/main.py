from contextlib import asynccontextmanager

from fastapi import FastAPI

from pyxie_core.audit import write_audit_event
from pyxie_core.db import SessionLocal
from pyxie_core.request_context import current_client_ip
from pyxie_core.models import AppSettings

from . import config  # noqa: F401 -- import asserts the Phase 0 safety gate at startup
from .routers import (
    audit,
    console,
    host_kit,
    setup,
    feedback,
    auth,
    findings,
    inventory,
    jobs,
    maintenance,
    metrics,
    network,
    notification_rules,
    notifications,
    operations,
    placement,
    policies,
    protection,
    providers,
    recommendations,
    node_detail,
    reports,
    system_updates,
    workload_detail,
)
from .seed import seed_defaults


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = SessionLocal()
    try:
        seed_defaults(db)
        mutations_enabled = bool(db.query(AppSettings.pve_mutations_enabled).filter(AppSettings.id == 1).scalar())
        write_audit_event(
            db,
            event_category="system",
            event_type="application.startup",
            actor="system",
            metadata={
                "version": config.settings.APP_VERSION,
                "pve_mutations_enabled": mutations_enabled,
            },
            severity="warning" if mutations_enabled else "info",
        )
    finally:
        db.close()
    yield


app = FastAPI(title="PyXie Manager API", version=config.settings.APP_VERSION, lifespan=lifespan)


class ClientIpMiddleware:
    """Pure ASGI middleware: publish the request's client address (already
    rewritten from X-Forwarded-For by uvicorn when the peer is trusted, see
    FORWARDED_ALLOW_IPS in compose.yaml) so write_audit_event can record it."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        token = current_client_ip.set(client[0] if client else None)
        try:
            await self.app(scope, receive, send)
        finally:
            current_client_ip.reset(token)


app.add_middleware(ClientIpMiddleware)

app.include_router(auth.router)
app.include_router(inventory.router)
app.include_router(providers.router)
app.include_router(audit.router)
app.include_router(findings.router)
app.include_router(recommendations.router)
app.include_router(reports.router)
app.include_router(metrics.router)
app.include_router(policies.router)
app.include_router(protection.router)
app.include_router(maintenance.router)
app.include_router(operations.router)
app.include_router(placement.router)
app.include_router(notifications.router)
app.include_router(notification_rules.router)
app.include_router(jobs.router)
app.include_router(network.router)
app.include_router(system_updates.router)
app.include_router(node_detail.router)
app.include_router(workload_detail.router)
app.include_router(console.router)
app.include_router(host_kit.admin_router)
app.include_router(host_kit.public_router)
app.include_router(setup.router)
app.include_router(feedback.router)


@app.get("/api/health")
def health():
    db = SessionLocal()
    try:
        mutations_enabled = bool(db.query(AppSettings.pve_mutations_enabled).filter(AppSettings.id == 1).scalar())
    finally:
        db.close()
    return {"status": "ok", "version": config.settings.APP_VERSION, "mutations_enabled": mutations_enabled}
