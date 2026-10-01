import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = os.environ["DATABASE_URL"]

# The API runs every sync request in one of anyio's 40 worker threads. With
# SQLAlchemy's default pool (5 + 10 overflow = 15) more than 15 concurrent
# requests make the excess threads block on pool checkout (30s). The finished
# requests then cannot run their get_db() teardown (db.close()) because that
# also needs a worker thread, so their connections sit "idle in transaction"
# and the pool stays full while the UI keeps polling -- a self-sustaining
# stall (2026-10-01). Keep pool + overflow above the thread count so a request
# thread can always get a connection; connections are opened lazily, so the
# larger ceiling costs nothing at idle.
DB_POOL_SIZE = int(os.environ.get("DB_POOL_SIZE", "20"))
DB_MAX_OVERFLOW = int(os.environ.get("DB_MAX_OVERFLOW", "30"))
DB_POOL_TIMEOUT = int(os.environ.get("DB_POOL_TIMEOUT", "15"))

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=DB_POOL_SIZE,
    max_overflow=DB_MAX_OVERFLOW,
    pool_timeout=DB_POOL_TIMEOUT,
    future=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()

# RQ's default Worker forks a child process per job. A forked child inherits
# the parent's already-open pooled connections, which are not fork-safe --
# both processes end up sharing the same underlying TCP socket to Postgres.
# Short jobs can get lucky; a long-running job (a multi-minute migration or
# reboot monitoring loop with many periodic commits) reliably races on it,
# silently failing to persist updates with no exception raised. Disposing
# the pool in the child immediately after fork forces it to open fresh
# connections instead of reusing inherited ones.
if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=engine.dispose)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
