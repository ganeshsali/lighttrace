"""LightTrace server: one OpenTelemetry (OTLP/HTTP) receiver + a web UI."""

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
        if not self.api_keys:
            raise SystemExit("lighttrace: set LIGHTTRACE_API_KEYS (comma-separated ingest keys)")
        if not self.password:
            raise SystemExit("lighttrace: set LIGHTTRACE_PASSWORD (UI admin password)")


_warned = set()


def warn_once(key, msg, *args):
    """Unsupported SDK behaviour is logged once per kind, never per request."""
    if key not in _warned:
        _warned.add(key)
        log.warning(msg, *args)


def _float(v):
    if v in (None, ""):
        return None
    try:
        return float(v)
    except ValueError:
        raise HTTPException(400, f"not a number: {v!r}") from None


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

    app = FastAPI(title="LightTrace", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
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

    api_keys = [k.encode() for k in settings.api_keys]

    def check_key(request):
        """Only keys listed in LIGHTTRACE_API_KEYS get in."""
        key = request.headers.get("x-api-key") or ""
        auth = request.headers.get("authorization") or ""
        if not key and auth.lower().startswith("bearer "):
            key = auth[7:]
        if not (key and any(hmac.compare_digest(key.encode(), k) for k in api_keys)):
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

    async def otlp(request, kind):
        """One OTLP/HTTP export (protobuf or JSON). Project: the x-lighttrace-project header, else
        service.name (OTEL_SERVICE_NAME), else "default"; created on its first trace."""
        check_key(request)
        body = await read_body(request)
        ctype = (request.headers.get("content-type") or "").lower()
        if "protobuf" not in ctype and "json" not in ctype:
            warn_once(("otlp-ctype", ctype.split(";")[0]), "unsupported OTLP Content-Type %r", ctype)
            raise HTTPException(415, "expected application/x-protobuf or application/json")
        if kind == "metrics":
            warn_once("metrics", "ignoring OTLP metrics (LightTrace stores traces only); "
                                 "set OTEL_METRICS_EXPORTER=none in the app to stop sending them")
        else:
            try:
                req = otel.parse(body, ctype, kind)
                data = (otel.to_runs(req, request.headers.get("x-lighttrace-project")) if kind == "traces"
                        else otel.log_messages(req))
            except Exception as e:  # untrusted payload: any shape error is the sender's
                warn_once(("otlp-bad", type(e).__name__), "rejected OTLP payload: %s", type(e).__name__)
                raise HTTPException(400, "invalid OTLP payload") from None
            (store.upsert_runs if kind == "traces" else store.attach_messages)(data)
        if "protobuf" in ctype:  # an empty Export*ServiceResponse
            return Response(b"", media_type="application/x-protobuf")
        return {}

    @app.post("/v1/traces")
    async def otlp_traces(request: Request):
        return await otlp(request, "traces")

    @app.post("/v1/logs")
    async def otlp_logs(request: Request):
        """Only GenAI events (prompt/response content) are kept, on their span's run."""
        return await otlp(request, "logs")

    @app.post("/v1/metrics")
    async def otlp_metrics(request: Request):
        return await otlp(request, "metrics")

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

    R = {"dependencies": [Depends(session)]}
    W = {"dependencies": [Depends(session_write)]}

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

    @app.middleware("http")
    async def revalidate_ui(request: Request, call_next):
        """Browsers re-check UI files (cheap, ETag), so an upgrade never leaves them on old scripts."""
        resp = await call_next(request)
        if request.url.path.startswith("/static/"):
            resp.headers["Cache-Control"] = "no-cache"
        return resp

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def unknown(path: str, request: Request):
        warn_once(("path", request.method, path.split("/")[0]),
                  "unsupported endpoint %s /%s (LightTrace implements tracing ingest only)", request.method, path)
        return Response(status_code=404)

    return app


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
    p = argparse.ArgumentParser(prog="lighttrace", description="Lightweight self-hosted tracing server (OpenTelemetry)")
    p.add_argument("--host", default=s.host)
    p.add_argument("--port", type=int, default=s.port)
    p.add_argument("--db", default=s.db_path, help="SQLite file path or postgresql:// URL")
    a = p.parse_args()
    s.host, s.port, s.db_path = a.host, a.port, a.db
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = create_app(s)
    where = "postgres" if s.db_path.startswith(("postgres://", "postgresql://")) else s.db_path
    log.info("LightTrace %s on http://%s:%d, OTLP at /v1/traces (db: %s, retention: %s)", __version__, s.host,
             s.port, where,
             f"{s.retention_days:g} days" if s.retention_days > 0 else "forever")
    # access_log off: keep logs quiet; payloads are never logged anywhere.
    uvicorn.run(app, host=s.host, port=s.port, access_log=False, proxy_headers=True)


if __name__ == "__main__":
    main()
