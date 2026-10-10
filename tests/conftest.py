"""Shared fixtures. Tests need DATABASE_URL pointing at a Postgres 16 server where the
user may create databases; each test session gets its own fresh database with
db/*.sql applied, and drops it afterwards."""
import os
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def db_url():
    admin = os.environ.get("DATABASE_URL")
    if not admin:
        pytest.skip("DATABASE_URL not set")
    name = f"kernel_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'create database "{name}"')
    url = make_conninfo(**{**conninfo_to_dict(admin), "dbname": name})
    with psycopg.connect(url, autocommit=True) as conn:
        for sql in sorted((ROOT / "db").glob("*.sql")):
            conn.execute(sql.read_text())
    old = os.environ["DATABASE_URL"]
    os.environ["DATABASE_URL"] = url
    yield url
    os.environ["DATABASE_URL"] = old
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'drop database "{name}" with (force)')


@pytest.fixture
def conn(db_url):
    with psycopg.connect(db_url, autocommit=True) as c:
        yield c
