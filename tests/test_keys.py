from conftest import API_KEY
from test_otel import TRACE, kv, span

ADMIN = {"x-api-key": API_KEY}


def body(name="root", service="svc", span_id="b7ad6b7169203331", **attrs):
    return {"resourceSpans": [{"resource": {"attributes": [kv("service.name", service)]},
                               "scopeSpans": [{"spans": [span(span_id, "", name, 0, 1, attrs)]}]}]}


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

    # the x-lighttrace-project header and service.name are ignored: traces land in the key's project
    assert client.post("/v1/traces", json=body(service="a"), headers={**key, "x-lighttrace-project": "other"}
                       ).status_code == 200
    assert client.post("/v1/traces", json=body(service="b"), headers={"authorization": f"Bearer {k['key']}"}
                       ).status_code == 200
    assert projects(client) == {"mine": 2}
    assert client.get("/api/projects/mine/keys").json()[0]["last_used_at"] is not None

    # it cannot overwrite another project's runs (same OTel ids) nor add spans to their traces
    client.post("/v1/traces", json=body(service="shared"), headers={**ADMIN, "x-lighttrace-project": "theirs"})
    client.post("/v1/traces", json=body("pwned", service="shared"), headers=key)
    child = body("intruder", service="shared", span_id="1111111111111111")
    child["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["parentSpanId"] = "b7ad6b7169203331"
    client.post("/v1/traces", json=child, headers=key)
    [t] = client.get("/api/projects/theirs/runs").json()["items"]
    assert t["name"] == "root" and [r["name"] for r in client.get(f"/api/traces/{t['id']}").json()["runs"]] == ["root"]

    # revoked: rejected on traces and logs
    assert client.delete(f"/api/keys/{k['id']}").status_code == 200
    assert client.post("/v1/traces", json=body(), headers=key).status_code == 401
    assert client.post("/v1/logs", json={"resourceLogs": []}, headers=key).status_code == 401
    assert client.delete(f"/api/keys/{k['id']}").status_code == 404


def test_admin_key_writes_anywhere(client):
    for p in ("a", "b"):
        client.post("/v1/traces", json=body(service=p), headers=ADMIN)
    client.post("/v1/traces", json=body(service="x"), headers={**ADMIN, "x-lighttrace-project": "c"})
    assert projects(client) == {"a": 1, "b": 1, "c": 1}
    assert client.post("/v1/traces", json=body(), headers={"x-api-key": ""}).status_code == 401


def test_deleting_a_project_revokes_its_keys(client):
    k = client.post("/api/projects/gone/keys", json={}).json()
    assert k["name"] == "key"
    client.delete("/api/projects/gone")
    assert client.post("/v1/traces", json=body(), headers={"x-api-key": k["key"]}).status_code == 401


def test_ui_files_are_revalidated(client):
    """After an upgrade the browser must not keep old UI scripts (the Keys page lives in project.js)."""
    r = client.get("/static/project.js")
    assert r.headers["cache-control"] == "no-cache" and "viewKeys" in r.text
