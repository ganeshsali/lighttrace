# Sending traces to lighttrace

lighttrace has no SDK of its own and **one receiver**: OpenTelemetry at `http://<host>:1984/v1/traces`.
Every framework below sends there, with libraries the app may already use:

| Page | For |
|---|---|
| [LangChain](langchain.md) | LangChain apps (the LangSmith SDK's OTel mode, no code change; or OpenInference) |
| [LangGraph](langgraph.md) | LangGraph agents (same two options) |
| [LlamaIndex](llamaindex.md) | LlamaIndex apps (OpenInference) |
| [CrewAI](crewai.md) | CrewAI crews (OpenInference) |
| [OpenAI Agents SDK](openai-agents.md) | `openai-agents` (OpenInference) |
| [Frameworks with built-in OpenTelemetry](builtin-otel.md) | Pydantic AI, Google ADK, Strands, Semantic Kernel, Vercel AI SDK / Mastra |
| [Plain OpenTelemetry](manual-otel.md) | Anything else: your own spans, any language |

Everything below needs a running lighttrace and an API key: open the project's **Keys** tab
(or **+ New project**) and create one. In the examples the server is `http://127.0.0.1:1984`.

## The OpenTelemetry settings every page uses

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:1984
export OTEL_EXPORTER_OTLP_HEADERS=x-api-key=<your key>
export OTEL_SERVICE_NAME=my-app        # names the project when you use an admin key
```

Use the HTTP protocol (the default in Python; set `OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf` where the
default is gRPC). With a project key, traces always land in that key's project. The standard OpenTelemetry exporter
reads these variables, so the Python setup is the same three lines everywhere:

```python
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

provider = TracerProvider()
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
```

Call `provider.shutdown()` before a short script exits, or the last spans are lost.

## OpenLLMetry: one alternative for all of them

[OpenLLMetry](https://github.com/traceloop/openllmetry) (Traceloop, Apache-2.0) instruments most
frameworks and LLM clients at once:

```bash
pip install traceloop-sdk
```

```python
from traceloop.sdk import Traceloop
Traceloop.init(app_name="my-app", api_endpoint="http://127.0.0.1:1984",
               headers={"x-api-key": "<your key>"}, telemetry_enabled=False)
```

It sends metrics too; lighttrace accepts and drops them.
