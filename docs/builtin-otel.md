# Frameworks with built-in OpenTelemetry

These frameworks create OpenTelemetry spans themselves. Give them an exporter pointed at lighttrace:

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:1984
export OTEL_EXPORTER_OTLP_HEADERS=x-api-key=<your key>
export OTEL_SERVICE_NAME=my-app
```

The Python ones use the *global* tracer provider, so register it:

```python
from opentelemetry import trace
# provider = TracerProvider() ... as in the overview
trace.set_tracer_provider(provider)
```

## Pydantic AI

```bash
pip install pydantic-ai-slim opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
```

```python
from pydantic_ai import Agent
Agent.instrument_all()          # after trace.set_tracer_provider(provider)
```

Or with Logfire's SDK: `logfire.configure(send_to_logfire=False)` plus `logfire.instrument_pydantic_ai()`
(it reads the same `OTEL_*` variables). Runnable:
[`examples/pydantic_ai_demo.py`](../examples/pydantic_ai_demo.py) (TestModel), tested end to end;
Pydantic AI 0.4 and 2.x and the Logfire route are tested with real payloads.

## Google ADK

```bash
pip install google-adk opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
```

Set the global tracer provider **before importing ADK**; nothing else. Runnable:
[`examples/google_adk_demo.py`](../examples/google_adk_demo.py) (a fake model in place of Gemini),
tested end to end (ADK 1.10) and with a real ADK 2.12 payload. ADK's top span (`invocation`) carries no
input or output; open the trace to see the conversation on the `call_llm` runs.

## Strands Agents (documented only)

```bash
pip install 'strands-agents[otel]'
```

```python
from strands.telemetry import StrandsTelemetry
StrandsTelemetry().setup_otlp_exporter()   # reads the OTEL_* variables
```

Strands emits OTel GenAI attributes, which lighttrace maps. Not run here: Strands has no built-in fake
model, so there is no example script.

## Semantic Kernel (documented only)

```bash
pip install semantic-kernel opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
export SEMANTICKERNEL_EXPERIMENTAL_GENAI_ENABLE_OTEL_DIAGNOSTICS=true
export SEMANTICKERNEL_EXPERIMENTAL_GENAI_ENABLE_OTEL_DIAGNOSTICS_SENSITIVE=true   # include prompts/answers
```

Register the global tracer provider as above. Semantic Kernel emits OTel GenAI attributes and events.
Not run here (no fake model; an example would need a real LLM).

## Vercel AI SDK and Mastra (JavaScript)

```bash
npm install ai @opentelemetry/sdk-trace-node @opentelemetry/sdk-trace-base \
  @opentelemetry/exporter-trace-otlp-proto @opentelemetry/resources
```

Register a tracer provider with an OTLP exporter (it reads the `OTEL_*` variables), then turn telemetry
on per call:

```js
await generateText({ model, prompt, experimental_telemetry: { isEnabled: true, functionId: "my-agent",
  metadata: { thread_id: "..." } } });
```

In Next.js, `@vercel/otel`'s `registerOTel({ serviceName })` does the provider part. Runnable:
[`examples/vercel-ai/`](../examples/vercel-ai/) (`npm install && node index.mjs`, mock model), run end to
end by hand against lighttrace (not part of the Python test suite); the mapping is also tested with a real
payload. **Mastra** builds on the AI SDK; enable its OpenTelemetry export to the same endpoint and the
`ai.*` spans map the same way (documented only).

## OpenLLMetry alternative

For Python frameworks without their own OTel support, see [the overview](README.md#openllmetry-one-alternative-for-all-of-them).
