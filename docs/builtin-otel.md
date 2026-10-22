# Frameworks with built-in OpenTelemetry

These frameworks create OpenTelemetry spans themselves; they only need somewhere to send them, which
`register()` provides (it sets the global OpenTelemetry provider they use):

```python
import lighttrace
lighttrace.register()      # call it before creating agents
```

## Pydantic AI

`register()` also switches on Pydantic AI's instrumentation (`Agent.instrument_all()`). Runnable:
[`examples/pydantic_ai_demo.py`](../examples/pydantic_ai_demo.py) (TestModel), tested end to end;
Pydantic AI 0.4 and 2.x and the Logfire route are tested with real payloads.

## Google ADK

Nothing else to do; call `register()` before importing ADK's agents. Runnable:
[`examples/google_adk_demo.py`](../examples/google_adk_demo.py) (a fake model in place of Gemini), tested
end to end (ADK 1.10) and with a real ADK 2.12 payload. ADK's top span (`invocation`) carries no input or
output; the conversation is on the `call_llm` runs.

## Strands Agents

Nothing else to do: with a global provider in place, Strands uses it. Documented, not run here (no fake
model in Strands).

## Microsoft Agent Framework

Nothing else to do: its instrumentation is on by default; `register()` also sets `ENABLE_SENSITIVE_DATA`
(prompts and answers) unless you set it, or pass `capture_content=False`. Documented, not run here.

## Semantic Kernel

Nothing else to do: `register()` sets Semantic Kernel's GenAI diagnostics switches
(`SEMANTICKERNEL_EXPERIMENTAL_GENAI_ENABLE_OTEL_DIAGNOSTICS[_SENSITIVE]`) unless you set them; call it
before creating the kernel. Documented, not run here.

## Vercel AI SDK and Mastra (JavaScript)

lighttrace is a Python package, so JavaScript apps use the AI SDK's own OpenTelemetry and send to the
receiver directly:

```bash
npm install ai @opentelemetry/sdk-trace-node @opentelemetry/sdk-trace-base \
  @opentelemetry/exporter-trace-otlp-proto @opentelemetry/resources
OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:1984 OTEL_EXPORTER_OTLP_HEADERS=x-api-key=<key> \
OTEL_SERVICE_NAME=my-app node app.mjs
```

Register a tracer provider with an OTLP exporter and turn telemetry on per call
(`experimental_telemetry: { isEnabled: true }`; newer versions: `registerTelemetry(new OpenTelemetry())`
from `@ai-sdk/otel`). Runnable: [`examples/vercel-ai/`](../examples/vercel-ai/) (mock model), run by hand;
the mapping, including the new `gen_ai.client.operation.time_to_first_chunk`, is tested. **Mastra**
builds on the AI SDK; its OpenTelemetry export to the same endpoint maps the same way (documented only).
