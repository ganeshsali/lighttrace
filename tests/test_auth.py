import pytest
from fastapi.testclient import TestClient

from conftest import PASSWORD
from lighttrace.app import Settings, create_app
from test_otel import kv, span


def body(service=None):
    res = {"attributes": [kv("service.name", service)]} if service else {}
    return {"resourceSpans": [{"resource": res, "scopeSpans": [{"spans": [span("b7ad6b7169203331", "", "r", 0, 1, {})]}]}]}


@pytest.fixture
def app(tmp_path):
    return create_app(Settings(db_path=str(tmp_path / "a.db"), api_keys=["key-one", "key-two"], password=PASSWORD,
                               retention_days=0))


def test_only_listed_keys_get_in(app):
    c = TestClient(app)
    for key in ("key-one", "key-two"):
        assert c.post("/v1/traces", json=body(key), headers={"x-api-key": key}).status_code == 200
    assert c.post("/v1/traces", json=body(), headers={"authorization": "Bearer key-two"}).status_code == 200
    for headers in ({}, {"x-api-key": ""}, {"x-api-key": "key-three"}, {"authorization": "Bearer nope"}):
        assert c.post("/v1/traces", json=body(), headers=headers).status_code == 401
        assert c.post("/v1/logs", json={"resourceLogs": []}, headers=headers).status_code == 401


def test_projects_are_created_from_the_apps_own_name(app):
    """No project setup: OTEL_SERVICE_NAME (or the x-lighttrace-project header) names the project."""
    c = TestClient(app)
    c.post("/v1/traces", json=body("mira-local"), headers={"x-api-key": "key-one"})
    c.post("/v1/traces", json=body("ignored"), headers={"x-api-key": "key-one", "x-lighttrace-project": "chatassist"})
    c.post("/v1/traces", json=body("unknown_service:python"), headers={"x-api-key": "key-two"})
    c.post("/api/login", json={"password": PASSWORD})
    assert sorted(p["name"] for p in c.get("/api/projects").json()) == ["chatassist", "default", "mira-local"]


def test_keys_are_required(tmp_path):
    with pytest.raises(SystemExit):
        create_app(Settings(db_path=str(tmp_path / "b.db"), api_keys=[], password=PASSWORD))


def test_ui_files_are_revalidated(app):
    """After an upgrade the browser must not keep old UI scripts."""
    r = TestClient(app).get("/static/project.js")
    assert r.headers["cache-control"] == "no-cache" and "viewDashboard" in r.text
