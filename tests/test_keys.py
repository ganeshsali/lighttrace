import uuid

from conftest import API_KEY
from test_otel import kv, span

ADMIN = {"x-api-key": API_KEY}


def run(project="proj", **kw):
    rid = str(uuid.uuid4())
    return {"id": rid, "trace_id": kw.pop("trace_id", rid), "name": "root", "run_type": "chain",
            "start_time": "2026-01-01T00:00:00", "session_name": project, "inputs": {"input": "hi"}, **kw}


def projects(client):
    return {p["name"]: p["trace_count"] for p in client.get("/api/projects").json()}


def test_project_key_lifecycle_and_confinement(client):
    assert client.post("/api/projects/mine/keys", json={"name": "ci"}, headers={"x-lighttrace": ""}).status_code == 403
    k = client.post("/api/projects/mine/keys", json={"name": "ci"}).json()
    assert k["key"].startswith("lt_") and k["prefix"] == k["key"][:10]
    [listed] = client.get("/api/projects/mine/keys").json()
    assert listed["name"] == "ci" and "key" not in listed and "hash" not in listed and listed["last_used_at"] is None
    assert projects(client) == {"mine": 0}  # creating a key creates the project
    key = {"x-api-key": k["key"]}

    # LangSmith path: session_name is ignored, the run lands in the key's project
    assert client.post("/runs/batch", json={"post": [run("someone-else")]}, headers=key).status_code == 200
    assert client.post("/runs", json=run("x"), headers=key).status_code == 200
    # OTLP path (Bearer auth too): header and service.name are ignored
    body = {"resourceSpans": [{"resource": {"attributes": [kv("service.name", "svc")]},
                               "scopeSpans": [{"spans": [span("b7ad6b7169203331", "", "otel-root", 0, 1, {})]}]}]}
    assert client.post("/v1/traces", json=body, headers={"authorization": f"Bearer {k['key']}",
                                                         "x-lighttrace-project": "other"}).status_code == 200
    assert projects(client) == {"mine": 3}
    assert client.get("/api/projects/mine/keys").json()[0]["last_used_at"] is not None

    # it cannot touch another project's runs: neither patch them nor add runs to their traces
    theirs = run("theirs")
    client.post("/runs/batch", json={"post": [theirs]}, headers=ADMIN)
    assert client.patch(f"/runs/{theirs['id']}", json={"error": "pwned"}, headers=key).status_code == 200
    intruder = run(trace_id=theirs["id"], parent_run_id=theirs["id"], name="intruder")
    client.post("/runs/batch", json={"post": [intruder]}, headers=key)
    t = client.get(f"/api/traces/{theirs['id']}").json()
    assert t["project"] == "theirs" and [r["name"] for r in t["runs"]] == ["root"] and t["runs"][0]["error"] is None

    # revoked: rejected on both paths
    assert client.delete(f"/api/keys/{k['id']}").status_code == 200
    assert client.post("/runs/batch", json={"post": [run()]}, headers=key).status_code == 401
    assert client.post("/v1/traces", json=body, headers=key).status_code == 401
    assert client.delete(f"/api/keys/{k['id']}").status_code == 404


def test_admin_key_writes_anywhere(client):
    client.post("/runs/batch", json={"post": [run("a"), run("b")]}, headers=ADMIN)
    body = {"resourceSpans": [{"scopeSpans": [{"spans": [span("b7ad6b7169203331", "", "r", 0, 1, {})]}]}]}
    client.post("/v1/traces", json=body, headers={**ADMIN, "x-lighttrace-project": "c"})
    assert projects(client) == {"a": 1, "b": 1, "c": 1}
    assert client.post("/runs", json=run(), headers={"x-api-key": ""}).status_code == 401


def test_deleting_a_project_revokes_its_keys(client):
    k = client.post("/api/projects/gone/keys", json={}).json()
    assert k["name"] == "key"
    client.delete("/api/projects/gone")
    assert client.post("/runs", json=run(), headers={"x-api-key": k["key"]}).status_code == 401


def test_ui_files_are_revalidated(client):
    """After an upgrade the browser must not keep old UI scripts (the Keys page lives in project.js)."""
    r = client.get("/static/project.js")
    assert r.headers["cache-control"] == "no-cache" and "viewKeys" in r.text
