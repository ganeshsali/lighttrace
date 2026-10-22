"""Storage and the UI API. Runs go straight into the store here; the receiver (/v1/traces) that
turns spans into these runs is tested in test_otel*.py."""
import time
import uuid

T0 = 1767225600.0  # 2026-01-01 00:00:00 UTC


def run(**kw):
    rid = kw.pop("id", None) or str(uuid.uuid4())
    return {"id": rid, "trace_id": kw.pop("trace_id", rid), "name": "root", "run_type": "chain", "start_time": T0,
            "project": "proj", "inputs": {"input": "hi"}, **kw}


def ingest(client, runs):
    client.app.state.store.upsert_runs(runs)


def traces(client, **params):
    return client.get("/api/projects/proj/runs", params={"scope": "traces", **params}).json()["items"]


def tokens(i, o):
    return {"input_tokens": i, "output_tokens": o, "total_tokens": i + o}


FAKE_MODEL = {"invocation_params": {"model": "fake-model"}}


def test_ui_needs_login(client):
    assert client.get("/api/projects").json() == []
    client.cookies.clear()
    assert client.get("/api/projects").status_code == 401
    assert client.post("/api/login", json={"password": "wrong"}).status_code == 401


def test_writes_need_the_csrf_header(client):
    r = run()
    ingest(client, [r])
    assert client.delete(f"/api/traces/{r['id']}", headers={"x-lighttrace": ""}).status_code == 403
    assert client.delete(f"/api/traces/{r['id']}").status_code == 200
    assert client.get(f"/api/traces/{r['id']}").status_code == 404


def test_partial_update_before_full_run(client):
    """Updates merge whatever order they arrive in (OTel log events can come before their span)."""
    r = run()
    patch = {"id": r["id"], "trace_id": r["id"], "end_time": T0 + 2.5, "outputs": {"output": "done"},
             "extra": {"metadata": {"b": 2}}}
    ingest(client, [patch])
    r["extra"] = {"metadata": {"a": 1}}
    ingest(client, [r])

    (stored,) = client.get(f"/api/traces/{r['id']}").json()["runs"]
    assert stored["status"] == "success" and stored["project"] == "proj"
    assert stored["outputs"] == {"output": "done"} and stored["inputs"] == {"input": "hi"}
    assert stored["extra"]["metadata"] == {"a": 1, "b": 2}
    assert stored["end_time"] - stored["start_time"] == 2.5 and stored["latency"] == 2.5
    assert client.get("/api/projects").json()[0]["trace_count"] == 1


def test_error_from_a_later_update(client):
    r = run()
    ingest(client, [r])
    ingest(client, [{"id": r["id"], "end_time": T0 + 1, "error": "Boom\nTraceback"}])
    t = traces(client)[0]
    assert t["status"] == "error" and t["error"].startswith("Boom")
    assert traces(client, status="success") == []


def test_tokens_and_cost_summed_per_trace(client):
    root = run(end_time=T0 + 1)
    child = run(trace_id=root["id"], parent_run_id=root["id"], run_type="llm", extra=FAKE_MODEL, **tokens(3, 4))
    # a LangChain model whose name is only in LangChain's run metadata
    child2 = run(trace_id=root["id"], parent_run_id=root["id"], run_type="llm",
                 extra={"metadata": {"ls_model_name": "other-model"}}, **tokens(1, 2))
    ingest(client, [root, child, child2])
    t = traces(client)[0]
    assert t["total_tokens"] == 10
    assert abs(t["cost"] - (3 * 1.0 + 4 * 2.0) / 1e6) < 1e-12  # only the priced model counts
    assert traces(client, min_tokens=11) == [] and len(traces(client, min_tokens=10)) == 1
    llm = client.get("/api/projects/proj/runs", params={"scope": "runs", "run_type": "llm"}).json()["items"]
    assert sorted(x["model"] for x in llm) == ["fake-model", "other-model"]


def test_model_call_inside_a_root_model_call_counts_once(client):
    """A raw client call traced twice (the listener's span, and the SDK's own span inside it, as the
    Anthropic SDK does). The inner span points at the root span's own id, which no row has."""
    root = run(run_type="llm", end_time=T0 + 1, **tokens(3, 4))
    inner = run(trace_id=root["id"], parent_run_id=str(uuid.uuid4()), run_type="llm", **tokens(3, 4))
    ingest(client, [root, inner])
    assert traces(client)[0]["total_tokens"] == 7


def test_trace_first_token_comes_from_its_llm_run(client):
    """A graph root never streams; its first token is the first streamed LLM token inside it."""
    root = run(start_time=T0, end_time=T0 + 5,
               extra={"metadata": {"thread_id": "ft"}})
    llm = run(trace_id=root["id"], parent_run_id=root["id"], run_type="llm", start_time=T0 + 1,
              end_time=T0 + 3, **tokens(1, 1),
              events=[{"name": "start", "time": T0 + 1},
                      {"name": "new_token", "time": T0 + 1.4},
                      {"name": "new_token", "time": T0 + 1.5}])
    ingest(client, [root, llm])
    assert round(traces(client)[0]["first_token_time"], 6) == 1.4
    runs = client.get(f"/api/traces/{root['id']}").json()["runs"]
    # the root counts from the trace start, the llm run from its own start
    assert {r["run_type"]: round(r["first_token_time"], 6) for r in runs} == {"chain": 1.4, "llm": 0.4}
    turn = client.get("/api/projects/proj/thread", params={"id": "ft"}).json()["turns"][0]
    assert round(turn["first_token_time"], 6) == 1.4


def test_threads_group_turns(client):
    turns = [run(start_time=T0 + i, end_time=T0 + i + 0.5,
                 inputs={"input": f"q{i}"}, outputs={"output": f"a{i}"},
                 extra={"metadata": {"thread_id": "th"}}) for i in range(3)]
    other = run(extra={"metadata": {"session_id": "s2"}})
    ingest(client, [*turns, other])
    items = client.get("/api/projects/proj/threads").json()["items"]
    th = next(i for i in items if i["thread_id"] == "th")
    assert th["turns"] == 3 and th["first_input"] == "q0" and th["last_output"] == "a2" and th["p50"] == 0.5
    assert {i["thread_id"] for i in items} == {"th", "s2"}
    thread = client.get("/api/projects/proj/thread", params={"id": "th"}).json()
    assert [t["inputs"]["input"] for t in thread["turns"]] == ["q0", "q1", "q2"]
    assert client.get("/api/projects/proj/thread", params={"id": "nope"}).status_code == 404


def test_filters_latency_tag_name_and_saved(client):
    fast = run(end_time=T0 + 0.1, tags=["prod"], name="fast")
    slow = run(end_time=T0 + 5, name="slow")
    ingest(client, [fast, slow])
    assert [t["name"] for t in traces(client, min_latency=1)] == ["slow"]
    assert [t["name"] for t in traces(client, max_latency=1)] == ["fast"]
    assert [t["name"] for t in traces(client, tag="prod")] == ["fast"]
    assert [t["name"] for t in traces(client, name="SLO")] == ["slow"]
    assert client.get("/api/projects/proj/runs", params={"meta": "bad key:x"}).status_code == 400
    saved = {"name": "slow ones", "query": {"min_latency": "1"}}
    fid = client.post("/api/projects/proj/filters", json=saved).json()["id"]
    assert client.get("/api/projects/proj/filters").json() == [{"id": fid, "name": "slow ones",
                                                                "query": {"min_latency": "1"}}]
    client.delete(f"/api/filters/{fid}")
    assert client.get("/api/projects/proj/filters").json() == []


def test_stats_and_dashboard(client):
    now = time.time()
    ok = run(start_time=now - 100, end_time=now - 99)
    bad = run(start_time=now - 50, end_time=now - 47, error="x")
    llm = run(trace_id=ok["id"], parent_run_id=ok["id"], run_type="llm", start_time=now - 100, end_time=now - 99.5,
              extra=FAKE_MODEL, **tokens(5, 5))
    ingest(client, [ok, bad, llm])
    s = client.get("/api/projects/proj/stats", params={"since": now - 3600}).json()
    assert (s["traces"], s["errors"], s["error_rate"], s["total_tokens"]) == (2, 1, 0.5, 10)
    assert s["p50"] == 1 and s["p99"] == 3
    d = client.get("/api/projects/proj/dashboard", params={"since": now - 3600, "until": now}).json()
    assert len(d["series"]) == 60 and d["series"][-1]["t"] < now  # no empty bucket past the range
    assert sum(b["traces"] for b in d["series"]) == 2 and sum(b["errors"] for b in d["series"]) == 1
    assert sum(b["tokens"] for b in d["series"]) == 10
    assert d["top"]["llm"][0]["name"] == "fake-model"


def test_delete_project(client):
    ingest(client, [run()])
    assert client.delete("/api/projects/proj").status_code == 200
    assert client.get("/api/projects").json() == []


def test_retention(client):
    store = client.app.state.store
    old, new = run(start_time=T0 - 6 * 365 * 86400), run(start_time=time.time())
    old_child = run(trace_id=old["id"], parent_run_id=old["id"], start_time=T0 - 6 * 365 * 86400 + 1)
    ingest(client, [old, old_child, new])
    assert store.delete_older_than(time.time() - 30 * 86400) == 2
    assert [t["id"] for t in traces(client)] == [new["id"]]
