# lighttrace

A small, self-hosted tracing server with a web UI for **LangChain and LangGraph** apps.
It speaks enough of the LangSmith ingest API that the LangSmith SDK already inside your app
sends traces to it — **no code changes**, just environment variables.

- One Python process (FastAPI). Storage in **SQLite** (one file, default) or **Postgres**.
  ~50–100 MB RAM; runs comfortably on a 2 CPU / 4 GB box.
- Keep traces for N days or **forever** (`LIGHTTRACE_RETENTION_DAYS=0`).
- Ingest protected by API keys, UI by a password. Binds `127.0.0.1` by default.
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
LANGSMITH_API_KEY=<one of LIGHTTRACE_API_KEYS>
LANGSMITH_PROJECT=myapp-prod
```

The root run id shown in the UI is the same id LangSmith would have given you, so code that stores
`run_id` keeps working. To group turns into threads, put `thread_id` (or `session_id` /
`conversation_id`) in the run's metadata, e.g. `config={"metadata": {"thread_id": ...}}`.

Some apps load `.env` in a way that never reaches the SDK (pydantic-settings, for one). If traces
do not arrive, export the variables in the shell, or have the app copy its own settings into
`os.environ`.

## Run it

```bash
pip install .                       # SQLite only
pip install '.[postgres]'           # with Postgres support
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
| `LIGHTTRACE_API_KEYS` | — (required) | Comma-separated ingest keys, checked against the SDK's `x-api-key` header. Use one per app/environment so you can revoke one |
| `LIGHTTRACE_PASSWORD` | — (required) | UI admin password. Changing it logs everyone out |
| `LIGHTTRACE_DB` | `lighttrace.db` | SQLite file path, or `postgresql://…` URL |
| `LIGHTTRACE_RETENTION_DAYS` | `30` | Delete traces older than this, checked hourly. `0` keeps forever |
| `LIGHTTRACE_PRICES` | — | Cost tracking: `{"model": [usd_per_1M_input, usd_per_1M_output]}` inline or a JSON file path. Exact or prefix match on model name |
| `LIGHTTRACE_HOST` / `LIGHTTRACE_PORT` | `127.0.0.1` / `1984` | Bind address |
| `LIGHTTRACE_COOKIE_SECURE` | `false` | Set `true` behind HTTPS |
| `LIGHTTRACE_MAX_BODY_MB` | `50` | Max ingest body (after gzip) |

Several apps or environments share one server by using different `LANGSMITH_PROJECT` names
(e.g. `myapp-staging`, `myapp-prod`); projects are created on first trace.

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
- Text search is a `LIKE`/`ILIKE` scan; percentiles and dashboards are computed over the selected
  time window in Python. Fine for hundreds of thousands of traces; at many millions, narrow the
  window or add database-side indexes (e.g. `pg_trgm`).
- Single admin password, no users/roles. Anyone who can log in sees every project.
- Verified against `langsmith` 0.14.x (what `langchain-core` 1.x installs).

## API

Ingest (LangSmith-compatible, `x-api-key`): `GET /info`, `POST /runs/batch`, `POST /runs`,
`PATCH /runs/{id}`. A patch may arrive before its post; both merge into one row.

UI JSON (session cookie; writes also need the `x-lighttrace: 1` header):
`GET /api/projects`, `GET /api/projects/{p}/runs?scope=traces|runs&…filters`,
`GET /api/projects/{p}/threads`, `GET /api/projects/{p}/thread?id=…`,
`GET /api/projects/{p}/stats`, `GET /api/projects/{p}/dashboard?since=&until=`,
`GET|POST /api/projects/{p}/filters`, `GET|DELETE /api/traces/{trace_id}`, `DELETE /api/projects/{p}`.
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
