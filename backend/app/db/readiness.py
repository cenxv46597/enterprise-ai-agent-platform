"""Read-only PostgreSQL connectivity probe, independent of the business pool."""

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from app.core.config import Settings


def database_is_ready() -> bool:
    """Return a public-safe result; never expose configuration or driver errors.

    Timeouts apply to connection attempts and individual statements, not a hard
    wall-clock deadline (for example, DNS and multiple hosts can take longer).
    SELECT 1 does not validate migrations, business tables, or the business pool.
    """
    engine = None
    ready = False
    try:
        database_url = Settings().database_url
        if not database_url:
            return False
        url = make_url(database_url)
        if url.drivername != "postgresql+psycopg":
            return False
        engine = create_engine(
            url,
            poolclass=NullPool,
            hide_parameters=True,
            connect_args={
                "connect_timeout": 3,
                "options": (
                    "-c statement_timeout=2000 -c lock_timeout=1000 "
                    "-c default_transaction_read_only=on"
                ),
            },
        )
        with engine.connect() as connection:
            ready = connection.execute(text("SELECT 1")).scalar_one() == 1
    except Exception:
        # Even configuration/engine creation may fail before a connection exists.
        # No exception text or connection string belongs in this public endpoint.
        ready = False
    finally:
        if engine is not None:
            try:
                engine.dispose()
            except Exception:
                ready = False
    return ready
