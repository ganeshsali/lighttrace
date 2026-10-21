"""Agent frameworks and OTel dialects. Fixtures in tests/fixtures are real exporter payloads
(captured from the SDK named in the test, protobuf converted to OTLP/JSON) unless a test says otherwise."""
import json
import uuid
from pathlib import Path

import pytest

from conftest import API_KEY
from test_otel import TRACE, kv, span

FIX = Path(__file__).parent / "fixtures"
H = {"x-api-key": API_KEY, "x-lighttrace-project": "proj"}


def post(client, body, path="/v1/traces"):
    r = client.post(path, json=body, headers=H)
    assert r.status_code == 200, r.text


def fixture(name):
    return json.loads((FIX / name).read_text())


def only_trace(client):
    [t] = client.get("/api/projects/proj/runs", params={"scope": "traces"}).json()["items"]
    return t, client.get(f"/api/traces/{t['id']}").json()["runs"]


def resource(service, spans):
    return {"resource": {"attributes": [kv("service.name", service)]}, "scopeSpans": [{"spans": spans}]}


def test_remote_parent_becomes_root_per_service(client):
    """Agent A calls server B twice in one trace (A2A / out-of-process MCP). B's spans have a remote parent."""
    a_root, a_call1, a_call2 = "a000000000000001", "a000000000000002", "a000000000000003"
    remote = {"flags": 0x301}
    b_spans = [
        span("b000000000000011", "b000000000000001", "db lookup", 120, 150, {}),  # local child of entry 1
        span("b000000000000001", a_call1, "tools/call search", 100, 200, {"input.value": "q1"}, **remote),
        span("b000000000000002", a_call2, "tools/call search", 300, 450, {}, **remote),
    ]
    post(client, {"resourceSpans": [resource("server-b", b_spans)]})  # B's spans arrive first
    post(client, {"resourceSpans": [resource("agent-a", [
        span(a_call1, a_root, "call", 90, 210, {}), span(a_call2, a_root, "call", 290, 460, {}),
        span(a_root, "", "agent", 0, 500, {"openinference.span.kind": "AGENT"})])]})

    traces = client.get("/api/projects/proj/runs", params={"scope": "traces"}).json()["items"]
    by_name = {t["name"]: t for t in traces}
    assert set(by_name) == {"agent", "tools/call search"}  # one trace per service, no id collision
    a, b = by_name["agent"], by_name["tools/call search"]
    assert a["id"] == str(uuid.uuid5(uuid.UUID(TRACE), "agent-a"))
    assert a["metadata"]["otel.trace_id"] == b["metadata"]["otel.trace_id"] == TRACE  # find both parts
    assert round(b["start_time"] - a["start_time"], 6) == 0.1 and round(b["latency"], 6) == 0.35  # widened over both entries
    assert b["input_preview"] == "q1"

    runs = client.get(f"/api/traces/{b['id']}").json()["runs"]
    assert [(r["name"], r["parent_run_id"] == b["id"]) for r in runs] == [
        ("tools/call search", False), ("tools/call search", True), ("db lookup", False), ("tools/call search", True)]
    assert runs[1]["extra"]["metadata"]["otel.remote_parent_span_id"] == a_call1
    assert runs[2]["parent_run_id"] == runs[1]["id"]
    assert [r["run_type"] for r in client.get(f"/api/traces/{a['id']}").json()["runs"]] == ["agent", "chain", "chain"]


def test_vercel_ai_sdk(client):
    """ai@5 generateText with MockLanguageModelV2, one tool round trip, experimental_telemetry on."""
    post(client, fixture("vercel_ai.json"))
    t, runs = only_trace(client)
    assert t["name"] == "ai.generateText" and t["thread_id"] == "t-v" and t["total_tokens"] == 30
    assert t["input_preview"] == "Weather in Pune?" and t["output_preview"] == "It is sunny in Pune."
    assert [(r["name"], r["run_type"]) for r in runs] == [
        ("ai.generateText", "chain"), ("ai.generateText.doGenerate", "llm"), ("weather", "tool"),
        ("ai.generateText.doGenerate", "llm")]
    first, tool, second = runs[1:]
    assert first["model"] == "mock-model-id" and (first["input_tokens"], first["output_tokens"]) == (10, 5)
    assert first["inputs"]["messages"] == [{"role": "system", "content": "You are terse."},
                                           {"role": "user", "content": "Weather in Pune?"}]
    assert first["outputs"]["messages"] == [{"role": "assistant", "content": "", "tool_calls": [
        {"name": "weather", "id": "call_1", "args": {"city": "Pune"}}]}]
    assert second["inputs"]["messages"][3]["tool_call_id"] == "call_1"
    assert json.loads(second["inputs"]["messages"][3]["content"]) == {"city": "Pune", "sky": "sunny"}
    assert tool["inputs"] == {"city": "Pune"} and tool["outputs"] == {"city": "Pune", "sky": "sunny"}
    assert first["extra"]["invocation_params"]["maxRetries"] == 2
    assert first["extra"]["metadata"]["ai.model.provider"] == "mock-provider"  # unmapped stays


def test_langfuse_sdk_v3(client):
    """langfuse 3.15 Python SDK: agent > generation + tool (tool marked level=ERROR)."""
    post(client, fixture("langfuse_v3.json"))
    t, runs = only_trace(client)
    assert t["name"] == "support-agent" and t["thread_id"] == "sess-1" and t["tags"] == ["prod", "beta"]
    assert t["metadata"]["tenant"] == "acme" and t["metadata"]["user.id"] == "u-1"
    assert t["input_preview"] == "where is my order?" and t["output_preview"] == "It shipped."
    root, gen, tool = runs
    assert root["run_type"] == "agent" and gen["run_type"] == "llm" and tool["run_type"] == "tool"
    assert gen["model"] == "fake-model" and (gen["input_tokens"], gen["output_tokens"], gen["total_tokens"]) == (12, 4, 16)
    assert gen["inputs"] == {"messages": [{"role": "user", "content": "where is my order?"}]}
    assert gen["outputs"] == {"messages": [{"role": "assistant", "content": "Let me check."}]}
    assert gen["extra"]["invocation_params"]["temperature"] == 0.1 and gen["extra"]["metadata"]["step"] == 1
    assert tool["status"] == "error" and tool["error"] == "tool exploded" and tool["inputs"] == {"order": 42}


def test_agent_spans(client):
    """OpenInference AGENT and GenAI invoke_agent/create_agent map to the agent run type."""
    post(client, {"resourceSpans": [resource("svc", [
        span("1000000000000001", "", "root", 0, 10, {"openinference.span.kind": "AGENT"}),
        span("1000000000000002", "1000000000000001", "invoke_agent helper", 1, 5, {"gen_ai.operation.name": "invoke_agent"}),
        span("1000000000000003", "1000000000000001", "create_agent x", 6, 7, {"gen_ai.operation.name": "create_agent"})])]})
    _, runs = only_trace(client)
    assert [r["run_type"] for r in runs] == ["agent", "agent", "agent"]
    agents = client.get("/api/projects/proj/runs", params={"scope": "runs", "run_type": "agent"}).json()["items"]
    assert len(agents) == 3


def run_sdk(script, **env):
    """Run an SDK snippet in its own process (SDKs install global tracer providers)."""
    import os
    import subprocess
    import sys
    r = subprocess.run([sys.executable, "-c", script], env={**os.environ, **env}, capture_output=True, text=True,
                       timeout=60)
    assert r.returncode == 0, r.stderr[-2000:]


def test_langfuse_sdk_live(live_server):
    """The real Langfuse SDK pointed at lighttrace: LANGFUSE_HOST + the lighttrace key as secret key."""
    pytest.importorskip("langfuse")
    url, app = live_server
    run_sdk("""
from langfuse import Langfuse
lf = Langfuse()
with lf.start_as_current_observation(as_type="agent", name="lf-agent", input={"question": "hi"}) as a:
    a.update(output={"answer": "yo"})
lf.shutdown()
""", LANGFUSE_HOST=url, LANGFUSE_PUBLIC_KEY="pk-anything", LANGFUSE_SECRET_KEY=API_KEY)
    [t] = app.state.store.runs("default", {}, "traces")["items"]
    assert (t["name"], t["run_type"], t["input_preview"], t["output_preview"]) == ("lf-agent", "agent", "hi", "yo")
