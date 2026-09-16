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
    # The PVE write kill switch used to live here as an env var -- it's now
    # a Settings-page toggle backed by app_settings.pve_mutations_enabled
    # (see shared/pyxie_core/pve_write_client.py's _mutations_enabled(),
    # which queries it fresh on every write call). Nothing here reads
    # PVE_MUTATIONS_ENABLED from the environment anymore.
    APP_VERSION: str = _read_version()


settings = Settings()
