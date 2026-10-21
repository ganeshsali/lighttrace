"""Real LangGraph graph + real langsmith SDK -> running lighttrace -> UI JSON API."""

import langsmith.run_trees
import langsmith.utils
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda
from langchain_core.tools import tool
from langchain_core.tracers.langchain import wait_for_all_tracers
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from conftest import API_KEY, PASSWORD


@tool
def get_stock(item: str) -> str:
    """Look up stock for an item."""
    return f"{item}: 42 units"


def build_graph():
    model = GenericFakeChatModel(messages=iter([
        AIMessage(content="", tool_calls=[{"name": "get_stock", "args": {"item": "bolts"}, "id": "call_1"}],
                  usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}),
        AIMessage(content="We have 42 bolts.",
                  usage_metadata={"input_tokens": 20, "output_tokens": 7, "total_tokens": 27}),
    ]))

    def agent(state: MessagesState):
        return {"messages": [model.invoke(state["messages"])]}

    def route(state: MessagesState):
        return "tools" if state["messages"][-1].tool_calls else END

    g = StateGraph(MessagesState)
    g.add_node("agent", agent)
    g.add_node("tools", ToolNode([get_stock]))
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", route, ["tools", END])
    g.add_edge("tools", "agent")
    return g.compile()


def test_langgraph_trace_end_to_end(live_server, monkeypatch):
    url, app = live_server
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_ENDPOINT", url)
    monkeypatch.setenv("LANGSMITH_API_KEY", API_KEY)
    monkeypatch.setenv("LANGSMITH_PROJECT", "e2e-proj")
    langsmith.utils.get_env_var.cache_clear()
    langsmith.utils.get_tracer_project.cache_clear()
    monkeypatch.setattr(langsmith.run_trees, "_CLIENT", None)  # the SDK caches its client per process

    meta = {"metadata": {"thread_id": "t-1", "user_id": "u-9"}}
    result = build_graph().invoke({"messages": [HumanMessage("how many bolts?")]}, config=meta)
    assert result["messages"][-1].content == "We have 42 bolts."
    second = GenericFakeChatModel(messages=iter([AIMessage("You're welcome!", usage_metadata={
        "input_tokens": 3, "output_tokens": 2, "total_tokens": 5})]))
    RunnableLambda(lambda q: second.invoke(q)).with_config(run_name="followup").invoke("thanks", config=meta)
    wait_for_all_tracers()

    ui = TestClient(app, headers={"x-lighttrace": "1"})
    assert ui.get("/api/projects").status_code == 401
    ui.post("/api/login", json={"password": PASSWORD})

    projects = ui.get("/api/projects").json()
    assert [(p["name"], p["trace_count"]) for p in projects] == [("e2e-proj", 2)]
    assert projects[0]["stats"]["threads"] == 1 and projects[0]["stats"]["total_tokens"] == 47

    traces = ui.get("/api/projects/e2e-proj/runs", params={"scope": "traces"}).json()["items"]
    t = next(x for x in traces if x["name"] == "LangGraph")
    assert t["status"] == "success" and t["total_tokens"] == 42 and t["thread_id"] == "t-1"
    assert "how many bolts?" in t["input_preview"]
    assert "We have 42 bolts." in t["output_preview"]

    # metadata filter, runs scope, threads list and the thread itself
    assert len(ui.get("/api/projects/e2e-proj/runs", params={"meta": "user_id:u-9"}).json()["items"]) == 2
    assert ui.get("/api/projects/e2e-proj/runs", params={"meta": "user_id:x"}).json()["items"] == []
    tools = ui.get("/api/projects/e2e-proj/runs", params={"scope": "runs", "run_type": "tool"}).json()["items"]
    assert [r["name"] for r in tools] == ["get_stock"]
    threads = ui.get("/api/projects/e2e-proj/threads").json()["items"]
    assert len(threads) == 1 and threads[0]["thread_id"] == "t-1" and threads[0]["turns"] == 2
    assert threads[0]["total_tokens"] == 47 and "how many bolts?" in threads[0]["first_input"]
    thread = ui.get("/api/projects/e2e-proj/thread", params={"id": "t-1"}).json()
    assert [turn["name"] for turn in thread["turns"]] == ["LangGraph", "followup"]
    assert len(thread["turns"][0]["llm_runs"]) == 2 and thread["stats"]["turns"] == 2

    trace = ui.get(f"/api/traces/{t['id']}").json()
    assert trace["thread_id"] == "t-1" and trace["project"] == "e2e-proj"
    runs = trace["runs"]
    by_id = {r["id"]: r for r in runs}
    root = runs[0]
    assert root["is_root"] == 1 and root["parent_run_id"] is None and root["id"] == t["id"]
    assert all(r["trace_id"] == root["id"] for r in runs)
    assert all(r["parent_run_id"] in by_id for r in runs[1:])
    assert all(r["project"] == "e2e-proj" and r["status"] == "success" for r in runs)
    assert all(r["latency"] is not None and r["latency"] >= 0 for r in runs)

    llms = [r for r in runs if r["run_type"] == "llm"]
    tools = [r for r in runs if r["run_type"] == "tool"]
    assert len(llms) == 2 and len(tools) == 1
    assert sorted(r["total_tokens"] for r in llms) == [15, 27]
    assert sorted((r["input_tokens"], r["output_tokens"]) for r in llms) == [(10, 5), (20, 7)]
    assert tools[0]["name"] == "get_stock"
    assert "bolts" in str(tools[0]["inputs"]) and "42 units" in str(tools[0]["outputs"])
    assert {"agent", "tools"} <= {r["name"] for r in runs}
    for r in runs:  # every child's parent chain leads to the root
        while r["parent_run_id"]:
            r = by_id[r["parent_run_id"]]
        assert r is root
    assert root["extra"]["metadata"]["thread_id"] == "t-1"
