import gzip
import json
import time
import uuid

from conftest import API_KEY

H = {"x-api-key": API_KEY}


def run(**kw):
    rid = kw.pop("id", None) or str(uuid.uuid4())
    return {"id": rid, "trace_id": kw.pop("trace_id", rid), "dotted_order": f"20260101T000000000000Z{rid}",
            "name": "root", "run_type": "chain", "start_time": "2026-01-01T00:00:00.000000",
            "session_name": "proj", "inputs": {"input": "hi"}, **kw}


def traces(client, **params):
    return client.get("/api/projects/proj/runs", params={"scope": "traces", **params}).json()["items"]


def llm_out(i, o):
    return {"generations": [[{"message": {"lc": 1, "type": "constructor", "kwargs": {
        "content": "x", "usage_metadata": {"input_tokens": i, "output_tokens": o, "total_tokens": i + o}}}}]]}


def test_info_has_all_batch_config_keys(client):
    cfg = client.get("/info").json()["batch_ingest_config"]
    assert cfg["use_multipart_endpoint"] is False
    for k in ("scale_up_qsize_trigger", "scale_up_nthreads_limit", "scale_down_nempty_trigger", "size_limit",
              "size_limit_bytes"):
        assert k in cfg


def test_auth_rejected(client):
    body = {"post": [run()]}
    assert client.post("/runs/batch", json=body).status_code == 401
    assert client.post("/runs/batch", json=body, headers={"x-api-key": "nope"}).status_code == 401
    assert client.post("/runs", json=run(), headers={"x-api-key": "nope"}).status_code == 401
    assert client.patch(f"/runs/{uuid.uuid4()}", json={}, headers={"x-api-key": "nope"}).status_code == 401
    assert client.get("/api/projects").json() == []
    client.cookies.clear()
    assert client.get("/api/projects").status_code == 401
    assert client.post("/api/login", json={"password": "wrong"}).status_code == 401


def test_writes_need_the_csrf_header(client):
    r = run()
    client.post("/runs/batch", json={"post": [r]}, headers=H)
    assert client.delete(f"/api/traces/{r['id']}", headers={"x-lighttrace": ""}).status_code == 403
    assert client.delete(f"/api/traces/{r['id']}").status_code == 200
    assert client.get(f"/api/traces/{r['id']}").status_code == 404


def test_patch_before_post(client):
    r = run()
    patch = {"id": r["id"], "trace_id": r["id"], "dotted_order": r["dotted_order"],
             "end_time": "2026-01-01T00:00:02.500000", "outputs": {"output": "done"}, "extra": {"metadata": {"b": 2}}}
    assert client.post("/runs/batch", json={"patch": [patch]}, headers=H).status_code == 200
    r["extra"] = {"metadata": {"a": 1}}
    assert client.post("/runs/batch", json={"post": [r]}, headers=H).status_code == 200

    (stored,) = client.get(f"/api/traces/{r['id']}").json()["runs"]
    assert stored["status"] == "success" and stored["project"] == "proj"
    assert stored["outputs"] == {"output": "done"} and stored["inputs"] == {"input": "hi"}
    assert stored["extra"]["metadata"] == {"a": 1, "b": 2}
    assert stored["end_time"] - stored["start_time"] == 2.5 and stored["latency"] == 2.5
    assert client.get("/api/projects").json()[0]["trace_count"] == 1


def test_single_run_endpoints_and_error(client):
    r = run()
    assert client.post("/runs", json=r, headers=H).status_code == 200
    assert client.patch(f"/runs/{r['id']}", json={"end_time": "2026-01-01T00:00:01", "error": "Boom\nTraceback"},
                        headers=H).status_code == 200
    t = traces(client)[0]
    assert t["status"] == "error" and t["error"].startswith("Boom")
    assert traces(client, status="success") == []


def test_gzip_body(client):
    r = run(inputs={"question": "gzipped?"})
    body = gzip.compress(json.dumps({"post": [r]}).encode())
    resp = client.post("/runs/batch", content=body,
                       headers={**H, "content-type": "application/json", "content-encoding": "gzip"})
    assert resp.status_code == 200
    assert traces(client, q="gzipped")[0]["id"] == r["id"]
    assert client.post("/runs/batch", content=b"x", headers={**H, "content-encoding": "zstd"}).status_code == 415


def test_multipart_is_404(client):
    assert client.post("/runs/multipart", headers=H).status_code == 404


def test_tokens_and_cost_summed_per_trace(client):
    root = run(end_time="2026-01-01T00:00:01")
    child = run(trace_id=root["id"], parent_run_id=root["id"], run_type="llm", outputs=llm_out(3, 4),
                extra={"metadata": {"ls_model_name": "fake-model"}})
    child2 = run(trace_id=root["id"], parent_run_id=root["id"], run_type="llm",
                 outputs={"generations": [[{"text": "y"}]], "llm_output": {"token_usage": {
                     "prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}}})
    client.post("/runs/batch", json={"post": [root, child, child2]}, headers=H)
    t = traces(client)[0]
    assert t["total_tokens"] == 10
    assert abs(t["cost"] - (3 * 1.0 + 4 * 2.0) / 1e6) < 1e-12  # only the priced model counts
    assert traces(client, min_tokens=11) == [] and len(traces(client, min_tokens=10)) == 1
    llm = client.get("/api/projects/proj/runs", params={"scope": "runs", "run_type": "llm"}).json()["items"]
    assert sorted(x["model"] or "" for x in llm) == ["", "fake-model"]


def test_trace_first_token_comes_from_its_llm_run(client):
    """A graph root never streams; its first token is the first streamed LLM token inside it."""
    root = run(start_time="2026-01-01T00:00:00", end_time="2026-01-01T00:00:05",
               extra={"metadata": {"thread_id": "ft"}})
    llm = run(trace_id=root["id"], parent_run_id=root["id"], run_type="llm", start_time="2026-01-01T00:00:01",
              end_time="2026-01-01T00:00:03", outputs=llm_out(1, 1),
              events=[{"name": "start", "time": "2026-01-01T00:00:01"},
                      {"name": "new_token", "time": "2026-01-01T00:00:01.400000"},
                      {"name": "new_token", "time": "2026-01-01T00:00:01.500000"}])
    client.post("/runs/batch", json={"post": [root, llm]}, headers=H)
    assert round(traces(client)[0]["first_token_time"], 6) == 1.4
    runs = client.get(f"/api/traces/{root['id']}").json()["runs"]
    # the root counts from the trace start, the llm run from its own start
    assert {r["run_type"]: round(r["first_token_time"], 6) for r in runs} == {"chain": 1.4, "llm": 0.4}
    assert round(client.get("/api/projects/proj/thread", params={"id": "ft"}).json()["turns"][0]["first_token_time"], 6) == 1.4


def test_threads_group_turns(client):
    turns = [run(start_time=f"2026-01-01T00:00:0{i}", end_time=f"2026-01-01T00:00:0{i}.5",
                 inputs={"input": f"q{i}"}, outputs={"output": f"a{i}"},
                 extra={"metadata": {"thread_id": "th"}}) for i in range(3)]
    other = run(extra={"metadata": {"session_id": "s2"}})
    client.post("/runs/batch", json={"post": turns + [other]}, headers=H)
    items = client.get("/api/projects/proj/threads").json()["items"]
    th = next(i for i in items if i["thread_id"] == "th")
    assert th["turns"] == 3 and th["first_input"] == "q0" and th["last_output"] == "a2" and th["p50"] == 0.5
    assert {i["thread_id"] for i in items} == {"th", "s2"}
    thread = client.get("/api/projects/proj/thread", params={"id": "th"}).json()
    assert [t["inputs"]["input"] for t in thread["turns"]] == ["q0", "q1", "q2"]
    assert client.get("/api/projects/proj/thread", params={"id": "nope"}).status_code == 404


def test_filters_latency_tag_name_and_saved(client):
    fast = run(end_time="2026-01-01T00:00:00.1", tags=["prod"], name="fast")
    slow = run(end_time="2026-01-01T00:00:05", name="slow")
    client.post("/runs/batch", json={"post": [fast, slow]}, headers=H)
    assert [t["name"] for t in traces(client, min_latency=1)] == ["slow"]
    assert [t["name"] for t in traces(client, max_latency=1)] == ["fast"]
    assert [t["name"] for t in traces(client, tag="prod")] == ["fast"]
    assert [t["name"] for t in traces(client, name="SLO")] == ["slow"]
    assert client.get("/api/projects/proj/runs", params={"meta": "bad key:x"}).status_code == 400
    fid = client.post("/api/projects/proj/filters", json={"name": "slow ones", "query": {"min_latency": "1"}}).json()["id"]
    assert client.get("/api/projects/proj/filters").json() == [{"id": fid, "name": "slow ones",
                                                                "query": {"min_latency": "1"}}]
    client.delete(f"/api/filters/{fid}")
    assert client.get("/api/projects/proj/filters").json() == []


def test_stats_and_dashboard(client):
    now = time.time()
    ok = run(start_time=now - 100, end_time=now - 99)
    bad = run(start_time=now - 50, end_time=now - 47, error="x")
    llm = run(trace_id=ok["id"], parent_run_id=ok["id"], run_type="llm", start_time=now - 100, end_time=now - 99.5,
              outputs=llm_out(5, 5), extra={"metadata": {"ls_model_name": "fake-model"}})
    client.post("/runs/batch", json={"post": [ok, bad, llm]}, headers=H)
    s = client.get("/api/projects/proj/stats", params={"since": now - 3600}).json()
    assert (s["traces"], s["errors"], s["error_rate"], s["total_tokens"]) == (2, 1, 0.5, 10)
    assert s["p50"] == 1 and s["p99"] == 3
    d = client.get("/api/projects/proj/dashboard", params={"since": now - 3600, "until": now}).json()
    assert len(d["series"]) == 60 and d["series"][-1]["t"] < now  # no empty bucket past the range
    assert sum(b["traces"] for b in d["series"]) == 2 and sum(b["errors"] for b in d["series"]) == 1
    assert sum(b["tokens"] for b in d["series"]) == 10
    assert d["top"]["llm"][0]["name"] == "fake-model"


def test_delete_project(client):
    client.post("/runs/batch", json={"post": [run()]}, headers=H)
    assert client.delete("/api/projects/proj").status_code == 200
    assert client.get("/api/projects").json() == []


def test_retention(client):
    store = client.app.state.store
    old, new = run(start_time="2020-01-01T00:00:00"), run(start_time=time.time())
    old_child = run(trace_id=old["id"], parent_run_id=old["id"], start_time="2020-01-01T00:00:01")
    client.post("/runs/batch", json={"post": [old, old_child, new]}, headers=H)
    assert store.delete_older_than(time.time() - 30 * 86400) == 2
    assert [t["id"] for t in traces(client)] == [new["id"]]
