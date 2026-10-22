# Sending traces to lighttrace

**One method for every Python app:**

```bash
pip install lighttrace
```

```python
import lighttrace
lighttrace.register()      # once at startup, before the app does any AI work
```

```bash
export LIGHTTRACE_ENDPOINT=http://127.0.0.1:1984
export LIGHTTRACE_API_KEY=<one of the server's LIGHTTRACE_API_KEYS>
export LIGHTTRACE_PROJECT=my-app       # the project; created on its first trace
```

`register()` finds the frameworks the app uses and turns on the right listener for each (lighttrace
writes none of its own), then points one OpenTelemetry sender at the server. Same code everywhere: no
special start command, works with `--reload`, gunicorn, Docker and short scripts. Without
`LIGHTTRACE_ENDPOINT` and `LIGHTTRACE_API_KEY` it does nothing.

Options: `register(project=..., endpoint=..., api_key=...)` instead of the variables;
`capture_content=False` to leave prompts, answers and tool data out; `clients=True/False` to force
tracing of raw model clients (OpenAI, Anthropic, …) on or off. It returns the OpenTelemetry
`TracerProvider` (or `None` when off), and adds itself to an existing OpenTelemetry setup if the app has one.

| Page | Notes for |
|---|---|
| [LangChain](langchain.md) | LangChain apps |
| [LangGraph](langgraph.md) | LangGraph agents, threads |
| [LlamaIndex](llamaindex.md) | LlamaIndex apps |
| [CrewAI](crewai.md) | CrewAI crews |
| [OpenAI Agents SDK](openai-agents.md) | `openai-agents` |
| [Frameworks with built-in OpenTelemetry](builtin-otel.md) | Pydantic AI, Google ADK, Strands, Microsoft Agent Framework, Semantic Kernel, Vercel AI SDK / Mastra (JavaScript) |
| [DSPy, smolagents, Agno, Qwen-Agent, model clients](more-frameworks.md) | Those frameworks, and raw OpenAI / Anthropic / Gemini / Bedrock / Portkey calls |
| [Plain OpenTelemetry](manual-otel.md) | Your own spans, other languages, no lighttrace package |

## Without the lighttrace package

The server's receiver is standard OpenTelemetry, so any exporter can send to it directly (other
languages, an existing OpenTelemetry setup, the zero-code `opentelemetry-instrument` launcher):

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:1984
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf   # lighttrace speaks OTLP over HTTP, not gRPC
export OTEL_EXPORTER_OTLP_HEADERS=x-api-key=<your key>
export OTEL_SERVICE_NAME=my-app
```

Instrumentations from OpenTelemetry (official GenAI ones), OpenInference, OpenLLMetry (`Traceloop.init(api_endpoint=..., headers={"x-api-key": ...})`),
OpenLIT, MLflow and the LangSmith SDK's OTel mode are understood as well.
