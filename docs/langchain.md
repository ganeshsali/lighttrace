# LangChain

LangChain sends to lighttrace's one receiver (`/v1/traces`) like every other framework. Two ways:

## Option 1: the LangSmith SDK's OpenTelemetry mode (no code change, recommended)

LangChain already ships the `langsmith` SDK, which can emit OpenTelemetry. Settings only:

```bash
pip install 'langsmith[otel]'              # the OpenTelemetry packages it needs
export LANGSMITH_TRACING=true
export LANGSMITH_OTEL_ENABLED=true
export LANGSMITH_OTEL_ONLY=true            # send only to lighttrace, never to LangSmith's cloud
export OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:1984
export OTEL_EXPORTER_OTLP_HEADERS=x-api-key=<your key>
export OTEL_SERVICE_NAME=my-app
```

You get every chain, prompt, parser, LLM call (with tokens) and tool, with LangChain's own inputs and
outputs; errors, tags and metadata come along. Group turns into threads with
`config={"metadata": {"thread_id": "..."}}`.

A short script must flush before it exits; a long-running server needs nothing:

```python
from opentelemetry import trace
trace.get_tracer_provider().shutdown()
```

Runnable: [`examples/demo_app.py`](../examples/demo_app.py) (fake chat model, no API key needed), tested
end to end in `tests/test_e2e.py`, which also checks that nothing is sent to LangSmith's cloud.

**Moving an app from older lighttrace versions:** replace `LANGSMITH_ENDPOINT`, `LANGSMITH_API_KEY` and
`LANGSMITH_PROJECT` with the settings above. The project now comes from the key (or `OTEL_SERVICE_NAME`
with an admin key).

## Option 2: OpenInference

For apps that already use OpenTelemetry for other things:

```bash
pip install openinference-instrumentation-langchain opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
```

Set the `OTEL_*` variables from [the overview](README.md), then before your chains run:

```python
from openinference.instrumentation.langchain import LangChainInstrumentor
# provider = TracerProvider() ... as in the overview
LangChainInstrumentor().instrument(tracer_provider=provider)
```

Runnable: [`examples/otel_demo.py`](../examples/otel_demo.py), tested end to end.

## OpenLLMetry alternative

```bash
pip install traceloop-sdk
```

```python
from traceloop.sdk import Traceloop
Traceloop.init(app_name="my-app", api_endpoint="http://127.0.0.1:1984", headers={"x-api-key": "<your key>"})
```
