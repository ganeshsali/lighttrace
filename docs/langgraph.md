# LangGraph

LangGraph runs on LangChain, so it is the [LangChain](langchain.md) setup:

```python
import lighttrace
lighttrace.register()
```

Each `graph.invoke(...)` / `ainvoke` / `astream` becomes one trace: the graph run, each agent and model
call (tokens, time to first token) and each tool call. Pass the conversation as
`config={"configurable": {"thread_id": thread_id}}` (the same id the checkpointer uses) and the
**Threads** tab shows the whole conversation.

Runnable: [`examples/langgraph_demo.py`](../examples/langgraph_demo.py) (a LangGraph agent with tools,
a fake model, three threads and one failing turn), tested end to end, and `tests/test_register.py`
(streaming, tokens, time to first token).

Other ways (LangSmith SDK OTel mode, OpenInference): see [LangChain](langchain.md#other-ways).
