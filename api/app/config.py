import os
from pathlib import Path

# Single source of truth for the app version: the git-tracked VERSION file
# at the repo root (mirrored by a matching git tag on release) -- not an
# env var, which would let the running version silently drift from what's
# actually committed. Falls back to APP_VERSION only if the file is ever
# missing (e.g. a stray deployment layout), so startup never hard-fails
# over a version string.
_VERSION_FILE = Path(__file__).resolve().parent.parent.parent / "VERSION"


def _read_version() -> str:
    try:
        return _VERSION_FILE.read_text().strip()
    except OSError:
        return os.environ.get("APP_VERSION", "0.0.0-unknown")


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
    APP_VERSION: str = _read_version()


settings = Settings()
