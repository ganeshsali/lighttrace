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
[`examples/google_adk_demo.py`](../examples/google_adk_demo.py) (a fake model in place of Gemini).

Status: tested end to end with **ADK 2.7.0**, and a real ADK 2.12 payload is tested too. 2.7.0 is the
newest release that installs next to lighttrace: ADK 2.12 caps OpenTelemetry at 1.42.1, while
OpenTelemetry's GenAI listeners need 1.43 or newer.

ADK's top span (`invocation`) carries no input or output; the conversation is on the `call_llm` runs.

## Strands Agents

Nothing else to do: with a global provider in place, Strands uses it. Runnable:
[`examples/strands_demo.py`](../examples/strands_demo.py). Tested end to end (strands-agents 1.62) with
its real OpenAI model class against a stub server: the agent, each event-loop cycle, the model calls
and the tool.

## Microsoft Agent Framework

Nothing else to do: its instrumentation is on by default. `register()` also sets
`ENABLE_SENSITIVE_DATA`, so prompts and answers are recorded, unless you set it yourself or pass
`capture_content=False`.

Its OpenAI client now lives in a separate package: `pip install agent-framework-core agent-framework-openai`.
Runnable: [`examples/agent_framework_demo.py`](../examples/agent_framework_demo.py). Tested end to end
(agent-framework-core 1.22, agent-framework-openai 1.17) against a stub server.

## Semantic Kernel

Nothing else to do: `register()` sets Semantic Kernel's GenAI diagnostics switches
(`SEMANTICKERNEL_EXPERIMENTAL_GENAI_ENABLE_OTEL_DIAGNOSTICS[_SENSITIVE]`) unless you set them. Call it
before importing Semantic Kernel, which reads them on import.

Semantic Kernel writes prompts and answers as Python log records rather than span attributes.
`register()` puts them on the model call's span as GenAI events, so they show up as chat. These
records stay out of your app's own logs.

Runnable: [`examples/semantic_kernel_demo.py`](../examples/semantic_kernel_demo.py) (a
`ChatCompletionAgent` with a plugin). Tested end to end (semantic-kernel 1.45) against a stub server.

## Vercel AI SDK and Mastra (JavaScript)

lighttrace is a Python package, so JavaScript apps use the AI SDK's own OpenTelemetry and send to the
receiver directly:

```bash
npm install ai @ai-sdk/otel @opentelemetry/sdk-trace-node @opentelemetry/sdk-trace-base \
  @opentelemetry/exporter-trace-otlp-proto @opentelemetry/resources
OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:1984 OTEL_EXPORTER_OTLP_HEADERS=x-api-key=<key> \
OTEL_SERVICE_NAME=my-app node app.mjs
```

Register a tracer provider with an OTLP exporter, then the AI SDK's telemetry integration once at
startup: `registerTelemetry(new OpenTelemetry())` (from `ai` and `@ai-sdk/otel`). Name the agent with
`telemetry: { functionId: "..." }` on a call.

AI SDK 5 and 6 instead turn telemetry on per call with `experimental_telemetry: { isEnabled: true }`
and emit `ai.*` attributes; lighttrace reads those too.

Runnable: [`examples/vercel-ai/`](../examples/vercel-ai/) (mock model). Tested end to end with
**AI SDK 7.0.148** on Node 24 (`npm install` in that folder, then `pytest tests/test_frameworks.py -k vercel`).

**Mastra** builds on the AI SDK; its OpenTelemetry export to the same endpoint maps the same way
(documented only).
