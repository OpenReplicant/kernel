"""Fixtures for the kernel tests. Needs the services from `tools/dev.sh up` started with a test
graph name (the tests drop and recreate that graph and the kernel schema):

    KERNEL_GRAPH=test_kernel tools/dev.sh up
    DATABASE_URL=postgresql://agens@127.0.0.1:55432/postgres KERNEL_GRAPH=test_kernel pytest
"""
import os
import shutil
import threading
import time
from pathlib import Path

import psycopg
import pytest

from kernel import bridge as kbridge
from kernel import db, modules
from kernel.rulego import RuleGo

TESTS = Path(__file__).resolve().parent
NOTES = TESTS / "modules" / "notes"

# tests that call the entity's real model, Haiku through `claude -p`
needs_claude = pytest.mark.skipif(shutil.which("claude") is None, reason="needs `claude` logged in (Haiku)")


def pytest_configure(config):
    graph = os.environ.get("KERNEL_GRAPH", "")
    if not graph.startswith("test"):
        pytest.exit("set KERNEL_GRAPH to a name starting with 'test' (the tests drop that graph)", 2)
    if "DATABASE_URL" not in os.environ:
        pytest.exit("set DATABASE_URL (tools/dev.sh up prints it)", 2)


@pytest.fixture(scope="session")
def rulego():
    r = RuleGo()
    if not r.up():
        pytest.exit(f"RuleGo-Server is not answering at {r.url}: run tools/dev.sh up", 2)
    return r


@pytest.fixture(scope="session")
def fresh_core():
    """A fresh graph and kernel schema, the core installed."""
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
        c.execute("DROP EVENT TRIGGER IF EXISTS kernel_refuse_undeclared")
        c.execute(f"DROP GRAPH IF EXISTS {db.graph_name()} CASCADE")
        c.execute("DROP SCHEMA IF EXISTS kernel CASCADE")
    with db.connect() as conn:
        db.install_core(conn)


@pytest.fixture
def conn(fresh_core):
    with db.connect() as c:
        yield c


@pytest.fixture(scope="session")
def notes(fresh_core, rulego):
    """The notes test module, installed."""
    with db.connect() as c:
        return modules.install(NOTES, c, rulego)


@pytest.fixture
def limits(conn):
    """Set kernel.config limits for one test, restoring them afterwards."""
    saved = dict(conn.execute("SELECT key, value FROM kernel.config").fetchall())
    conn.commit()

    def set_(**kw):
        for k, v in kw.items():
            conn.execute("UPDATE kernel.config SET value = %s::jsonb WHERE key = %s", (str(v), k))
        conn.commit()

    yield set_
    for k, v in saved.items():
        conn.execute("UPDATE kernel.config SET value = %s::jsonb WHERE key = %s", (str(v), k))
    conn.commit()


@pytest.fixture
def bridge(fresh_core, rulego):
    """Start the bridge in a thread; stopped after the test."""
    threads = []

    def start():
        stop = threading.Event()
        t = threading.Thread(target=kbridge.run, kwargs={"poll": 0.3, "stop": stop, "rulego": rulego}, daemon=True)
        t.start()
        threads.append((t, stop))
        return stop

    yield start
    for t, stop in threads:
        stop.set()
        t.join(5)


def wait_for(pred, timeout=20.0, every=0.1):
    end = time.time() + timeout
    while time.time() < end:
        result = pred()
        if result:
            return result
        time.sleep(every)
    return pred()
