# lighttrace

Self-hosted tracing with a web UI for **AI apps and agents**. An app adds two lines:

```python
import lighttrace          # pip install lighttrace
lighttrace.register()
```

and every model call, tool call and agent step (with tokens, cost and time to first token) shows up in
lighttrace. `register()` turns on the right listener for whatever the app uses: LangChain, LangGraph,
LlamaIndex, CrewAI, OpenAI Agents, Pydantic AI, Google ADK, Strands, Microsoft Agent Framework, raw
OpenAI/Anthropic/Bedrock/Gemini clients and more — see [Supported frameworks](#supported-frameworks).
The server has **one receiver**, standard OpenTelemetry at `/v1/traces`, so anything else that speaks
OpenTelemetry (other languages, the Vercel AI SDK) can send too.

- The server is one Python process (FastAPI). Storage in **SQLite** (one file, default) or **Postgres**.
  ~50–100 MB RAM; runs comfortably on a 2 CPU / 4 GB box.
- Keep traces for N days or **forever** (`LIGHTTRACE_RETENTION_DAYS=0`).
- Ingest protected by API keys (`LIGHTTRACE_API_KEYS`), UI by a password. Binds `127.0.0.1` by default.
- No frontend build step: plain HTML/CSS/JS served by the same process.

lighttrace is an independent project, not affiliated with or endorsed by LangChain, Inc.
"LangSmith" is a trademark of LangChain, Inc.; it is named here only to say which SDK can send to lighttrace.

## What the UI does

**Projects** — every project with traces, threads, error rate, P50/P99 latency, tokens and cost
for a time window.

**Inside a project**, four tabs plus a stats sidebar (traces, threads, error rate, P50/P99,
tokens, median tokens per trace, cost):

| Tab | Shows |
|---|---|
| **Threads** | One row per conversation (`thread_id` / `session_id` / `conversation_id` metadata): first input, last output, turns, errors, P50/P99, tokens, cost |
| **Traces** | One row per root run: input/output previews, status, latency, time to first token, tokens, cost, tags. Tick two to **compare**, or several to delete |
| **Runs** | Every run (LLM calls, tools, chains, retrievers), filterable by run type |
| **Dashboard** | Charts over time: traces and errors, P50/P99 latency, tokens, cost; top tools, LLM models and chains by runs, errors and latency |

Filters: time range, status, full-text search over inputs/outputs/errors, name, run type, tag,
metadata `key:value` (several), latency ≥ / ≤, tokens ≥. Active filters show
as chips; any filter can be **saved** per project. Columns can be shown or hidden, and a **Live**
toggle refreshes every 10 seconds.

**A thread** opens on one page with three views (keys `T`, `U`, `D`):
- **Trajectory** — the whole conversation as chat across all turns (human, AI, tool calls with
  arguments, tool results), de-duplicated, with a per-turn line for latency, tokens, cost, model
  and links to the LLM call. Downloadable as Markdown.
- **Turns** — one card per turn with its input and output.
- **Details** — every turn listed with its run tree **collapsed**; open as many as you like. The
  selected turn starts expanded. Each tree has a waterfall, a filter box, expand/collapse, and
  arrow-key / `j` `k` navigation.

Opening any trace that belongs to a thread lands in that thread's Details view.

**The run panel** (right side): latency, time to first token, tokens in/out, cost, model, run id
with copy, and two tabs — **Run** (error with traceback; input and output as Pretty or raw JSON,
with copy; LLM messages as chat, retriever documents, tool args) and **Metadata** (metadata, tags,
invocation params, timing, ids, runtime, events).

Light and dark mode, persisted per browser.

## Point your app at it

```bash
pip install lighttrace
```

```python
import lighttrace
lighttrace.register()      # once at startup, before the app does any AI work
```

```
LIGHTTRACE_ENDPOINT=http://<lighttrace-host>:1984
LIGHTTRACE_API_KEY=<one of the server's LIGHTTRACE_API_KEYS>
LIGHTTRACE_PROJECT=myapp   # the project; created on its first trace
```

That's all: no special start command, and it works with `--reload`, gunicorn, Docker and scripts (what is
left is sent when the process exits). The settings can also be passed in code:
`lighttrace.register(project="myapp", endpoint=..., api_key=...)`. Without an endpoint and a key,
`register()` does nothing, so the same code runs untraced where they aren't set.

What `register()` does:

1. **Finds the frameworks** the app has installed.
2. **Turns on their listeners.** lighttrace writes none of its own: it uses OpenTelemetry's official GenAI
   instrumentations, or OpenInference's where the official one is missing or incomplete (LlamaIndex,
   CrewAI), and the framework's own OpenTelemetry where it has one (ADK, Strands, Microsoft Agent
   Framework, Semantic Kernel, Pydantic AI). Model clients (OpenAI, Anthropic, Bedrock, Google GenAI,
   Portkey) are traced unless the frameworks present already record their model calls, so a call is
   never recorded twice.
3. **Points one sender** at lighttrace with the key and project. Prompts, answers, tool arguments and
   results are included (`register(capture_content=False)` leaves them out).

If the app already set up OpenTelemetry, `register()` adds lighttrace as one more destination.
Threads: put `thread_id` (or `session_id` / `conversation_id`) in the run's metadata or LangGraph
config, e.g. `config={"configurable": {"thread_id": ...}}`.

**No Python, or no code change at all?** Any OpenTelemetry exporter can send to the receiver directly:
`OTEL_EXPORTER_OTLP_ENDPOINT=http://<host>:1984`, `OTEL_EXPORTER_OTLP_HEADERS=x-api-key=<key>`,
`OTEL_SERVICE_NAME=<project>` (see [docs/](docs/README.md)).

### The receiver

`POST /v1/traces` takes OTLP over HTTP, protobuf or JSON, optionally gzip. Next to it, as part of the
same OpenTelemetry standard, `POST /v1/logs` takes the prompt/response content that the official OTel
GenAI instrumentations send as log events, and `POST /v1/metrics` is accepted and dropped (so exporters
don't error). There is no other way in.

### API keys and projects

Only the keys listed in `LIGHTTRACE_API_KEYS` (comma-separated) are accepted; anything else gets 401.
Give each app or team its own key, and remove a key from the list (then restart) to shut that app out.

Projects need no setup. Each trace goes to the project named by `LIGHTTRACE_PROJECT` (with
`register()`; it is sent as the `x-lighttrace-project` header), else the app's `service.name`
(`OTEL_SERVICE_NAME`), else `default`; a new name creates the project on its first trace.

### Supported frameworks

Per-framework notes and runnable examples are in [docs/](docs/README.md). "End to end" means a test runs
the framework (fake model, or its real model class against a stub server: no LLM) through
`lighttrace.register()` against a live lighttrace; "real payload" means a test replays what the real
exporter sent; "documented" means set up from the vendor's docs only.

| Framework | Listener `register()` turns on | Status |
|---|---|---|
| [LangChain / LangGraph](docs/langgraph.md) | OpenTelemetry's official (records model calls, tokens, time to first token) | end to end; real payload |
| [LlamaIndex](docs/llamaindex.md) | OpenInference's (the official one records only retrievals today) | end to end |
| [OpenAI Agents SDK](docs/openai-agents.md) | official + the OpenAI client's (model calls) | end to end, incl. the real model class |
| [CrewAI](docs/crewai.md) | OpenInference's (no official one yet) | end to end (crewai 1.6.1) |
| [Pydantic AI](docs/builtin-otel.md#pydantic-ai) | its built-in OpenTelemetry | end to end; real payloads for 0.4, 2.x and Logfire |
| [Google ADK](docs/builtin-otel.md#google-adk) | its built-in OpenTelemetry | end to end (1.10); real payload (2.12) |
| [Strands Agents](docs/builtin-otel.md#strands-agents) | its built-in OpenTelemetry | documented |
| [Microsoft Agent Framework](docs/builtin-otel.md#microsoft-agent-framework) | its built-in OpenTelemetry | documented |
| [Semantic Kernel](docs/builtin-otel.md#semantic-kernel) | its built-in OpenTelemetry | documented |
| DSPy, smolagents, Agno, Qwen-Agent | official + the model client's | documented |
| OpenAI, Anthropic, Bedrock, Google GenAI, Portkey (no framework) | official client listeners | OpenAI: real payload |
| [Vercel AI SDK](docs/builtin-otel.md#vercel-ai-sdk-and-mastra-javascript) (JavaScript) | its built-in OpenTelemetry, sending to `/v1/traces` | real payload; example run by hand |
| [Your own spans, any language](docs/manual-otel.md) | plain OpenTelemetry + GenAI attributes | end to end |

Also understood when sent over OpenTelemetry: the LangSmith SDK's OTel mode (end to end), MLflow,
Langfuse SDK v3, OpenLLMetry and OpenLIT (real payloads).

### Supported transports & dialects

| Receiver (all on port 1984) | Status |
|---|---|
| OTLP/HTTP protobuf on `/v1/traces` (gzip ok) | tested with the real Python exporter; the JS exporter by hand |
| OTLP/HTTP JSON, ids hex (spec) or base64 | tested |
| OTLP `/v1/logs` (protobuf + JSON), GenAI events only | tested with real payloads |
| OTLP `/v1/metrics` | accepted and dropped (one warning) |

| Dialect (span attributes) | Mapped | Status |
|---|---|---|
| [OpenInference](https://github.com/Arize-ai/openinference) | span kind, `input.value`/`output.value`, `llm.input_messages`/`output_messages` incl. tool calls, tokens, model, invocation params, retrieval documents, embedding texts, `metadata`, `tag.tags`, `session.id` | real |
| [OTel GenAI](https://opentelemetry.io/docs/specs/semconv/gen-ai/) | `gen_ai.operation.name`, models, usage, `gen_ai.input/output.messages`, `gen_ai.system_instructions`, tool call arguments/result, `gen_ai.conversation.id` | real |
| OTel GenAI events | log or span events `gen_ai.system/user/assistant/tool.message`, `gen_ai.choice`, `gen_ai.client.inference.operation.details`, older `gen_ai.content.prompt`/`completion`; matched to their span whichever arrives first | real (log events); documented (span events) |
| OpenLLMetry | `traceloop.span.kind`, `traceloop.entity.input`/`output`, GenAI attributes, older `gen_ai.prompt.N` | real |
| OpenLIT | GenAI attributes + its operation-details log event | real |
| Vercel AI SDK / Mastra | `ai.operationId`, `ai.prompt(.messages)`, `ai.response.text`/`toolCalls`, `ai.usage.*`, `ai.model.id`, `ai.toolCall.*`, `ai.settings.*`, `ai.telemetry.metadata.*` | real |
| Langfuse SDK v3 | `langfuse.observation.*`, `langfuse.trace.*`, `session.id` | real |
| MLflow | `mlflow.spanType`, `mlflow.spanInputs`/`spanOutputs`, `mlflow.chat.tokenUsage` or OpenAI-style `usage` | real |
| LangSmith OTel mode | `langsmith.span.kind`, `langsmith.metadata.*`, `langsmith.span.tags`, JSON `gen_ai.prompt`/`completion`; an exception on a span counts as an error (the SDK marks failed roots OK) | real |
| Google ADK | `gcp.vertex.agent.llm_request`/`llm_response` as chat, `tool_call_args`/`tool_response` | real (1.10 and 2.12) |
| Pydantic AI / Logfire | GenAI attributes, `pydantic_ai.all_messages` + `final_result`, pre-1.0 `events`/`all_messages_events`; `logfire.json_schema` decodes JSON attributes | real |

Every other span attribute shows up as run metadata (so the `key:value` filter works on it), resource
attributes under Runtime, and an error status with its `exception` event becomes the run's error.
Threads group on a `thread_id` / `session_id` / `conversation_id` in `metadata`, or on `session.id` /
`gen_ai.conversation.id`. Agent spans (OpenInference `AGENT`, `gen_ai.operation.name` `invoke_agent` /
`create_agent`, Langfuse `agent`, OpenLLMetry `agent`, MLflow `AGENT`, Pydantic AI and ADK agent runs)
get their own **agent** run type.

**Several services in one trace** (an agent calling another agent over A2A, or an MCP server in its own
process): lighttrace keeps one trace per *(OTel trace id, `service.name`)*. Each service's part shows as its
own trace, and both carry the OTel trace id in their `otel.trace_id` metadata, so the filter
`otel.trace_id:<id>` finds them together. A span whose parent is in another process (OTLP remote-parent
flag) hangs under a root for its service's part; if that service is entered several times in one trace,
the root spans all of them. The remote parent's span id stays in the span's `otel.remote_parent_span_id`
metadata. Give each service its own `service.name` (`OTEL_SERVICE_NAME`).

## Run it

```bash
pip install "lighttrace[server]"            # the server (SQLite); from a checkout: pip install ".[server]"
pip install "lighttrace[server,postgres]"   # with Postgres support
cp .env.example .env                # then set LIGHTTRACE_API_KEYS and LIGHTTRACE_PASSWORD
lighttrace                          # http://127.0.0.1:1984  (--host, --port, --db override)
```

`lighttrace` reads `.env` from the current directory (variables already set in the shell win).
A good key: `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`.

Docker (the image includes Postgres support):

```bash
docker build -t lighttrace .
docker run -d --name lighttrace --restart unless-stopped \
  -p 127.0.0.1:1984:1984 -v lighttrace-data:/data --env-file .env \
  -e LIGHTTRACE_HOST=0.0.0.0 lighttrace
```

The image binds `0.0.0.0` inside the container and stores SQLite at `/data/lighttrace.db`;
`-p 127.0.0.1:…` keeps it private on the host.

### Postgres

```
LIGHTTRACE_DB=postgresql://lighttrace:<password>@<host>:5432/lighttrace
LIGHTTRACE_RETENTION_DAYS=0          # keep forever
```

Create an empty database and a user that owns it; lighttrace creates its tables on start. Any
Postgres 12+ should work, managed ones (Supabase, Neon, RDS) included; tests run against 16. Back up
with your provider's snapshots or `pg_dump`.

### Configuration

All config is environment variables — see [.env.example](.env.example).

| Variable | Default | |
|---|---|---|
| `LIGHTTRACE_API_KEYS` | — (required) | Comma-separated ingest keys; only these are accepted. Use one per app or team so you can remove one without touching the others |
| `LIGHTTRACE_PASSWORD` | — (required) | UI admin password. Changing it logs everyone out |
| `LIGHTTRACE_DB` | `lighttrace.db` | SQLite file path, or `postgresql://…` URL |
| `LIGHTTRACE_RETENTION_DAYS` | `30` | Delete traces older than this, checked hourly. `0` keeps forever |
| `LIGHTTRACE_PRICES` | — | Cost tracking: `{"model": [usd_per_1M_input, usd_per_1M_output]}` inline or a JSON file path. Exact or prefix match on model name |
| `LIGHTTRACE_HOST` / `LIGHTTRACE_PORT` | `127.0.0.1` / `1984` | Bind address |
| `LIGHTTRACE_COOKIE_SECURE` | `false` | Set `true` behind HTTPS |
| `LIGHTTRACE_MAX_BODY_MB` | `50` | Max ingest body (after gzip) |

Several apps or environments share one server as separate projects (e.g. `myapp-staging`,
`myapp-prod`). There is no project setup: a project appears with its first trace, named by the app's
`OTEL_SERVICE_NAME`.

## HTTPS with Caddy

Keep lighttrace on `127.0.0.1:1984` and let Caddy terminate TLS. With a domain:

```
# /etc/caddy/Caddyfile
traces.example.com {
    reverse_proxy 127.0.0.1:1984
}
```

No domain? [sslip.io](https://sslip.io) gives every IP a hostname, and Caddy gets a real
Let's Encrypt certificate for it. For a server at `203.0.113.7`:

```
traces.203-0-113-7.sslip.io {
    reverse_proxy 127.0.0.1:1984
}
```

Then set `LIGHTTRACE_COOKIE_SECURE=true` and point apps at
`OTEL_EXPORTER_OTLP_ENDPOINT=https://traces.203-0-113-7.sslip.io`. If the app runs on the same server,
`http://127.0.0.1:1984` is simpler and skips TLS.

## Retention and storage

With `LIGHTTRACE_RETENTION_DAYS` > 0, every hour lighttrace deletes whole traces whose root started
earlier than that. With `0` nothing is ever deleted; prefer Postgres then.

SQLite: the file stops growing once retention kicks in (freed pages are reused); run
`sqlite3 lighttrace.db 'VACUUM'` while stopped to give disk back, and back up with
`sqlite3 lighttrace.db ".backup backup.db"` (safe while running).

Run payloads are never logged — they usually hold user and business data.

## Limits

- **Tracing only, one receiver.** No feedback, datasets, experiments/evaluations, annotation queues,
  prompt hub or playground, and no LangSmith-style or vendor-specific ingest APIs: an app that still
  calls the old LangSmith API (`/runs/batch`, `/info`) gets 404 and the server logs, once, how to switch
  it to OpenTelemetry mode.
- **No public share links**, on purpose: a share link publishes every prompt and tool result to
  anyone holding it.
- **OTLP over HTTP only** (protobuf or JSON, gzip ok; no zstd). Exporters set to gRPC must be switched
  to `http/protobuf` (`OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf`). OTLP logs are read for GenAI content
  only; metrics are dropped. Traces produced while lighttrace is down are lost once the exporter's
  short retry gives up — exporters don't buffer to disk.
- Text search is a `LIKE`/`ILIKE` scan; percentiles and dashboards are computed over the selected
  time window in Python. Fine for hundreds of thousands of traces; at many millions, narrow the
  window or add database-side indexes (e.g. `pg_trgm`).
- Single admin password, no users/roles. Anyone who can log in sees every project. Any valid API key
  can write to any project.
- LangChain's OpenTelemetry mode verified with `langsmith` 0.14.x (what `langchain-core` 1.x installs).

## API

Ingest, the one receiver (key in `x-api-key` or `Authorization: Bearer <key>`): `POST /v1/traces`,
`POST /v1/logs`, `POST /v1/metrics` (accepted, dropped); OTLP/HTTP protobuf or JSON (ids hex or
base64).

UI JSON (session cookie; writes also need the `x-lighttrace: 1` header):
`GET /api/projects`, `GET /api/projects/{p}/runs?scope=traces|runs&…filters`,
`GET /api/projects/{p}/threads`, `GET /api/projects/{p}/thread?id=…`,
`GET /api/projects/{p}/stats`, `GET /api/projects/{p}/dashboard?since=&until=`,
`GET|POST /api/projects/{p}/filters`, `GET|DELETE /api/traces/{trace_id}`, `DELETE /api/projects/{p}`
(also deletes its keys), `GET|POST /api/projects/{p}/keys` (POST `{"name"}` returns the full key once),
`DELETE /api/keys/{id}`.
Filters: `since, until, status, q, name, run_type, tag, meta=key:value (repeatable), min_latency,
max_latency, min_tokens`.

## Development

```bash
python3.11 -m venv .venv && .venv/bin/pip install -e '.[server,test]'
.venv/bin/pytest            # includes end-to-end LangGraph tests (register() and the LangSmith SDK), no LLM calls
# framework end-to-end tests (tests/test_frameworks.py) skip unless the framework is installed, e.g.
.venv/bin/pip install llama-index-core openai-agents pydantic-ai-slim
LIGHTTRACE_TEST_PG=postgresql://user@127.0.0.1/lighttrace_test .venv/bin/pytest   # also on Postgres
# sample traces (fake model, 3 threads) into a running server:
LIGHTTRACE_ENDPOINT=http://127.0.0.1:1984 LIGHTTRACE_API_KEY=<key> LIGHTTRACE_PROJECT=demo \
  .venv/bin/python examples/langgraph_demo.py
```

The Postgres test database is wiped by the tests; never point it at real data.

Contributions welcome: see [CONTRIBUTING.md](CONTRIBUTING.md) (commits are signed off under the DCO),
[SECURITY.md](SECURITY.md) for reporting vulnerabilities, and the [Code of Conduct](CODE_OF_CONDUCT.md).

License: MIT.
