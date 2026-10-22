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
        c.execute("DROP TABLE IF EXISTS runs, projects, saved_filters")
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


@pytest.fixture
def openai_stub():
    """An OpenAI-compatible chat completions server with canned replies: a tool call when tools are
    offered and no tool result came back yet, else a short answer. No real model, no key."""
    import json
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            req = json.loads(self.rfile.read(int(self.headers["content-length"])))
            msgs, tools = req.get("messages") or [], req.get("tools")
            if tools and msgs[-1].get("role") != "tool":
                msg = {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1", "type": "function",
                       "function": {"name": tools[0]["function"]["name"], "arguments": '{"city": "Pune"}'}}]}
            else:
                msg = {"role": "assistant", "content": "It is sunny in Pune."}
            body = json.dumps({"id": "c1", "object": "chat.completion", "created": 1, "model": req["model"],
                               "choices": [{"index": 0, "message": msg, "finish_reason": "stop"}],
                               "usage": {"prompt_tokens": 11, "completion_tokens": 5, "total_tokens": 16}}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/v1"
    server.shutdown()
