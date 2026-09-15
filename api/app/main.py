from contextlib import asynccontextmanager

from fastapi import FastAPI

from pyxie_core.audit import write_audit_event
from pyxie_core.db import SessionLocal

from . import config  # noqa: F401 -- import asserts the Phase 0 safety gate at startup
from .routers import (
    audit,
    auth,
    findings,
    inventory,
    jobs,
    maintenance,
    metrics,
    network,
    notifications,
    operations,
    placement,
    policies,
    protection,
    providers,
    recommendations,
)
from .seed import seed_defaults


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = SessionLocal()
    try:
        seed_defaults(db)
        write_audit_event(
            db,
            event_category="system",
            event_type="application.startup",
            actor="system",
            metadata={
                "version": config.settings.APP_VERSION,
                "pve_mutations_enabled": config.settings.PVE_MUTATIONS_ENABLED,
            },
            severity="warning" if config.settings.PVE_MUTATIONS_ENABLED else "info",
        )
    finally:
        db.close()
    yield


app = FastAPI(title="PyXie Manager API", version=config.settings.APP_VERSION, lifespan=lifespan)

app.include_router(auth.router)
app.include_router(inventory.router)
app.include_router(providers.router)
app.include_router(audit.router)
app.include_router(findings.router)
app.include_router(recommendations.router)
app.include_router(metrics.router)
app.include_router(policies.router)
app.include_router(protection.router)
app.include_router(maintenance.router)
app.include_router(operations.router)
app.include_router(placement.router)
app.include_router(notifications.router)
app.include_router(jobs.router)
app.include_router(network.router)


@app.get("/api/health")
def health():
    return {"status": "ok", "version": config.settings.APP_VERSION, "mutations_enabled": config.settings.PVE_MUTATIONS_ENABLED}
