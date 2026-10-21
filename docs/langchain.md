# LangChain

## Route 1: the LangSmith SDK (no code change)

LangChain already ships the `langsmith` SDK. Point it here with environment variables:

```bash
export LANGSMITH_TRACING=true
export LANGSMITH_ENDPOINT=http://127.0.0.1:1984
export LANGSMITH_API_KEY=<your key>
export LANGSMITH_PROJECT=my-app      # ignored with a project key: the key decides
```

That's all. This route gives the richest traces (every chain, prompt, parser and LLM call, with
LangChain's own inputs and outputs). Group turns into threads with
`config={"metadata": {"thread_id": "..."}}`.

Runnable: [`examples/demo_app.py`](../examples/demo_app.py) (fake chat model, no API key needed).

## Route 2: OpenTelemetry with OpenInference

For apps that already use OpenTelemetry, or that should send to several backends:

```bash
pip install openinference-instrumentation-langchain opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
```

Set the `OTEL_*` variables from [the overview](README.md), then before your chains run:

```python
from openinference.instrumentation.langchain import LangChainInstrumentor
# provider = TracerProvider() ... as in the overview
LangChainInstrumentor().instrument(tracer_provider=provider)
```

Runnable: [`examples/otel_demo.py`](../examples/otel_demo.py) (tested end to end).

### LangSmith SDK in OpenTelemetry mode

The LangSmith SDK can also emit OpenTelemetry instead of its own format:

```bash
pip install 'langsmith[otel]'
export LANGSMITH_TRACING=true LANGSMITH_OTEL_ENABLED=true LANGSMITH_OTEL_ONLY=true
export OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:1984 OTEL_EXPORTER_OTLP_HEADERS=x-api-key=<your key>
```

lighttrace reads its `langsmith.*` attributes (tested with a real payload). Route 1 is simpler.

## OpenLLMetry alternative

```bash
pip install traceloop-sdk
```

```python
from traceloop.sdk import Traceloop
Traceloop.init(app_name="my-app", api_endpoint="http://127.0.0.1:1984", headers={"x-api-key": "<your key>"})
```
