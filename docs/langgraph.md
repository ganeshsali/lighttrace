# LangGraph

LangGraph runs on LangChain, so both LangChain routes work unchanged.

## Route 1: the LangSmith SDK (no code change, recommended)

```bash
export LANGSMITH_TRACING=true
export LANGSMITH_ENDPOINT=http://127.0.0.1:1984
export LANGSMITH_API_KEY=<your key>
export LANGSMITH_PROJECT=my-agent
```

Each `graph.invoke(...)` becomes one trace with every node, LLM call and tool inside it. Pass
`config={"metadata": {"thread_id": thread_id}}` (the same id you give the checkpointer) and the
**Threads** tab shows the whole conversation.

Runnable: [`examples/demo_app.py`](../examples/demo_app.py): a LangGraph agent with a tool, a fake
model and three threads. This route has an end-to-end test (`tests/test_e2e.py`).

## Route 2: OpenTelemetry with OpenInference

```bash
pip install openinference-instrumentation-langchain opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
```

```python
from openinference.instrumentation.langchain import LangChainInstrumentor
# provider = TracerProvider() ... as in the overview
LangChainInstrumentor().instrument(tracer_provider=provider)
```

with the `OTEL_*` variables from [the overview](README.md). Runnable:
[`examples/otel_demo.py`](../examples/otel_demo.py) (the same graph as `demo_app.py`), tested end to end.

## OpenLLMetry alternative

```bash
pip install traceloop-sdk
```

```python
from traceloop.sdk import Traceloop
Traceloop.init(app_name="my-agent", api_endpoint="http://127.0.0.1:1984", headers={"x-api-key": "<your key>"})
```
