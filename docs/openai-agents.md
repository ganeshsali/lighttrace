# OpenAI Agents SDK

```bash
pip install openai-agents openinference-instrumentation-openai-agents opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
export OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:1984
export OTEL_EXPORTER_OTLP_HEADERS=x-api-key=<your key>
export OTEL_SERVICE_NAME=my-agents
```

```python
from agents import set_trace_processors
from openinference.instrumentation.openai_agents import OpenAIAgentsInstrumentor
# provider = TracerProvider() ... as in the overview
set_trace_processors([])  # optional: stop the SDK from also uploading traces to OpenAI
OpenAIAgentsInstrumentor().instrument(tracer_provider=provider)
```

Each `Runner.run(...)` becomes a trace: the agent, every model call (with tokens), tool calls and
handoffs.

Runnable: [`examples/openai_agents_demo.py`](../examples/openai_agents_demo.py) (a fake model, no
OpenAI key), tested end to end in `tests/test_frameworks.py`.

## OpenLLMetry alternative

```bash
pip install traceloop-sdk
```

```python
from traceloop.sdk import Traceloop
Traceloop.init(app_name="my-agents", api_endpoint="http://127.0.0.1:1984", headers={"x-api-key": "<your key>"})
```

OpenLLMetry instruments the OpenAI client underneath; the agent structure then comes from its own
`openai-agents` instrumentation.
