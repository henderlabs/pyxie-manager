"""Test-only environment setup, loaded by pytest before any test module in
this directory is collected/imported.

pyxie_core.db / pyxie_core.crypto read DATABASE_URL / PYXIE_CREDENTIAL_KEY
at MODULE IMPORT time, so importing pyxie_core.maintenance or
pyxie_core.protection_workflow (both needed by these regression tests)
fails outright without them present -- even though the tests themselves
never touch a real database or decrypt a real secret.

setdefault() only fills these in if unset, so running the real app (which
already has real values in .env) is completely unaffected. The values
below are throwaway and deliberately never point at anything real:
DATABASE_URL is never actually connected to (SQLAlchemy's create_engine()
is lazy -- no connection is attempted until a query runs, and every test
here mocks out anything that would run one), and the Fernet key just has
to be validly formatted, never decrypts anything real.
"""

import os

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/pyxie_manager_test_unused")
os.environ.setdefault("PYXIE_CREDENTIAL_KEY", "xI1M3T720DZ_j34i6ytHFMGxsJ5gkiMZbqAlkUHOiUs=")
os.environ.setdefault("PVE_MUTATIONS_ENABLED", "false")
