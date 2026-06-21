"""
Shared pytest fixtures.

Unit tests (type inference, classification) need nothing but the `src`
modules and run anywhere. Integration tests (incremental Gold loads) need a
real reachable PostgreSQL instance - they use the exact same `get_engine()`
the pipeline itself uses (PG_* env vars / .env), and skip themselves
automatically if no database is reachable instead of failing the whole suite.
"""
import uuid

import pytest
from sqlalchemy import text

from common import get_engine


@pytest.fixture
def pg_engine():
    engine = get_engine()
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no reachable PostgreSQL instance for integration tests: {exc}")
    yield engine
    engine.dispose()


@pytest.fixture
def unique_suffix():
    return uuid.uuid4().hex[:8]


@pytest.fixture
def cleanup_tables(pg_engine):
    """Yields a list the test appends created table names to; drops them
    all (if they exist) after the test, pass or fail."""
    created = []
    yield created
    with pg_engine.begin() as conn:
        for name in created:
            conn.execute(text(f'DROP TABLE IF EXISTS "{name}"'))
