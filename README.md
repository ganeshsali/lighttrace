# lighttrace

A small, self-hosted tracing server with a web UI for **LangChain and LangGraph** apps.
It speaks enough of the LangSmith ingest API that the LangSmith SDK already inside your app
sends traces to it — **no code changes**, just environment variables.
It also accepts **OpenTelemetry (OTLP/HTTP)**, so apps on other frameworks or languages can send traces too.

- One Python process (FastAPI). Storage in **SQLite** (one file, default) or **Postgres**.
  ~50–100 MB RAM; runs comfortably on a 2 CPU / 4 GB box.
- Keep traces for N days or **forever** (`LIGHTTRACE_RETENTION_DAYS=0`).
- Ingest protected by API keys (per project, created in the UI), UI by a password. Binds `127.0.0.1` by default.
- No frontend build step: plain HTML/CSS/JS served by the same process.

lighttrace is an independent project, not affiliated with or endorsed by LangChain, Inc.
"LangSmith" is a trademark of LangChain, Inc.; it is named here only to say which SDK lighttrace works with.

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

In the app you want to trace (any LangChain / LangGraph app; it already has the `langsmith` SDK):

```
LANGSMITH_TRACING=true
LANGSMITH_ENDPOINT=http://<lighttrace-host>:1984
LANGSMITH_API_KEY=<a project key from the UI, or one of LIGHTTRACE_API_KEYS>
LANGSMITH_PROJECT=myapp-prod
```

### API keys

Each project has a **Keys** tab: create a key (it is shown once, copy it then), see when each key was
last used, revoke it. A project key can only write to its own project: whatever project name the app
sends (`LANGSMITH_PROJECT`, `x-lighttrace-project`, `service.name`) is ignored and its traces land in
the key's project. To start a new project, use **+ New project** on the projects page.
Only a SHA-256 hash and the first 10 characters of each key are stored.

Keys in `LIGHTTRACE_API_KEYS` are **admin keys**: they write to any project, chosen by the app as
before. Keep them for yourself; give each app or team a project key.

The root run id shown in the UI is the same id LangSmith would have given you, so code that stores
`run_id` keeps working. To group turns into threads, put `thread_id` (or `session_id` /
`conversation_id`) in the run's metadata, e.g. `config={"metadata": {"thread_id": ...}}`.

Some apps load `.env` in a way that never reaches the SDK (pydantic-settings, for one). If traces
do not arrive, export the variables in the shell, or have the app copy its own settings into
`os.environ`.

### Or send OpenTelemetry

Any app instrumented with OpenTelemetry can export to `POST /v1/traces` (OTLP over HTTP, protobuf or
JSON, optionally gzip). For example, LlamaIndex, CrewAI, the raw OpenAI SDK, or non-Python apps.
`POST /v1/logs` takes the prompt/response content that the official OTel GenAI instrumentations send as
log events, and `POST /v1/metrics` is accepted and dropped (so exporters don't error). OTLP over **gRPC**
works too: `pip install 'lighttrace[grpc]'` and set `LIGHTTRACE_GRPC_PORT=4317`.

```
OTEL_EXPORTER_OTLP_ENDPOINT=http://<lighttrace-host>:1984
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
OTEL_EXPORTER_OTLP_HEADERS=x-api-key=<project key>
```

With a project key the project is the key's. With an admin key it comes from the
`x-lighttrace-project` header, else the `service.name` resource attribute (`OTEL_SERVICE_NAME`), else
`default`. Spans are understood in these conventions:

| Convention (typical instrumentor) | Mapped |
|---|---|
| [OpenInference](https://github.com/Arize-ai/openinference) (Phoenix) | span kind, `input.value`/`output.value`, `llm.input_messages`/`llm.output_messages` incl. tool calls, token counts, model, invocation params, `metadata`, `tag.tags`, `session.id` |
| [OTel GenAI](https://opentelemetry.io/docs/specs/semconv/gen-ai/) | `gen_ai.operation.name`, models, usage, `gen_ai.input.messages`/`gen_ai.output.messages`, `gen_ai.system_instructions`, tool call arguments/result, `gen_ai.conversation.id` |
| OpenLLMetry (Traceloop) | `traceloop.span.kind`, `traceloop.entity.input`/`output`, `gen_ai.prompt.N`/`gen_ai.completion.N` |
| Vercel AI SDK / Mastra (`experimental_telemetry`) | `ai.operationId` (model call → llm, `ai.toolCall` → tool), `ai.prompt`, `ai.prompt.messages`, `ai.response.text`/`toolCalls`, `ai.usage.*`, `ai.model.id`, `ai.toolCall.*`, `ai.settings.*`, `ai.telemetry.metadata.*` |
| Langfuse SDK v3 | `langfuse.observation.type`/`input`/`output`/`model.name`/`model.parameters`/`usage_details`/`level`/`metadata.*`, `langfuse.trace.tags`/`input`/`output`/`metadata.*`, `session.id` |
| OTel GenAI events | log events or span events `gen_ai.system/user/assistant/tool.message`, `gen_ai.choice`, `gen_ai.client.inference.operation.details`, and the older `gen_ai.content.prompt`/`completion`, matched to their span by trace/span id whichever arrives first |
| MLflow | `mlflow.spanType`, `mlflow.spanInputs`/`spanOutputs`, `mlflow.chat.tokenUsage` (or OpenAI-style `usage` in the output) |
| LangSmith SDK OTel mode | `langsmith.span.kind`, `langsmith.metadata.*`, `langsmith.span.tags`, `gen_ai.prompt`/`gen_ai.completion` JSON |
| Google ADK | `gcp.vertex.agent.llm_request`/`llm_response` (as chat messages), `tool_call_args`/`tool_response` |
| Pydantic AI / Logfire | GenAI attributes, `pydantic_ai.all_messages` + `final_result` on the agent run, pre-1.0 `events`/`all_messages_events`; `logfire.json_schema` decodes JSON-valued attributes, other `logfire.*` stay as metadata |
| OpenLIT | GenAI attributes and its operation-details log event |

Every other span attribute shows up as run metadata (so the `key:value` filter works on it), resource
attributes under Runtime, and an error status with its `exception` event becomes the run's error.
Threads group on a `thread_id` / `session_id` / `conversation_id` in `metadata`, or on `session.id` /
`gen_ai.conversation.id`. Agent spans (OpenInference `AGENT`, `gen_ai.operation.name` `invoke_agent` /
`create_agent`, Langfuse `agent`, OpenLLMetry `agent`) get their own **agent** run type.

**Several services in one trace** (an agent calling another agent over A2A, or an MCP server in its own
process): lighttrace keeps one trace per *(OTel trace id, `service.name`)*. Each service's part shows as its
own trace, and both carry the OTel trace id in their `otel.trace_id` metadata, so the filter
`otel.trace_id:<id>` finds them together. A span whose parent is in another process (OTLP remote-parent
flag) hangs under a root for its service's part; if that service is entered several times in one trace,
the root spans all of them. The remote parent's span id stays in the span's `otel.remote_parent_span_id`
metadata. Give each service its own `service.name` (`OTEL_SERVICE_NAME`).

**Langfuse SDK v3**: no code change, point it here: `LANGFUSE_HOST=http://<lighttrace-host>:1984`,
`LANGFUSE_SECRET_KEY=<lighttrace key>`, `LANGFUSE_PUBLIC_KEY=<anything>`. lighttrace accepts its
`/api/public/otel/v1/traces` endpoint and Basic auth (the secret key is checked as the API key).

For a LangChain app, the LangSmith route above gives richer traces; OTel is for everything else.

## Run it

```bash
pip install .                       # SQLite only
pip install '.[postgres]'           # with Postgres support
cp .env.example .env                # then set LIGHTTRACE_PASSWORD
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
| `LIGHTTRACE_API_KEYS` | — | Comma-separated **admin** ingest keys: may write to any project. Optional; per-project keys are created in the UI |
| `LIGHTTRACE_PASSWORD` | — (required) | UI admin password. Changing it logs everyone out |
| `LIGHTTRACE_DB` | `lighttrace.db` | SQLite file path, or `postgresql://…` URL |
| `LIGHTTRACE_RETENTION_DAYS` | `30` | Delete traces older than this, checked hourly. `0` keeps forever |
| `LIGHTTRACE_PRICES` | — | Cost tracking: `{"model": [usd_per_1M_input, usd_per_1M_output]}` inline or a JSON file path. Exact or prefix match on model name |
| `LIGHTTRACE_HOST` / `LIGHTTRACE_PORT` | `127.0.0.1` / `1984` | Bind address |
| `LIGHTTRACE_GRPC_PORT` | `0` (off) | OTLP/gRPC port, usually `4317`. Needs `pip install 'lighttrace[grpc]'` |
| `LIGHTTRACE_COOKIE_SECURE` | `false` | Set `true` behind HTTPS |
| `LIGHTTRACE_MAX_BODY_MB` | `50` | Max ingest body (after gzip) |

Several apps or environments share one server as separate projects (e.g. `myapp-staging`,
`myapp-prod`), each with its own keys. With an admin key, projects are created on first trace.

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
`LANGSMITH_ENDPOINT=https://traces.203-0-113-7.sslip.io`. If the app runs on the same server,
`http://127.0.0.1:1984` is simpler and skips TLS.

## Retention and storage

With `LIGHTTRACE_RETENTION_DAYS` > 0, every hour lighttrace deletes whole traces whose root started
earlier than that. With `0` nothing is ever deleted; prefer Postgres then.

SQLite: the file stops growing once retention kicks in (freed pages are reused); run
`sqlite3 lighttrace.db 'VACUUM'` while stopped to give disk back, and back up with
`sqlite3 lighttrace.db ".backup backup.db"` (safe while running).

Run payloads are never logged — they usually hold user and business data.

## Limits

- **Tracing only.** No feedback, datasets, experiments/evaluations, annotation queues, prompt hub
  or playground. Other LangSmith endpoints return 404 and log one warning per endpoint.
- **No public share links**, on purpose: a share link publishes every prompt and tool result to
  anyone holding it.
- **No multipart or zstd ingest.** `/info` tells the SDK to use JSON `/runs/batch`; `/runs/multipart`
  returns 404, which makes the SDK fall back. gzip bodies are accepted. If lighttrace is down when
  your app starts, the SDK tries multipart first, gets the 404 once the server is back and switches
  to `/runs/batch` by itself (verified). Traces produced while the server is down are lost — the SDK
  does not buffer to disk.
- **Traces only.** OTLP logs are read for GenAI content only (other log records are dropped);
  metrics are dropped. gRPC needs the `grpc` extra and its own port; it has no TLS of its own, so
  keep it on a private network or behind a TLS proxy.
- Text search is a `LIKE`/`ILIKE` scan; percentiles and dashboards are computed over the selected
  time window in Python. Fine for hundreds of thousands of traces; at many millions, narrow the
  window or add database-side indexes (e.g. `pg_trgm`).
- Single admin password, no users/roles. Anyone who can log in sees every project and can create keys.
- Verified against `langsmith` 0.14.x (what `langchain-core` 1.x installs).

## API

Ingest (LangSmith-compatible, `x-api-key`): `GET /info`, `POST /runs/batch`, `POST /runs`,
`PATCH /runs/{id}`. A patch may arrive before its post; both merge into one row. With a project key,
runs go into the key's project, and runs that already belong to another project are left alone.

OpenTelemetry (`x-api-key`, `Authorization: Bearer <key>`, or Basic with the key as password):
`POST /v1/traces` (also at `/api/public/otel/v1/traces` for the Langfuse SDK), `POST /v1/logs`,
`POST /v1/metrics` (accepted, dropped); OTLP/HTTP protobuf or JSON (ids hex or base64). On
`LIGHTTRACE_GRPC_PORT`: the OTLP/gRPC `TraceService`, `LogsService` and `MetricsService` with the key in
`x-api-key` or `authorization` metadata.

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
python3.11 -m venv .venv && .venv/bin/pip install -e '.[test]'
.venv/bin/pytest            # includes an end-to-end LangGraph + real SDK test, no LLM calls
LIGHTTRACE_TEST_PG=postgresql://user@127.0.0.1/lighttrace_test .venv/bin/pytest   # also on Postgres
# sample traces (fake model, 3 threads) into a running server:
LANGSMITH_TRACING=true LANGSMITH_ENDPOINT=http://127.0.0.1:1984 LANGSMITH_API_KEY=<key> \
  LANGSMITH_PROJECT=demo .venv/bin/python examples/demo_app.py
```

The Postgres test database is wiped by the tests; never point it at real data.

License: MIT.
