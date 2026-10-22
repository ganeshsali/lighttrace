"""Sample LangGraph app with a fake model (no LLM calls) and no tracing code of its own.

Traced with lighttrace.register(): see examples/langgraph_demo.py. Or, with no code at all, through the
LangSmith SDK's OpenTelemetry mode (what tests/test_e2e.py runs):

    pip install 'langsmith[otel]' langgraph
    LANGSMITH_TRACING=true LANGSMITH_OTEL_ENABLED=true LANGSMITH_OTEL_ONLY=true \
    OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:1984 OTEL_EXPORTER_OTLP_HEADERS=x-api-key=<key> \
    OTEL_SERVICE_NAME=demo python examples/demo_app.py
"""

import time

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_core.tracers.langchain import wait_for_all_tracers
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode


@tool
def get_stock(item: str, warehouse: str = "main") -> dict:
    """Current stock for an item in a warehouse."""
    time.sleep(0.15)  # pretend the ERP is slow, so the waterfall shows something
    return {"item": item, "warehouse": warehouse, "on_hand": 42, "reserved": 5}


@tool
def create_purchase_order(item: str, qty: int) -> str:
    """Create a draft purchase order."""
    return f"PO-1007 drafted: {qty} x {item}"


def usage(i, o):
    return {"input_tokens": i, "output_tokens": o, "total_tokens": i + o}


def build(replies, fail=False):
    model = GenericFakeChatModel(messages=iter(replies))

    def agent(state: MessagesState):
        if fail and len(state["messages"]) > 2:
            raise RuntimeError("ERP API timeout while fetching supplier list")
        msgs = [SystemMessage("You are MIRA-like ERP assistant. Use tools for stock questions.")] + state["messages"]
        time.sleep(0.05)
        return {"messages": [model.invoke(msgs)]}

    g = StateGraph(MessagesState)
    g.add_node("agent", agent)
    g.add_node("tools", ToolNode([get_stock, create_purchase_order]))
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", lambda s: "tools" if s["messages"][-1].tool_calls else END, ["tools", END])
    g.add_edge("tools", "agent")
    return g.compile()


def call(name, args, cid):
    return {"name": name, "args": args, "id": cid}


def turn(graph, text, meta, history=()):
    """One chat turn; returns the final state (None if the graph raised)."""
    try:
        return graph.invoke({"messages": [*history, HumanMessage(text)]}, meta)
    except RuntimeError:
        return None


def main():
    alice = {"metadata": {"thread_id": "thread-a", "user_id": "alice"}, "tags": ["chat"]}
    s1 = turn(build([
        AIMessage("", tool_calls=[call("get_stock", {"item": "M8 bolts"}, "c1")], usage_metadata=usage(120, 18)),
        AIMessage("You have 42 M8 bolts on hand (5 reserved).", usage_metadata=usage(190, 14)),
    ]), "How many M8 bolts do we have?", alice)
    s2 = turn(build([
        AIMessage("", tool_calls=[call("get_stock", {"item": "hex nuts", "warehouse": "east"}, "c2"),
                                  call("create_purchase_order", {"item": "hex nuts", "qty": 500}, "c3")],
                  usage_metadata=usage(140, 40)),
        AIMessage("Stock is 42 in east; I drafted PO-1007 for 500 hex nuts.", usage_metadata=usage(260, 22)),
    ]), "Check hex nuts in east and reorder 500", alice, s1["messages"])
    turn(build([AIMessage("PO-1007 is a draft for 500 hex nuts; it still needs approval.", usage_metadata=usage(300, 16))]),
         "What's the status of that PO?", alice, s2["messages"])

    turn(build([AIMessage("", tool_calls=[call("get_stock", {"item": "washers"}, "c4")], usage_metadata=usage(110, 12))],
               fail=True), "Washers stock and best supplier?", {"metadata": {"thread_id": "thread-b", "user_id": "bob"}})
    turn(build([AIMessage("Hello! Ask me about stock, orders or invoices.", usage_metadata=usage(60, 11))]),
         "hi", {"metadata": {"thread_id": "thread-c", "user_id": "carol"}})
    turn(build([AIMessage("Nightly stock summary: 3 items below reorder level.", usage_metadata=usage(80, 12))]),
         "nightly summary", {"tags": ["batch"]})  # no thread: a standalone trace

    wait_for_all_tracers()
    from opentelemetry import trace
    getattr(trace.get_tracer_provider(), "shutdown", lambda: None)()  # flush what LangSmith's OTel mode queued
    print("sent 6 traces (3 threads)")


if __name__ == "__main__":
    main()
