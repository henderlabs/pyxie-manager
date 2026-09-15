import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = os.environ["DATABASE_URL"]

engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
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
