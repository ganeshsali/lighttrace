"""lighttrace.register() in an app: picks the right official listeners and sends to the server."""
import os
import subprocess
import sys

import lighttrace.client as client
from conftest import API_KEY


def test_model_clients_only_where_the_framework_does_not_record_calls(monkeypatch):
    """LangChain records its model calls (client spans would duplicate them); the OpenAI Agents
    SDK's official listener leaves them to the OpenAI client listener."""
    installed = {"langchain-core", "openai"}
    monkeypatch.setattr(client, "_installed", lambda d: d in installed)
    names = lambda **kw: [d for d, _ in client._targets(**kw)]  # noqa: E731
    assert names() == ["langchain-core"]
    assert names(clients=True) == ["langchain-core", "openai"]
    installed.add("openai-agents")
    assert names() == ["langchain-core", "openai-agents", "openai"]
    installed -= {"langchain-core", "openai-agents"}
    assert names() == ["openai"]  # a plain OpenAI SDK app
    installed.add("google-adk")  # built-in OpenTelemetry covers its own model calls
    assert names() == []


def test_content_switches_follow_capture_content():
    """Microsoft Agent Framework and Semantic Kernel record prompts and answers only when switched on."""
    sensitive = ("ENABLE_SENSITIVE_DATA", "SEMANTICKERNEL_EXPERIMENTAL_GENAI_ENABLE_OTEL_DIAGNOSTICS_SENSITIVE",
                 "OPENINFERENCE_HIDE_INPUTS")
    script = ("import os, lighttrace; lighttrace.register(capture_content={}); "
              f"print([os.environ.get(v) for v in {sensitive!r}])")
    env = {**{k: v for k, v in os.environ.items() if k not in sensitive}, "LIGHTTRACE_ENDPOINT": "http://127.0.0.1:9",
           "LIGHTTRACE_API_KEY": API_KEY}
    out = {c: subprocess.run([sys.executable, "-c", script.format(c)], env=env, capture_output=True, text=True).stdout
           for c in (True, False)}
    assert out[True].strip() == "['true', 'true', None]"
    assert out[False].strip() == "[None, None, 'true']"


def test_no_endpoint_or_key_means_no_tracing(monkeypatch):
    monkeypatch.delenv("LIGHTTRACE_ENDPOINT", raising=False)
    monkeypatch.delenv("LIGHTTRACE_API_KEY", raising=False)
    assert client.register() is None and client._provider is None


APP = """
import asyncio
import lighttrace
lighttrace.register()  # the two lines an app adds

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode


@tool
def get_stock(item: str) -> str:
    \"\"\"Stock for an item.\"\"\"
    return f"{item}: 42"


model = GenericFakeChatModel(messages=iter([
    AIMessage("", tool_calls=[{"name": "get_stock", "args": {"item": "bolts"}, "id": "c1"}],
              usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}),
    AIMessage("We have 42 bolts.", usage_metadata={"input_tokens": 20, "output_tokens": 7, "total_tokens": 27}),
])).with_config(metadata={"ls_model_name": "fake-model", "ls_provider": "openai"})


async def agent(state):
    if state["messages"][-1].type == "human":  # the fake can't stream tool calls or usage
        return {"messages": [await model.ainvoke(state["messages"])]}
    out = None
    async for chunk in model.astream(state["messages"]):  # streamed: gives time to first token
        out = chunk if out is None else out + chunk
    return {"messages": [out]}


g = StateGraph(MessagesState)
g.add_node("agent", agent)
g.add_node("tools", ToolNode([get_stock]))
g.add_edge(START, "agent")
g.add_conditional_edges("agent", lambda s: "tools" if s["messages"][-1].tool_calls else END, ["tools", END])
g.add_edge("tools", "agent")
graph = g.compile()
asyncio.run(graph.ainvoke({"messages": [HumanMessage("how many bolts?")]}, {"configurable": {"thread_id": "t-9"}}))
print("done")  # no flush call: register() sends what's left when the process exits
"""


def test_register_langgraph_end_to_end(live_server):
    url, app = live_server
    env = {k: v for k, v in os.environ.items() if not k.startswith(("OTEL_", "LANGSMITH_", "LANGCHAIN_"))}
    env.update(LIGHTTRACE_ENDPOINT=url, LIGHTTRACE_API_KEY=API_KEY, LIGHTTRACE_PROJECT="my-agent")
    r = subprocess.run([sys.executable, "-c", APP], env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and "done" in r.stdout, r.stderr[-3000:]

    store = app.state.store
    [t] = store.runs("my-agent", {}, "traces")["items"]  # project from LIGHTTRACE_PROJECT, created on first trace
    assert t["thread_id"] == "t-9" and t["input_preview"] == "how many bolts?"
    assert t["output_preview"] == "We have 42 bolts."
    assert t["total_tokens"] == 15 and t["first_token_time"] is not None  # (the fake streams no usage)
    runs = store.trace(t["id"])["runs"]
    llms = [r for r in runs if r["run_type"] == "llm"]
    assert len(llms) == 2 and llms[0]["total_tokens"] == 15 and llms[1]["first_token_time"] is not None
    assert llms[0]["model"] == "fake-model"
    # OpenInference's tree: the graph, its nodes and LangChain's own names
    assert {"LangGraph", "agent", "tools", "get_stock", "GenericFakeChatModel"} <= {r["name"] for r in runs}
    assert [r["name"] for r in runs if r["run_type"] == "tool"] == ["get_stock"]
    assert all(r["extra"]["metadata"].get("thread_id") == "t-9" for r in llms)  # run metadata comes along
    assert {r["run_type"] for r in runs if r["name"] == "agent"} == {"chain"}  # a node named "agent" is a chain
