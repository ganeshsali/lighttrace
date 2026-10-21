"""lighttrace server: LangSmith-compatible ingest + a web UI."""

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, otel
from .store import META_KEY_RE, Prices, Store

log = logging.getLogger("lighttrace")
STATIC = Path(__file__).parent / "static"
SESSION_COOKIE = "lighttrace_session"
SESSION_TTL = 7 * 24 * 3600


def _env_bool(name, default=False):
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _load_prices(value):
    """LIGHTTRACE_PRICES: inline JSON or a path to a JSON file, {"model": [usd_per_1M_in, usd_per_1M_out]}."""
    if not value:
        return {}
    text = value if value.lstrip().startswith("{") else Path(value).read_text()
    return json.loads(text)


@dataclass
class Settings:
    # A file path (SQLite) or a postgresql:// URL.
    db_path: str = field(default_factory=lambda: os.environ.get("LIGHTTRACE_DB", "lighttrace.db"))
    api_keys: list = field(default_factory=lambda: [
        k.strip() for k in os.environ.get("LIGHTTRACE_API_KEYS", "").split(",") if k.strip()])
    password: str = field(default_factory=lambda: os.environ.get("LIGHTTRACE_PASSWORD", ""))
    cookie_secure: bool = field(default_factory=lambda: _env_bool("LIGHTTRACE_COOKIE_SECURE"))
    retention_days: float = field(default_factory=lambda: float(os.environ.get("LIGHTTRACE_RETENTION_DAYS", "30")))
    max_body_mb: float = field(default_factory=lambda: float(os.environ.get("LIGHTTRACE_MAX_BODY_MB", "50")))
    host: str = field(default_factory=lambda: os.environ.get("LIGHTTRACE_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(os.environ.get("LIGHTTRACE_PORT", "1984")))
    prices: dict = field(default_factory=lambda: _load_prices(os.environ.get("LIGHTTRACE_PRICES", "")))

    def validate(self):
        if not self.password:
            raise SystemExit("lighttrace: set LIGHTTRACE_PASSWORD (UI admin password)")


_warned = set()


def warn_once(key, msg, *args):
    """Unsupported SDK behaviour is logged once per kind, never per request."""
    if key not in _warned:
        _warned.add(key)
        log.warning(msg, *args)


# The SDK reads all of these keys; a missing one kills its tracing thread.
INFO = {
    "version": __version__,
    "license_expiration_time": None,
    "batch_ingest_config": {
        "use_multipart_endpoint": False,
        "scale_up_qsize_trigger": 1000,
        "scale_up_nthreads_limit": 16,
        "scale_down_nempty_trigger": 4,
        "size_limit": 100,
        "size_limit_bytes": 20_971_520,
    },
    "instance_flags": {"zstd_compression_enabled": False},
}


def _float(v):
    if v in (None, ""):
        return None
    try:
        return float(v)
    except ValueError:
        raise HTTPException(400, f"not a number: {v!r}")


def ui_filters(request: Request):
    """The UI's filter query params -> Store filter dict (validated)."""
    p = request.query_params
    meta = []
    for item in p.getlist("meta"):
        key, sep, value = item.partition(":")
        if not sep or not META_KEY_RE.match(key):
            raise HTTPException(400, "meta filters look like key:value")
        meta.append((key, value))
    status = p.get("status") or None
    if status not in (None, "success", "error", "pending"):
        raise HTTPException(400, "bad status")
    return {
        "since": _float(p.get("since")), "until": _float(p.get("until")), "status": status,
        "run_type": p.get("run_type") or None, "name": p.get("name") or None, "q": p.get("q") or None,
        "tag": p.get("tag") or None, "thread_id": p.get("thread_id") or None, "meta": meta,
        "min_latency": _float(p.get("min_latency")), "max_latency": _float(p.get("max_latency")),
        "min_tokens": _float(p.get("min_tokens")),
    }


def _page(limit, offset):
    return max(1, min(limit, 200)), max(0, offset)


def create_app(settings=None):
    settings = settings or Settings()
    settings.validate()
    store = Store(settings.db_path, Prices(settings.prices))
    max_body = int(settings.max_body_mb * 1024 * 1024)
    session_key = hashlib.sha256(b"lighttrace-session:" + settings.password.encode()).digest()

    app = FastAPI(title="lighttrace", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store

    # ---------- retention ----------

    def retention_loop():
        while True:
            try:
                n = store.delete_older_than(time.time() - settings.retention_days * 86400)
                if n:
                    log.info("retention: deleted %d runs older than %s days", n, settings.retention_days)
            except Exception:
                log.exception("retention sweep failed")
            time.sleep(3600)

    if settings.retention_days > 0:
        threading.Thread(target=retention_loop, daemon=True, name="lighttrace-retention").start()

    # ---------- ingest ----------

    admin_keys = [k.encode() for k in settings.api_keys]

    def key_project(key):
        """None for an admin key (LIGHTTRACE_API_KEYS, any project), else the project key's project.
        Raises PermissionError for a bad key."""
        if key and any(hmac.compare_digest(key.encode(), k) for k in admin_keys):
            return None
        project = store.key_project(key) if key else None
        if not project:
            raise PermissionError
        return project

    def check_key(request):
        key = request.headers.get("x-api-key") or ""
        auth = request.headers.get("authorization") or ""
        if not key and auth.lower().startswith("bearer "):
            key = auth[7:]
        elif not key and auth.lower().startswith("basic "):  # Langfuse SDK: public_key:secret_key
            try:
                key = base64.b64decode(auth[6:]).decode().partition(":")[2]
            except ValueError:
                pass
        try:
            return key_project(key)
        except PermissionError:
            raise HTTPException(401, "invalid api key")

    async def read_body(request):
        body = await request.body()
        if len(body) > max_body:
            raise HTTPException(413, "body too large")
        enc = (request.headers.get("content-encoding") or "").lower()
        if enc == "gzip":
            d = zlib.decompressobj(wbits=16 + zlib.MAX_WBITS)
            body = d.decompress(body, max_body)
            if d.unconsumed_tail:
                raise HTTPException(413, "body too large")
        elif enc not in ("", "identity"):
            warn_once(("enc", enc), "unsupported Content-Encoding %r on %s; ask the SDK not to compress", enc,
                      request.url.path)
            raise HTTPException(415, f"unsupported content-encoding {enc}")
        return body

    async def read_json(request):
        body = await read_body(request)
        ctype = request.headers.get("content-type", "")
        if ctype and "json" not in ctype:
            warn_once(("ctype", ctype.split(";")[0]), "unsupported Content-Type %r on %s", ctype, request.url.path)
            raise HTTPException(415, "expected application/json")
        try:
            return json.loads(body)
        except ValueError:
            raise HTTPException(400, "invalid json")

    @app.get("/info")
    def info():
        return INFO

    @app.post("/runs/batch")
    async def runs_batch(request: Request):
        project = check_key(request)
        body = await read_json(request)
        if not isinstance(body, dict):
            raise HTTPException(400, "expected {post: [...], patch: [...]}")
        unknown = set(body) - {"post", "patch"}
        if unknown:
            warn_once(("batch-keys", tuple(sorted(unknown))), "ignoring unknown /runs/batch keys %s", sorted(unknown))
        runs = list(body.get("post") or []) + list(body.get("patch") or [])
        _validate_runs(runs)
        store.upsert_runs(runs, project)
        return {}

    @app.post("/runs")
    async def create_run(request: Request):
        project = check_key(request)
        run = await read_json(request)
        _validate_runs([run])
        store.upsert_runs([run], project)
        return {}

    @app.patch("/runs/{run_id}")
    async def update_run(run_id: str, request: Request):
        project = check_key(request)
        run = await read_json(request)
        if not isinstance(run, dict):
            raise HTTPException(400, "expected a run object")
        store.upsert_runs([{**run, "id": run_id}], project)
        return {}

    @app.post("/runs/multipart")
    def runs_multipart():
        # 404 makes the SDK fall back to /runs/batch.
        warn_once("multipart", "SDK used /runs/multipart; lighttrace only supports /runs/batch (it will fall back)")
        raise HTTPException(404, "multipart ingest not supported")

    @app.post("/v1/traces")
    @app.post("/api/public/otel/v1/traces")  # where the Langfuse SDK sends (LANGFUSE_HOST)
    async def otlp_traces(request: Request):
        """OTLP/HTTP (protobuf or JSON). Project: the project key's, else the x-lighttrace-project
        header, else service.name."""
        project = check_key(request)
        body = await read_body(request)
        ctype = (request.headers.get("content-type") or "").lower()
        if "protobuf" not in ctype and "json" not in ctype:
            warn_once(("otlp-ctype", ctype.split(";")[0]), "unsupported OTLP Content-Type %r", ctype)
            raise HTTPException(415, "expected application/x-protobuf or application/json")
        try:
            req, b64 = otel.parse(body, ctype)
            runs = otel.to_runs(req, b64, project or request.headers.get("x-lighttrace-project"))
        except Exception as e:  # untrusted payload: any shape error is the sender's
            warn_once(("otlp-bad", type(e).__name__), "rejected OTLP payload: %s", type(e).__name__)
            raise HTTPException(400, "invalid OTLP payload")
        store.upsert_runs(runs, project)
        if "protobuf" in ctype:  # an empty ExportTraceServiceResponse
            return Response(b"", media_type="application/x-protobuf")
        return {}

    # ---------- UI auth ----------

    def make_session():
        exp = str(int(time.time()) + SESSION_TTL)
        return exp + "." + hmac.new(session_key, exp.encode(), "sha256").hexdigest()

    def session(request: Request):
        val = request.cookies.get(SESSION_COOKIE) or ""
        exp, _, sig = val.partition(".")
        good = hmac.new(session_key, exp.encode(), "sha256").hexdigest()
        if not (exp.isdigit() and hmac.compare_digest(sig, good) and int(exp) > time.time()):
            raise HTTPException(401, "login required")

    def session_write(request: Request):
        """Mutating UI calls also need a custom header, which a cross-site form cannot send."""
        session(request)
        if request.headers.get("x-lighttrace") != "1":
            raise HTTPException(403, "missing x-lighttrace header")

    @app.post("/api/login")
    async def login(request: Request):
        try:
            pw = str((await request.json()).get("password", ""))
        except Exception:
            pw = ""
        if not secrets.compare_digest(pw.encode(), settings.password.encode()):
            time.sleep(0.5)  # slow down guessing
            raise HTTPException(401, "wrong password")
        resp = JSONResponse({"ok": True})
        resp.set_cookie(SESSION_COOKIE, make_session(), max_age=SESSION_TTL, httponly=True,
                        samesite="strict", secure=settings.cookie_secure)
        return resp

    @app.post("/api/logout")
    def logout():
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(SESSION_COOKIE)
        return resp

    @app.get("/api/me", dependencies=[Depends(session)])
    def me():
        return {"ok": True, "version": __version__, "prices": bool(settings.prices),
                "retention_days": settings.retention_days}

    # ---------- UI data API ----------

    R = dict(dependencies=[Depends(session)])
    W = dict(dependencies=[Depends(session_write)])

    @app.get("/api/projects", **R)
    def api_projects(since: float | None = None):
        return store.projects(since if since is not None else time.time() - 7 * 86400)

    @app.delete("/api/projects/{project}", **W)
    def api_delete_project(project: str):
        store.delete_project(project)
        return {"ok": True}

    @app.get("/api/projects/{project}/stats", **R)
    def api_stats(project: str, since: float | None = None, until: float | None = None):
        return store.stats(project, since, until)

    @app.get("/api/projects/{project}/runs", **R)
    def api_runs(request: Request, project: str, scope: str = "traces", limit: int = 50, offset: int = 0):
        if scope not in ("traces", "runs"):
            raise HTTPException(400, "scope is traces or runs")
        return store.runs(project, ui_filters(request), scope, *_page(limit, offset))

    @app.get("/api/projects/{project}/threads", **R)
    def api_threads(request: Request, project: str, limit: int = 50, offset: int = 0):
        return store.threads(project, ui_filters(request), *_page(limit, offset))

    @app.get("/api/projects/{project}/thread", **R)
    def api_thread(project: str, id: str):
        t = store.thread(project, id)
        if not t["turns"]:
            raise HTTPException(404, "thread not found")
        return t

    @app.get("/api/projects/{project}/dashboard", **R)
    def api_dashboard(project: str, since: float, until: float | None = None, bucket: float | None = None):
        until = until or time.time()
        if until <= since:
            raise HTTPException(400, "until must be after since")
        # ~60 buckets by default, never more than 500.
        bucket = max(bucket or (until - since) / 60, (until - since) / 500, 60)
        return store.dashboard(project, since, until, bucket)

    @app.get("/api/projects/{project}/filters", **R)
    def api_filters(project: str):
        return store.saved_filters(project)

    @app.post("/api/projects/{project}/filters", **W)
    async def api_save_filter(project: str, request: Request):
        body = await request.json()
        name = str(body.get("name") or "").strip()[:100]
        if not name or not isinstance(body.get("query"), dict):
            raise HTTPException(400, "needs name and query")
        return {"id": store.save_filter(project, name, body["query"])}

    @app.delete("/api/filters/{fid}", **W)
    def api_delete_filter(fid: str):
        store.delete_filter(fid)
        return {"ok": True}

    @app.get("/api/projects/{project}/keys", **R)
    def api_keys(project: str):
        return store.keys(project)

    @app.post("/api/projects/{project}/keys", **W)
    async def api_create_key(project: str, request: Request):
        try:
            name = str((await request.json()).get("name") or "").strip()[:100]
        except Exception:
            name = ""
        if not project.strip() or len(project) > 200:
            raise HTTPException(400, "bad project name")
        return store.create_key(project, name or "key")

    @app.delete("/api/keys/{key_id}", **W)
    def api_delete_key(key_id: str):
        if not store.delete_key(key_id):
            raise HTTPException(404, "key not found")
        return {"ok": True}

    @app.get("/api/traces/{trace_id}", **R)
    def api_trace(trace_id: str):
        t = store.trace(trace_id)
        if not t:
            raise HTTPException(404, "trace not found")
        return t

    @app.delete("/api/traces/{trace_id}", **W)
    def api_delete_trace(trace_id: str):
        if not store.delete_trace(trace_id):
            raise HTTPException(404, "trace not found")
        return {"ok": True}

    # ---------- static UI + unknown endpoints ----------

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def unknown(path: str, request: Request):
        warn_once(("path", request.method, path.split("/")[0]),
                  "unsupported endpoint %s /%s (lighttrace implements tracing ingest only)", request.method, path)
        return Response(status_code=404)

    return app


def _validate_runs(runs):
    for run in runs:
        if not isinstance(run, dict) or not run.get("id"):
            raise HTTPException(400, "each run needs an id")


def load_dotenv(path=".env"):
    """Minimal .env reader: KEY=VALUE lines, # comments, optional quotes. Real env vars win."""
    try:
        lines = Path(path).read_text().splitlines()
    except FileNotFoundError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.removeprefix("export ").partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def main():
    import argparse

    import uvicorn

    load_dotenv()
    s = Settings()
    p = argparse.ArgumentParser(prog="lighttrace", description="Lightweight LangSmith-compatible tracing server")
    p.add_argument("--host", default=s.host)
    p.add_argument("--port", type=int, default=s.port)
    p.add_argument("--db", default=s.db_path, help="SQLite file path or postgresql:// URL")
    a = p.parse_args()
    s.host, s.port, s.db_path = a.host, a.port, a.db
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = create_app(s)
    where = "postgres" if s.db_path.startswith(("postgres://", "postgresql://")) else s.db_path
    log.info("lighttrace %s on http://%s:%d (db: %s, retention: %s)", __version__, s.host, s.port, where,
             f"{s.retention_days:g} days" if s.retention_days > 0 else "forever")
    # access_log off: keep logs quiet; payloads are never logged anywhere.
    uvicorn.run(app, host=s.host, port=s.port, access_log=False, proxy_headers=True)


if __name__ == "__main__":
    main()
