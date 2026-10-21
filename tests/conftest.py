import os
import socket
import threading
import time

import pytest
import uvicorn
from fastapi.testclient import TestClient

from lighttrace.app import Settings, create_app

API_KEY = "test-key"
PASSWORD = "test-pw"
# Postgres tests run when this points at a throwaway database (its tables are dropped per test).
PG_URL = os.environ.get("LIGHTTRACE_TEST_PG")


@pytest.fixture(params=["sqlite", "postgres"])
def db_target(request, tmp_path):
    if request.param == "sqlite":
        return str(tmp_path / "t.db")
    if not PG_URL:
        pytest.skip("set LIGHTTRACE_TEST_PG=postgresql://... to run Postgres tests")
    import psycopg

    with psycopg.connect(PG_URL, autocommit=True) as c:
        c.execute("DROP TABLE IF EXISTS runs, projects, saved_filters, api_keys")
    return PG_URL


def make_settings(target, **kw):
    kw.setdefault("retention_days", 0)
    return Settings(db_path=target, api_keys=[API_KEY], password=PASSWORD, **kw)


def ui_client(app):
    c = TestClient(app, headers={"x-lighttrace": "1"})
    assert c.post("/api/login", json={"password": PASSWORD}).status_code == 200
    return c


@pytest.fixture
def client(db_target):
    return ui_client(create_app(make_settings(db_target, prices={"fake-model": [1.0, 2.0]})))


@pytest.fixture
def live_server(db_target):
    """A real uvicorn server on a free port, for the real langsmith SDK to talk to."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    app = create_app(make_settings(db_target))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    while not server.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", app
    server.should_exit = True
    t.join(5)
