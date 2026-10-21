# LangGraph

LangGraph runs on LangChain, so both [LangChain options](langchain.md) work unchanged and send to the one
receiver (`/v1/traces`).

## Option 1: the LangSmith SDK's OpenTelemetry mode (no code change, recommended)

```bash
pip install 'langsmith[otel]'
export LANGSMITH_TRACING=true
export LANGSMITH_OTEL_ENABLED=true
export LANGSMITH_OTEL_ONLY=true            # never send to LangSmith's cloud
export OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:1984
export OTEL_EXPORTER_OTLP_HEADERS=x-api-key=<your key>
export OTEL_SERVICE_NAME=my-agent
```

Each `graph.invoke(...)` becomes one trace with every node, LLM call and tool inside it. Pass
`config={"metadata": {"thread_id": thread_id}}` (the same id you give the checkpointer) and the
**Threads** tab shows the whole conversation. Short scripts: call
`opentelemetry.trace.get_tracer_provider().shutdown()` before exiting.

Runnable: [`examples/demo_app.py`](../examples/demo_app.py): a LangGraph agent with tools, a fake model,
three threads and one failing turn. End to end test: `tests/test_e2e.py`.

## Option 2: OpenInference

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
