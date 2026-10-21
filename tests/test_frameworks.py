"""End to end: run each examples/ script (a real framework + its instrumentor, fake models, no LLM
calls) against a live lighttrace. Each test skips when its framework is not installed."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import API_KEY

EX = Path(__file__).parent.parent / "examples"


def run_example(live_server, script, service, *modules, **env):
    for m in modules:
        pytest.importorskip(m)
    url, app = live_server
    env = {**os.environ, "OTEL_EXPORTER_OTLP_ENDPOINT": url, "OTEL_EXPORTER_OTLP_HEADERS": f"x-api-key={API_KEY}",
           "OTEL_SERVICE_NAME": service, "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
           "LANGSMITH_TRACING": "false", **env}
    r = subprocess.run([sys.executable, str(EX / script)], env=env, capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stderr[-3000:]
    store = app.state.store
    traces = store.runs(service, {}, "traces", limit=50)["items"]
    return traces, [store.trace(t["id"])["runs"] for t in traces]


def types(runs):
    return {r["run_type"] for r in runs}


def test_manual_otel(live_server):
    [t], [runs] = run_example(live_server, "manual_otel.py", "manual-demo", "opentelemetry.sdk")
    assert t["run_type"] == "agent" and t["thread_id"] == "thread-1" and t["total_tokens"] == 19
    assert t["output_preview"] == "" and [r["run_type"] for r in runs] == ["agent", "tool", "llm"]
    assert runs[2]["outputs"]["messages"][0]["content"] == "It is sunny in Pune."


def test_langgraph_openinference(live_server):
    traces, trees = run_example(live_server, "otel_demo.py", "demo-otel", "openinference.instrumentation.langchain",
                                "langgraph")
    assert len(traces) == 6 and {t["thread_id"] for t in traces} >= {"thread-a", "thread-b", "thread-c"}
    assert any(t["status"] == "error" for t in traces)
    assert all({"llm", "chain"} <= types(runs) for runs in trees)
    assert any("tool" in types(runs) for runs in trees)


def test_llamaindex(live_server):
    traces, trees = run_example(live_server, "llamaindex_demo.py", "llamaindex-demo",
                                "openinference.instrumentation.llama_index", "llama_index.core")
    t, runs = next((t, r) for t, r in zip(traces, trees) if t["input_preview"] == "Weather in Pune?")
    assert {"retriever", "llm", "embedding"} <= types(runs)
    docs = next(r for r in runs if r["run_type"] == "retriever")["outputs"]["documents"]
    assert docs[0]["page_content"] in ("Pune is sunny today.", "Mumbai has heavy rain.")  # mock vectors: any doc


def test_pydantic_ai(live_server):
    [t], [runs] = run_example(live_server, "pydantic_ai_demo.py", "pydantic-ai-demo", "pydantic_ai")
    assert t["run_type"] == "agent" and t["input_preview"] == "Weather in Pune?"
    assert {"agent", "llm", "tool"} <= types(runs) and t["total_tokens"] > 0


def test_google_adk(live_server):
    [t], [runs] = run_example(live_server, "google_adk_demo.py", "adk-demo", "google.adk")
    assert t["total_tokens"] == 28 and {"agent", "llm", "tool"} <= types(runs)
    assert next(r for r in runs if r["run_type"] == "llm")["inputs"]["messages"][-1]["content"] == "Weather in Pune?"


def test_openai_agents(live_server):
    [t], [runs] = run_example(live_server, "openai_agents_demo.py", "openai-agents-demo",
                              "openinference.instrumentation.openai_agents", "agents")
    assert {"agent", "llm", "tool"} <= types(runs)
    assert t["total_tokens"] == 30
    tool = next(r for r in runs if r["run_type"] == "tool")
    assert tool["name"] == "weather" and "sunny" in str(tool["outputs"])


def test_crewai(live_server):
    traces, trees = run_example(live_server, "crewai_demo.py", "crewai-demo", "openinference.instrumentation.crewai",
                                "crewai", CREWAI_DISABLE_TELEMETRY="true")
    assert traces and any("It is sunny in Pune." in t["output_preview"] for t in traces)
