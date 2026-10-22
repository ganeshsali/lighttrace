"""Storage. Everything that touches SQL lives here.

Two backends behind one small connection wrapper: SQLite (a file path) and
Postgres (a postgresql:// URL, needs `pip install lighttrace[postgres]`).
SQL is written once with `?` placeholders; the few dialect differences are
the helpers on `_DB`.
"""

import json
import math
import re
import sqlite3
import statistics
import threading
import time
import uuid
from contextlib import contextmanager, suppress

# Metadata keys that group traces into a thread; the first one set wins.
THREAD_KEYS = ("thread_id", "session_id", "conversation_id")
META_KEY_RE = re.compile(r"^[\w.\-]{1,100}$")

COLUMNS = {  # name -> type; {F} is a float column (DOUBLE PRECISION on Postgres)
    "id": "TEXT PRIMARY KEY", "project": "TEXT", "trace_id": "TEXT", "parent_run_id": "TEXT",
    "is_root": "INTEGER NOT NULL DEFAULT 0", "name": "TEXT", "run_type": "TEXT",
    "status": "TEXT NOT NULL DEFAULT 'pending'", "start_time": "{F}", "end_time": "{F}", "latency": "{F}",
    "first_token_time": "{F}", "error": "TEXT", "inputs": "TEXT", "outputs": "TEXT", "extra": "TEXT",
    "tags": "TEXT", "events": "TEXT", "input_tokens": "INTEGER", "output_tokens": "INTEGER",
    "total_tokens": "INTEGER", "model": "TEXT", "thread_id": "TEXT",
}
SCHEMA = [
    "CREATE TABLE IF NOT EXISTS projects (name TEXT PRIMARY KEY, created_at {F} NOT NULL, last_activity {F} NOT NULL)",
    "CREATE TABLE IF NOT EXISTS runs (" + ", ".join(f"{k} {v}" for k, v in COLUMNS.items()) + ")",
    "CREATE TABLE IF NOT EXISTS saved_filters (id TEXT PRIMARY KEY, project TEXT NOT NULL, name TEXT NOT NULL, "
    "query TEXT NOT NULL, created_at {F} NOT NULL)",
]
INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_runs_project_root ON runs(project, is_root, start_time)",
    "CREATE INDEX IF NOT EXISTS ix_runs_project_start ON runs(project, start_time)",
    "CREATE INDEX IF NOT EXISTS ix_runs_trace ON runs(trace_id)",
    "CREATE INDEX IF NOT EXISTS ix_runs_parent ON runs(parent_run_id)",
    "CREATE INDEX IF NOT EXISTS ix_runs_start ON runs(start_time)",
    "CREATE INDEX IF NOT EXISTS ix_runs_thread ON runs(project, thread_id, start_time)",
    "CREATE INDEX IF NOT EXISTS ix_runs_type_name ON runs(project, run_type, name)",
]
JSON_COLS = ("inputs", "outputs", "extra", "tags", "events")
SIMPLE_COLS = ("trace_id", "parent_run_id", "name", "run_type", "error")
# A model call is often traced twice: by the framework (LangChain's chat-model span) and, inside it,
# by the client library (the OpenAI SDK's span). Only the outer one counts for tokens, cost and the
# dashboard; both stay visible in the trace. A parent id that no row has is the root span's (see
# _tree_order), so then the parent is the trace's root row.
NESTED_LLM = ("{t}.run_type = 'llm' AND EXISTS (SELECT 1 FROM runs p WHERE p.run_type = 'llm' AND "
              "(p.id = {t}.parent_run_id OR (p.id = {t}.trace_id AND p.id <> {t}.id AND "
              "NOT EXISTS (SELECT 1 FROM runs q WHERE q.id = {t}.parent_run_id))))")


def _qs(values):
    """`?, ?, ...` placeholders for an IN list."""
    return ",".join("?" * len(values))


def _llm(t="runs"):
    return f"{t}.run_type = 'llm' AND NOT ({NESTED_LLM.format(t=t)})"


# Streamed-token markers: LangChain's own, OpenInference's first chunk, OpenLLMetry's per-chunk event.
TOKEN_EVENTS = ("new_token", "First Token Stream Event", "gen_ai.content.completion.chunk")

LIST_COLS = ("id, trace_id, parent_run_id, name, run_type, status, start_time, end_time, latency, first_token_time, "
             "error, inputs, outputs, extra, tags, input_tokens, output_tokens, total_tokens, model, thread_id")


# ---------------------------------------------------------------- helpers

def _model(extra):
    params = extra.get("invocation_params") or {}
    meta = extra.get("metadata") or {}  # ls_model_name: LangChain's own run metadata for chat models
    for v in (params.get("model"), params.get("model_name"), params.get("model_id"), params.get("deployment_name"),
              meta.get("ls_model_name")):
        if v:
            return str(v)
    return None


def _thread_id(extra):
    meta = (extra or {}).get("metadata") or {}
    for k in THREAD_KEYS:
        if meta.get(k) not in (None, ""):
            return str(meta[k])
    return None


def _first_token(events, start):
    if not isinstance(events, list) or start is None:
        return None
    times = []
    for e in events:
        if isinstance(e, dict) and e.get("name") in TOKEN_EVENTS and e.get("time"):
            with suppress(TypeError, ValueError):
                times.append(float(e["time"]))
    return min(times) - start if times else None


def preview(value, limit=200):
    """Short human text for a run's inputs/outputs in lists."""
    v = value
    for _ in range(5):
        if isinstance(v, dict):
            if "lc" in v and isinstance(v.get("kwargs"), dict):
                v = v["kwargs"]
                continue
            for key in ("messages", "input", "question", "query", "output", "answer", "content", "text", "result"):
                if key in v and v[key] not in (None, "", []):
                    v = v[key]
                    break
            else:
                break
        elif isinstance(v, list) and v:
            v = v[-1]
            if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str):  # ("human", "hi") tuples
                v = v[1]
        else:
            break
    if isinstance(v, dict) and "content" in v:
        v = v["content"]
    if isinstance(v, list):  # content blocks
        v = " ".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in v) or json.dumps(v, default=str)
    if v is None or v == {} or v == []:
        return ""
    if not isinstance(v, str):
        v = json.dumps(v, ensure_ascii=False, default=str)
    v = " ".join(v.split())
    return v if len(v) <= limit else v[: limit - 1] + "…"


def _tree_order(runs, root):
    """Runs in depth-first order, siblings by start. The root span's children point at an id no row
    has (see otel.py): those hang under the root."""
    ids = {r["id"] for r in runs}
    if root["is_root"]:
        for r in runs:
            if r is not root and r["parent_run_id"] not in ids:
                r["parent_run_id"] = root["id"]
    kids = {}
    for r in runs:  # already sorted by start_time
        kids.setdefault(r["parent_run_id"], []).append(r)
    order, seen = [], set()
    stack = [r for r in runs if r["parent_run_id"] not in ids][::-1]
    while stack:
        r = stack.pop()
        if r["id"] in seen:
            continue
        seen.add(r["id"])
        order.append(r)
        stack += kids.get(r["id"], [])[::-1]
    return order + [r for r in runs if r["id"] not in seen]  # parent cycles: bad data, still shown


def percentile(values, p):
    """Nearest-rank percentile; None for no values."""
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    k = max(0, min(len(vals) - 1, math.ceil(p / 100 * len(vals)) - 1))
    return vals[k]


class Prices:
    """Model -> (USD per 1M input tokens, USD per 1M output tokens). Exact or longest-prefix match."""

    def __init__(self, table=None):
        self.table = {k.lower(): (float(v[0]), float(v[1])) for k, v in (table or {}).items()}

    def cost(self, model, i, o):
        if not model or not self.table:
            return None
        m = model.lower()
        key = m if m in self.table else max((k for k in self.table if m.startswith(k)), key=len, default=None)
        if key is None:
            return None
        pi, po = self.table[key]
        return ((i or 0) * pi + (o or 0) * po) / 1_000_000


# ---------------------------------------------------------------- connection

class _DB:
    def __init__(self, target):
        self.target = target
        self.pg = target.startswith(("postgres://", "postgresql://"))
        # ponytail: one connection behind one lock; a pool if concurrent UI users ever contend.
        self.lock = threading.RLock()
        self._connect()

    def _connect(self):
        if self.pg:
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError:
                raise SystemExit("lighttrace: Postgres needs psycopg: pip install 'lighttrace[postgres]'") from None
            self.conn = psycopg.connect(self.target, autocommit=True, row_factory=dict_row)
        else:
            self.conn = sqlite3.connect(self.target, check_same_thread=False, isolation_level=None)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
        self.in_tx = False

    def _sql(self, sql):
        if self.pg:
            return sql.replace("?", "%s").replace("{F}", "DOUBLE PRECISION").replace("LIKE ", "ILIKE ")
        return sql.replace("{F}", "REAL")

    def execute(self, sql, args=()):
        with self.lock:
            try:
                return self.conn.execute(self._sql(sql), list(args))
            except Exception as e:
                # Reconnect once if Postgres dropped the connection (restart, idle timeout).
                if self.pg and not self.in_tx and type(e).__name__ in ("OperationalError", "InterfaceError"):
                    self._connect()
                    return self.conn.execute(self._sql(sql), list(args))
                raise

    def q(self, sql, args=()):
        cur = self.execute(sql, args)
        return [dict(r) for r in cur.fetchall()] if cur.description else []

    @contextmanager
    def tx(self):
        with self.lock:
            self.in_tx = True
            try:
                if self.pg:
                    with self.conn.transaction():
                        yield
                else:
                    self.conn.execute("BEGIN")
                    try:
                        yield
                        self.conn.execute("COMMIT")
                    except BaseException:
                        self.conn.execute("ROLLBACK")
                        raise
            finally:
                self.in_tx = False

    def meta_expr(self, key):
        """SQL expression for extra.metadata.<key> as text, plus its parameter."""
        if self.pg:
            return "(r.extra::jsonb #>> ?::text[])", "{metadata," + key + "}"
        return "CAST(json_extract(r.extra, ?) AS TEXT)", f'$.metadata."{key}"'


# ---------------------------------------------------------------- store

class Store:
    def __init__(self, target, prices=None):
        self.db = _DB(target)
        self.prices = prices or Prices()
        for stmt in SCHEMA:
            self.db.execute(stmt)
        for stmt in INDEXES:
            self.db.execute(stmt)

    # ---------- ingest ----------

    def upsert_runs(self, runs):
        """Insert or merge runs. Full runs and partial updates merge the same way: non-null
        incoming fields win and extra is merged one level deep, so an update that
        arrives before its run is fine."""
        now = time.time()
        with self.db.tx():
            for run in runs:
                self._upsert(run, now)

    def _upsert(self, run, now):
        run_id = str(run["id"])
        rows = self.db.q("SELECT * FROM runs WHERE id = ?", (run_id,))
        cur = dict.fromkeys(COLUMNS)
        cur.update(rows[0] if rows else {"id": run_id})
        for col in JSON_COLS:
            cur[col] = json.loads(cur[col]) if cur.get(col) else None
        if run.get("stand_in") and rows:  # an OTel stand-in root (otel.py): fill gaps, widen its time window
            run = {k: v for k, v in run.items() if cur.get(k) is None or k in ("start_time", "end_time", "extra")}
            for col, pick in (("start_time", min), ("end_time", max)):
                if run.get(col) is not None and cur[col] is not None:
                    run[col] = pick(run[col], cur[col])
        if run.get("absorb") and run["absorb"] != run_id:  # an OTel root span: take its early log messages
            early = self.db.q("SELECT inputs, outputs, extra FROM runs WHERE id = ? AND project IS NULL",
                              (run["absorb"],))
            if early:
                self.db.execute("DELETE FROM runs WHERE id = ?", (run["absorb"],))
                run = {**run, "extra": {**(run.get("extra") or {}), **json.loads(early[0]["extra"] or "{}")}}
                for col in ("inputs", "outputs"):
                    if run.get(col) is None and cur[col] is None and early[0][col]:
                        run[col] = json.loads(early[0][col])

        for col in SIMPLE_COLS:
            if run.get(col) is not None:
                cur[col] = str(run[col])
        for col in ("start_time", "end_time"):
            if run.get(col) is not None:
                cur[col] = float(run[col])
        for col in ("inputs", "outputs", "tags", "events"):
            if run.get(col) is not None:
                cur[col] = run[col]
        if isinstance(run.get("extra"), dict):
            extra = cur["extra"] or {}
            for k, v in run["extra"].items():
                extra[k] = {**extra[k], **v} if isinstance(v, dict) and isinstance(extra.get(k), dict) else v
            cur["extra"] = extra
        if run.get("project"):
            cur["project"] = run["project"]

        if not cur.get("trace_id") and not cur.get("parent_run_id"):
            cur["trace_id"] = run_id
        cur["is_root"] = int(cur.get("trace_id") == run_id)
        cur["status"] = "error" if cur.get("error") else ("success" if cur.get("end_time") else "pending")
        start, end = cur.get("start_time"), cur.get("end_time")
        cur["latency"] = end - start if start is not None and end is not None else None
        ftt = _first_token(cur["events"], start)  # from token events, else as sent (OTel attribute)
        cur["first_token_time"] = ftt if ftt is not None else run.get("first_token_time", cur.get("first_token_time"))
        cur["thread_id"] = _thread_id(cur["extra"])
        if cur.get("run_type") == "llm":
            for col in ("input_tokens", "output_tokens", "total_tokens"):
                if run.get(col) is not None:
                    cur[col] = run[col]
            cur["model"] = _model(cur["extra"] or {})

        for col in JSON_COLS:
            cur[col] = json.dumps(cur[col], ensure_ascii=False, default=str) if cur[col] is not None else None
        cols = list(COLUMNS)
        self.db.execute(
            f"INSERT INTO runs ({','.join(cols)}) VALUES ({','.join('?' * len(cols))}) "
            f"ON CONFLICT (id) DO UPDATE SET {', '.join(f'{c} = excluded.{c}' for c in cols[1:])}",
            [cur[c] for c in cols],
        )
        if cur.get("project"):
            self.db.execute(
                "INSERT INTO projects (name, created_at, last_activity) VALUES (?, ?, ?) "
                "ON CONFLICT (name) DO UPDATE SET last_activity = excluded.last_activity",
                (cur["project"], now, now),
            )

    def attach_messages(self, items):
        """Messages from OTel GenAI log events (otel.log_messages) onto their span's run. The span may
        not have arrived yet: then they wait in a project-less row that the span's upsert fills in
        (a root span absorbs it, see _upsert). Content the span carried itself is never replaced."""
        now = time.time()
        with self.db.tx():
            for it in items:
                rows = {r["id"]: r for r in self.db.q(
                    "SELECT id, project, inputs, outputs, extra FROM runs WHERE id IN (?, ?)", (it["id"], it["root"]))}
                row, root = rows.get(it["id"]), rows.get(it["root"])
                if not row and root and json.loads(root["extra"] or "{}").get("metadata", {}).get(
                        "otel.span_id") == it["span"]:
                    row = root
                extra = json.loads(row["extra"] or "{}") if row else {}
                done = extra.get("log_messages") or []
                cur = json.loads(row[it["side"]] or "null") if row else None
                if cur is not None and it["side"] not in done:
                    continue
                upd = {"id": row["id"] if row else it["id"], "trace_id": it["root"],
                       it["side"]: {"messages": (cur or {}).get("messages", []) + it["messages"]},
                       "extra": {"log_messages": sorted({*done, it["side"]})}}
                self._upsert(upd if row else {**upd, "start_time": now}, now)

    # ---------- filters ----------

    def _where(self, project, f, scope):
        """WHERE clause over `runs r` for the UI's filter dict. scope: traces | runs."""
        where, args = ["r.project = ?"], [project]
        if scope == "traces":
            where.append("r.is_root = 1")
        if f.get("since") is not None:
            where.append("r.start_time >= ?")
            args.append(float(f["since"]))
        if f.get("until") is not None:
            where.append("r.start_time < ?")
            args.append(float(f["until"]))
        if f.get("status"):
            where.append("r.status = ?")
            args.append(f["status"])
        if f.get("run_type"):
            where.append("r.run_type = ?")
            args.append(f["run_type"])
        if f.get("name"):
            where.append("r.name LIKE ?")
            args.append(f"%{f['name']}%")
        if f.get("q"):
            for term in str(f["q"]).split():
                where.append("(r.inputs LIKE ? OR r.outputs LIKE ? OR r.error LIKE ?)")
                args += [f"%{term}%"] * 3
        if f.get("tag"):
            where.append("r.tags LIKE ?")
            args.append("%" + json.dumps(str(f["tag"])) + "%")
        if f.get("thread_id"):
            where.append("r.thread_id = ?")
            args.append(f["thread_id"])
        for key, value in f.get("meta") or []:
            expr, arg = self.db.meta_expr(key)
            where.append(f"{expr} = ?")
            args += [arg, value]
        if f.get("min_latency") is not None:
            where.append("r.latency >= ?")
            args.append(float(f["min_latency"]))
        if f.get("max_latency") is not None:
            where.append("r.latency <= ?")
            args.append(float(f["max_latency"]))
        if f.get("min_tokens") is not None:
            if scope == "traces":
                where.append("(SELECT COALESCE(SUM(c.total_tokens), 0) FROM runs c "
                             f"WHERE c.trace_id = r.id AND {_llm('c')}) >= ?")
            else:
                where.append("COALESCE(r.total_tokens, 0) >= ?")
            args.append(int(f["min_tokens"]))
        return " AND ".join(where), args

    # ---------- page enrichment ----------

    def _llm_usage(self, trace_ids):
        """trace_id -> {tokens: (i, o, t), cost} summed over its llm runs."""
        out = {}
        if not trace_ids:
            return out
        rows = self.db.q(
            "SELECT trace_id, model, SUM(input_tokens) AS i, SUM(output_tokens) AS o, SUM(total_tokens) AS t "
            f"FROM runs WHERE trace_id IN ({_qs(trace_ids)}) AND {_llm()} "
            "GROUP BY trace_id, model", trace_ids)
        for r in rows:
            u = out.setdefault(r["trace_id"], {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost": None})
            u["input_tokens"] += r["i"] or 0
            u["output_tokens"] += r["o"] or 0
            u["total_tokens"] += r["t"] or 0
            c = self.prices.cost(r["model"], r["i"], r["o"])
            if c is not None:
                u["cost"] = (u["cost"] or 0) + c
        return out

    def _first_tokens(self, roots):
        """A root (graph/chain) does not stream itself: its time to first token is the
        first token of any streamed LLM run inside the trace, measured from the root's start."""
        need = [r for r in roots if r.get("first_token_time") is None and r.get("start_time") is not None]
        if not need:
            return
        ids = [r["id"] for r in need]
        first = {row["trace_id"]: row["t"] for row in self.db.q(
            "SELECT trace_id, MIN(start_time + first_token_time) AS t FROM runs "
            f"WHERE trace_id IN ({_qs(ids)}) AND first_token_time IS NOT NULL GROUP BY trace_id", ids)}
        for r in need:
            if first.get(r["id"]) is not None:
                r["first_token_time"] = max(0.0, first[r["id"]] - r["start_time"])

    def _list_item(self, r):
        extra = json.loads(r.pop("extra") or "{}")
        inputs = json.loads(r.pop("inputs") or "null")
        outputs = json.loads(r.pop("outputs") or "null")
        r["input_preview"] = preview(inputs)
        r["output_preview"] = preview(outputs) if outputs is not None else ""
        r["metadata"] = extra.get("metadata") or {}
        r["tags"] = json.loads(r["tags"] or "[]")
        return r

    # ---------- reads ----------

    def projects(self, since):
        rows = self.db.q(
            "SELECT p.name, p.created_at, p.last_activity, "
            "(SELECT COUNT(*) FROM runs r WHERE r.project = p.name AND r.is_root = 1) AS trace_count "
            "FROM projects p ORDER BY p.last_activity DESC")
        for p in rows:
            p["stats"] = self.stats(p["name"], since=since)
        return rows

    def runs(self, project, f, scope="traces", limit=50, offset=0):
        where, args = self._where(project, f, scope)
        rows = self.db.q(f"SELECT {LIST_COLS} FROM runs r WHERE {where} ORDER BY r.start_time DESC LIMIT ? OFFSET ?",
                         [*args, limit + 1, offset])
        items = [self._list_item(r) for r in rows[:limit]]
        if scope == "traces":
            self._first_tokens(items)
            usage = self._llm_usage([i["id"] for i in items])
            for i in items:
                u = usage.get(i["id"], {})
                i["input_tokens"], i["output_tokens"] = u.get("input_tokens"), u.get("output_tokens")
                i["total_tokens"], i["cost"] = u.get("total_tokens"), u.get("cost")
        else:
            for i in items:
                i["cost"] = self.prices.cost(i["model"], i["input_tokens"], i["output_tokens"])
        return {"items": items, "has_more": len(rows) > limit}

    def threads(self, project, f, limit=50, offset=0):
        where, args = self._where(project, f, "traces")
        page = self.db.q(
            "SELECT r.thread_id, MAX(r.start_time) AS last_start FROM runs r "
            f"WHERE {where} AND r.thread_id IS NOT NULL "
            "GROUP BY r.thread_id ORDER BY last_start DESC LIMIT ? OFFSET ?", [*args, limit + 1, offset])
        ids = [p["thread_id"] for p in page[:limit]]
        if not ids:
            return {"items": [], "has_more": False}
        roots = self.db.q(
            "SELECT id, thread_id, start_time, latency, status, error FROM runs "
            f"WHERE project = ? AND is_root = 1 AND thread_id IN ({_qs(ids)}) ORDER BY start_time",
            [project, *ids])
        usage = self._llm_usage([r["id"] for r in roots])
        by = {}
        for r in roots:
            by.setdefault(r["thread_id"], []).append(r)
        firsts = [v[0]["id"] for v in by.values()]
        lasts = [v[-1]["id"] for v in by.values()]
        io = {r["id"]: r for r in self.db.q(
            f"SELECT id, inputs, outputs FROM runs WHERE id IN ({_qs(firsts + lasts)})", firsts + lasts)}
        items = []
        for tid in ids:
            turns = by.get(tid, [])
            if not turns:
                continue
            tok = [usage.get(t["id"], {}) for t in turns]
            costs = [u.get("cost") for u in tok if u.get("cost") is not None]
            lat = [t["latency"] for t in turns]
            items.append({
                "thread_id": tid, "turns": len(turns), "first_start": turns[0]["start_time"],
                "last_start": turns[-1]["start_time"], "errors": sum(t["status"] == "error" for t in turns),
                "first_input": preview(json.loads(io[turns[0]["id"]]["inputs"] or "null")),
                "last_output": preview(json.loads(io[turns[-1]["id"]]["outputs"] or "null"))
                or (turns[-1]["error"] or "").split("\n")[0][:200],
                "p50": percentile(lat, 50), "p99": percentile(lat, 99),
                "total_tokens": sum(u.get("total_tokens") or 0 for u in tok),
                "cost": sum(costs) if costs else None,
            })
        return {"items": items, "has_more": len(page) > limit}

    def _full(self, r):
        for col in JSON_COLS:
            r[col] = json.loads(r[col]) if r.get(col) else None
        return r

    def thread(self, project, thread_id, max_turns=500):
        roots = [self._full(r) for r in self.db.q(
            "SELECT * FROM runs WHERE project = ? AND thread_id = ? AND is_root = 1 ORDER BY start_time LIMIT ?",
            (project, thread_id, max_turns))]
        ids = [r["id"] for r in roots]
        self._first_tokens(roots)
        usage = self._llm_usage(ids)
        llm = self.db.q(
            "SELECT id, trace_id, name, model, input_tokens, output_tokens, total_tokens, start_time FROM runs "
            f"WHERE trace_id IN ({_qs(ids)}) AND {_llm()} ORDER BY start_time", ids) if ids else []
        for r in roots:
            r.update(usage.get(r["id"], {}))
            r["llm_runs"] = [x for x in llm if x["trace_id"] == r["id"]]
        lat = [r["latency"] for r in roots]
        costs = [r.get("cost") for r in roots if r.get("cost") is not None]
        stats = {
            "turns": len(roots), "first_start": roots[0]["start_time"] if roots else None,
            "last_start": roots[-1]["start_time"] if roots else None,
            "p50": percentile(lat, 50), "p99": percentile(lat, 99),
            "input_tokens": sum(r.get("input_tokens") or 0 for r in roots),
            "output_tokens": sum(r.get("output_tokens") or 0 for r in roots),
            "total_tokens": sum(r.get("total_tokens") or 0 for r in roots),
            "cost": sum(costs) if costs else None, "errors": sum(r["status"] == "error" for r in roots),
        }
        return {"project": project, "thread_id": thread_id, "stats": stats, "turns": roots}

    def trace(self, trace_id):
        runs = [self._full(r) for r in self.db.q(
            "SELECT * FROM runs WHERE trace_id = ? ORDER BY start_time", (trace_id,))]
        if not runs:
            return None
        for r in runs:
            r["cost"] = self.prices.cost(r["model"], r["input_tokens"], r["output_tokens"])
        root = next((r for r in runs if r["is_root"]), runs[0])
        runs = _tree_order(runs, root)
        self._first_tokens([root])
        return {"trace_id": trace_id, "project": root["project"], "thread_id": root["thread_id"],
                "runs": runs}

    def stats(self, project, since=None, until=None):
        """Aggregate stats for a project over a time window (the project sidebar)."""
        rng, args = "", [project]
        if since is not None:
            rng += " AND start_time >= ?"
            args.append(since)
        if until is not None:
            rng += " AND start_time < ?"
            args.append(until)
        # ponytail: percentiles computed in Python over the window's root runs; push into SQL if windows get huge.
        roots = self.db.q(f"SELECT latency, status, thread_id FROM runs WHERE project = ? AND is_root = 1{rng}", args)
        per_trace = self.db.q(
            "SELECT trace_id, model, SUM(input_tokens) AS i, SUM(output_tokens) AS o, SUM(total_tokens) AS t "
            f"FROM runs WHERE project = ? AND {_llm()}{rng} GROUP BY trace_id, model", args)
        tokens, cost, priced = {}, 0.0, False
        for r in per_trace:
            tokens[r["trace_id"]] = tokens.get(r["trace_id"], 0) + (r["t"] or 0)
            c = self.prices.cost(r["model"], r["i"], r["o"])
            if c is not None:
                cost, priced = cost + c, True
        lat = [r["latency"] for r in roots]
        n = len(roots)
        errors = sum(r["status"] == "error" for r in roots)
        return {
            "traces": n, "threads": len({r["thread_id"] for r in roots if r["thread_id"]}),
            "errors": errors, "error_rate": errors / n if n else None,
            "p50": percentile(lat, 50), "p99": percentile(lat, 99),
            "total_tokens": sum(tokens.values()),
            "median_tokens": statistics.median(tokens.values()) if tokens else None,
            "cost": cost if priced else None,
        }

    def dashboard(self, project, since, until, bucket):
        """Time series per bucket plus top-runs tables (the Dashboard tab)."""
        args = [project, since, until]
        rng = "project = ? AND start_time >= ? AND start_time < ?"
        nb = max(1, math.ceil((until - since) / bucket - 1e-9))  # buckets covering [since, until)
        series = [{"t": since + i * bucket, "traces": 0, "errors": 0, "lat": [], "tokens": 0, "cost": None}
                  for i in range(nb)]

        def b(t):
            return series[min(nb - 1, max(0, int((t - since) // bucket)))]

        for r in self.db.q(f"SELECT start_time, latency, status FROM runs WHERE {rng} AND is_root = 1", args):
            s = b(r["start_time"])
            s["traces"] += 1
            s["errors"] += r["status"] == "error"
            s["lat"].append(r["latency"])
        for r in self.db.q(f"SELECT start_time, model, input_tokens AS i, output_tokens AS o, total_tokens AS t "
                           f"FROM runs WHERE {rng} AND {_llm()}", args):
            s = b(r["start_time"])
            s["tokens"] += r["t"] or 0
            c = self.prices.cost(r["model"], r["i"], r["o"])
            if c is not None:
                s["cost"] = (s["cost"] or 0) + c
        for s in series:
            lat = s.pop("lat")
            s["p50"], s["p99"] = percentile(lat, 50), percentile(lat, 99)

        groups = {}
        for r in self.db.q(f"SELECT run_type, name, model, latency, status, total_tokens FROM runs "
                           f"WHERE {rng} AND is_root = 0 AND NOT ({NESTED_LLM.format(t='runs')})", args):
            key = (r["run_type"] or "other", r["model"] if r["run_type"] == "llm" and r["model"] else r["name"])
            g = groups.setdefault(key, {"lat": [], "errors": 0, "tokens": 0})
            g["lat"].append(r["latency"])
            g["errors"] += r["status"] == "error"
            g["tokens"] += r["total_tokens"] or 0
        top = {}
        for (rtype, name), g in groups.items():
            top.setdefault(rtype, []).append({
                "name": name, "count": len(g["lat"]), "errors": g["errors"], "tokens": g["tokens"],
                "p50": percentile(g["lat"], 50), "p99": percentile(g["lat"], 99)})
        for rtype in top:
            top[rtype] = sorted(top[rtype], key=lambda x: -x["count"])[:10]
        return {"bucket": bucket, "series": series, "top": top}

    # ---------- saved filters, deletes ----------

    def saved_filters(self, project):
        rows = self.db.q("SELECT id, name, query FROM saved_filters WHERE project = ? ORDER BY name", (project,))
        for r in rows:
            r["query"] = json.loads(r["query"])
        return rows

    def save_filter(self, project, name, query):
        fid = uuid.uuid4().hex
        with self.db.tx():
            self.db.execute("INSERT INTO saved_filters (id, project, name, query, created_at) VALUES (?, ?, ?, ?, ?)",
                            (fid, project, name, json.dumps(query), time.time()))
        return fid

    def delete_filter(self, fid):
        with self.db.tx():
            self.db.execute("DELETE FROM saved_filters WHERE id = ?", (fid,))

    def delete_trace(self, trace_id):
        with self.db.tx():
            return self.db.execute("DELETE FROM runs WHERE trace_id = ?", (trace_id,)).rowcount

    def delete_project(self, project):
        with self.db.tx():
            for table, col in (("saved_filters", "project"), ("runs", "project"), ("projects", "name")):
                self.db.execute(f"DELETE FROM {table} WHERE {col} = ?", (project,))

    def delete_older_than(self, cutoff):
        """Delete whole traces whose root started before cutoff, plus any stray old runs."""
        with self.db.tx():
            return self.db.execute(
                "DELETE FROM runs WHERE start_time < ? OR trace_id IN "
                "(SELECT id FROM runs WHERE is_root = 1 AND start_time < ?)", (cutoff, cutoff)).rowcount
