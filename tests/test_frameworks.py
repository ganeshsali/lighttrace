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
    env = {**{k: v for k, v in os.environ.items() if not k.startswith(("OTEL_", "LIGHTTRACE_"))},
           # lighttrace.register() reads these; the plain-OTel examples read the OTEL_* ones
           "LIGHTTRACE_ENDPOINT": url, "LIGHTTRACE_API_KEY": API_KEY, "LIGHTTRACE_PROJECT": service,
           "OTEL_EXPORTER_OTLP_ENDPOINT": url, "OTEL_EXPORTER_OTLP_HEADERS": f"x-api-key={API_KEY}",
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


def test_langgraph_register(live_server):
    traces, trees = run_example(live_server, "langgraph_demo.py", "langgraph-demo", "langgraph")
    assert len(traces) == 6 and {t["thread_id"] for t in traces} >= {"thread-a", "thread-b", "thread-c"}
    assert any(t["status"] == "error" for t in traces)
    assert any("tool" in types(runs) for runs in trees)


def test_langgraph_openinference(live_server):
    traces, trees = run_example(live_server, "otel_demo.py", "demo-otel", "openinference.instrumentation.langchain",
                                "langgraph")
    assert len(traces) == 6 and {t["thread_id"] for t in traces} >= {"thread-a", "thread-b", "thread-c"}
    assert any(t["status"] == "error" for t in traces)
    assert all({"llm", "chain"} <= types(runs) for runs in trees)
    assert any("tool" in types(runs) for runs in trees)


def test_llamaindex(live_server):
    traces, trees = run_example(live_server, "llamaindex_demo.py", "llamaindex-demo", "llama_index.core")
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
    [t], [runs] = run_example(live_server, "openai_agents_demo.py", "openai-agents-demo", "agents")
    # The fake model never calls the OpenAI client, so there is no model call to record; with a
    # real model class there is (test_openai_agents_real_model_class).
    assert {"agent", "tool"} <= types(runs)
    tool = next(r for r in runs if r["run_type"] == "tool")
    assert tool["name"] == "weather" and "sunny" in str(tool["outputs"])


def test_openai_agents_real_model_class(live_server, openai_stub):
    """The real OpenAIChatCompletionsModel against a stub server: the official listener records the
    workflow, agent and tool, and the OpenAI client listener the model calls, all in one trace."""
    pytest.importorskip("agents")
    url, app = live_server
    script = f"""
import lighttrace
from agents import Agent, OpenAIChatCompletionsModel, Runner, function_tool, set_trace_processors
from openai import AsyncOpenAI
set_trace_processors([])
lighttrace.register()

@function_tool
def weather(city: str) -> str:
    \"\"\"Weather for a city.\"\"\"
    return "sunny"

model = OpenAIChatCompletionsModel(model="stub-model", openai_client=AsyncOpenAI(base_url="{openai_stub}", api_key="x"))
print(Runner.run_sync(Agent(name="weather-agent", tools=[weather], model=model), "Weather in Pune?").final_output)
"""
    env = {**{k: v for k, v in os.environ.items() if not k.startswith(("OTEL_", "LIGHTTRACE_"))},
           "LIGHTTRACE_ENDPOINT": url, "LIGHTTRACE_API_KEY": API_KEY, "LIGHTTRACE_PROJECT": "agents-real"}
    r = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and "sunny" in r.stdout, r.stderr[-3000:]
    [t] = app.state.store.runs("agents-real", {}, "traces")["items"]
    runs = app.state.store.trace(t["id"])["runs"]
    assert {"agent", "llm", "tool"} <= types(runs) and t["total_tokens"] == 32


def test_crewai(live_server):
    traces, trees = run_example(live_server, "crewai_demo.py", "crewai-demo", "crewai", CREWAI_DISABLE_TELEMETRY="true")
    assert traces and any("It is sunny in Pune." in t["output_preview"] for t in traces)
