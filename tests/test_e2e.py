"""Real LangGraph agent + the real LangSmith SDK in OpenTelemetry mode (settings only, no code
change) -> a running lighttrace's one receiver -> UI JSON API. Runs examples/demo_app.py."""

import os
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from conftest import API_KEY, PASSWORD

DEMO = Path(__file__).parent.parent / "examples" / "demo_app.py"


def test_langgraph_trace_end_to_end(live_server):
    url, app = live_server
    env = {**os.environ, "LANGSMITH_TRACING": "true", "LANGSMITH_OTEL_ENABLED": "true", "LANGSMITH_OTEL_ONLY": "true",
           # a dead LangSmith address: in OTel-only mode nothing may be sent there
           "LANGSMITH_ENDPOINT": "http://127.0.0.1:9", "LANGSMITH_API_KEY": "unused",
           "OTEL_EXPORTER_OTLP_ENDPOINT": url, "OTEL_EXPORTER_OTLP_HEADERS": f"x-api-key={API_KEY}",
           "OTEL_SERVICE_NAME": "e2e-proj"}
    r = subprocess.run([sys.executable, str(DEMO)], env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-3000:]
    assert "127.0.0.1:9" not in r.stderr and "Connection refused" not in r.stderr  # no LangSmith traffic

    ui = TestClient(app, headers={"x-lighttrace": "1"})
    assert ui.get("/api/projects").status_code == 401
    ui.post("/api/login", json={"password": PASSWORD})
    [project] = ui.get("/api/projects").json()
    assert (project["name"], project["trace_count"], project["stats"]["threads"]) == ("e2e-proj", 6, 3)

    traces = ui.get("/api/projects/e2e-proj/runs", params={"scope": "traces"}).json()["items"]
    assert {t["name"] for t in traces} == {"LangGraph"}
    first = next(t for t in traces if t["input_preview"] == "How many M8 bolts do we have?")
    assert first["status"] == "success" and first["thread_id"] == "thread-a" and first["tags"] == ["chat"]
    assert first["output_preview"] == "You have 42 M8 bolts on hand (5 reserved)." and first["total_tokens"] == 342
    failed = next(t for t in traces if t["thread_id"] == "thread-b")
    assert failed["status"] == "error" and "ERP API timeout" in failed["error"]

    # metadata filter, runs scope, threads list and the thread itself
    assert len(ui.get("/api/projects/e2e-proj/runs", params={"meta": "user_id:alice"}).json()["items"]) == 3
    tools = ui.get("/api/projects/e2e-proj/runs", params={"scope": "runs", "run_type": "tool"}).json()["items"]
    assert {t["name"] for t in tools} == {"get_stock", "create_purchase_order"}
    threads = {t["thread_id"]: t for t in ui.get("/api/projects/e2e-proj/threads").json()["items"]}
    assert threads["thread-a"]["turns"] == 3 and threads["thread-b"]["errors"] == 1
    thread = ui.get("/api/projects/e2e-proj/thread", params={"id": "thread-a"}).json()
    assert thread["stats"]["turns"] == 3 and len(thread["turns"][0]["llm_runs"]) == 2

    runs = ui.get(f"/api/traces/{first['id']}").json()["runs"]
    by_id = {r["id"]: r for r in runs}
    root = runs[0]
    assert root["is_root"] == 1 and root["id"] == first["id"] and all(r["project"] == "e2e-proj" for r in runs)
    llms = [r for r in runs if r["run_type"] == "llm"]
    assert sorted(r["total_tokens"] for r in llms) == [138, 204]
    assert any("42" in str(r["outputs"]) for r in runs if r["name"] == "get_stock")
    for r in runs:  # every child's parent chain leads to the root
        while r["parent_run_id"]:
            r = by_id[r["parent_run_id"]]
        assert r is root
