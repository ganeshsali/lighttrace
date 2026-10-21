# CrewAI

```bash
pip install crewai openinference-instrumentation-crewai opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
export CREWAI_DISABLE_TELEMETRY=true       # CrewAI's own usage telemetry, not your traces
export OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:1984
export OTEL_EXPORTER_OTLP_HEADERS=x-api-key=<your key>
export OTEL_SERVICE_NAME=my-crew
```

```python
from openinference.instrumentation.crewai import CrewAIInstrumentor
# provider = TracerProvider() ... as in the overview
CrewAIInstrumentor().instrument(tracer_provider=provider)
```

For the LLM calls inside a crew as their own runs (prompts, tokens), also instrument LiteLLM, which
CrewAI uses underneath: `pip install openinference-instrumentation-litellm`, then
`LiteLLMInstrumentor().instrument(tracer_provider=provider)`.

Runnable: [`examples/crewai_demo.py`](../examples/crewai_demo.py) (a fake LLM, no API key).
Not in the automated tests: the current `openinference-instrumentation-crewai` (1.x) asks for
`crewai>=1.10.1`, which pip could not resolve on the test machine (Python 3.11 and 3.12), so
`tests/test_frameworks.py` skips. The script was run by hand against lighttrace with crewai 1.9.3 and the
instrumentor's version check bypassed (`instrument(..., skip_dep_check=True)`): the crew kickoff and the
agent run arrive as a trace with their inputs and outputs.

## OpenLLMetry alternative

```bash
pip install traceloop-sdk
```

```python
from traceloop.sdk import Traceloop
Traceloop.init(app_name="my-crew", api_endpoint="http://127.0.0.1:1984", headers={"x-api-key": "<your key>"})
```
