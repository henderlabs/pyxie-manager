import os


class Settings:
    # Global kill switch for the write-capability engine (Stage W0+). Default
    # is false -- turning this on is a deliberate, explicit decision, not a
    # side effect of deploying new code. Every write path (see
    # shared/pyxie_core/pve_write_client.py) re-checks this at call time, not
    # just here at startup, so it stays effective even if some future code
    # path builds a write client without going through the operations engine.
    # Approving an individual operation in the UI is a SEPARATE gate from
    # this -- both must be true for any PVE mutation to actually happen.
    PVE_MUTATIONS_ENABLED: bool = os.environ.get("PVE_MUTATIONS_ENABLED", "false").lower() == "true"
    APP_VERSION: str = os.environ.get("APP_VERSION", "0.1.0-w0")


settings = Settings()
