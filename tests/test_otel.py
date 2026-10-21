import json

from conftest import API_KEY

H = {"x-api-key": API_KEY, "x-lighttrace-project": "proj"}
TRACE = "0af7651916cd43dd8448eb211c80319c"
ROOT, LLM, TOOL = "b7ad6b7169203331", "00f067aa0ba902b7", "1111111111111111"
T0 = 1_767_225_600_000_000_000  # 2026-01-01, in ns


def kv(key, value):
    if isinstance(value, bool):
        v = {"boolValue": value}
    elif isinstance(value, int):
        v = {"intValue": str(value)}
    elif isinstance(value, list):
        v = {"arrayValue": {"values": [{"stringValue": x} for x in value]}}
    else:
        v = {"stringValue": value}
    return {"key": key, "value": v}


def span(span_id, parent, name, start_ms, end_ms, attrs, **kw):
    return {"traceId": TRACE, "spanId": span_id, "parentSpanId": parent, "name": name,
            "startTimeUnixNano": str(T0 + start_ms * 1_000_000), "endTimeUnixNano": str(T0 + end_ms * 1_000_000),
            "attributes": [kv(k, v) for k, v in attrs.items()], **kw}


def test_otlp_json_openinference_and_genai(client):
    spans = [
        # Children first: exporters send each span when it ends, so the root usually arrives last.
        span(TOOL, ROOT, "search", 1500, 1800, {"gen_ai.operation.name": "execute_tool",
                                                "gen_ai.tool.call.arguments": '{"q": "weather"}'},
             status={"code": 2, "message": "boom"},
             events=[{"name": "exception", "timeUnixNano": str(T0),
                      "attributes": [kv("exception.type", "ValueError"), kv("exception.message", "bad q")]}]),
        span(LLM, ROOT, "ChatModel", 100, 1400, {
            "openinference.span.kind": "LLM", "llm.model_name": "fake-model",
            "llm.input_messages.0.message.role": "user", "llm.input_messages.0.message.content": "hi",
            "llm.output_messages.0.message.role": "assistant", "llm.output_messages.0.message.content": "hello",
            "llm.token_count.prompt": 10, "llm.token_count.completion": 5, "llm.temperature": "0.2"}),
        span(ROOT, "", "agent", 0, 2000, {
            "openinference.span.kind": "CHAIN", "input.value": '{"question": "weather?"}', "output.value": "sunny",
            "metadata": json.dumps({"thread_id": "t1"}), "tag.tags": ["prod"]}),
    ]
    body = {"resourceSpans": [{"resource": {"attributes": [kv("service.name", "ignored-by-header")]},
                               "scopeSpans": [{"scope": {"name": "openinference"}, "spans": spans}]}]}
    r = client.post("/v1/traces", json=body, headers=H)
    assert r.status_code == 200, r.text

    [t] = client.get("/api/projects/proj/runs", params={"scope": "traces"}).json()["items"]
    root_id = "0af76519-16cd-43dd-8448-eb211c80319c"
    assert t["id"] == root_id and t["name"] == "agent" and t["thread_id"] == "t1" and t["tags"] == ["prod"]
    assert t["input_preview"] == "weather?" and t["output_preview"] == "sunny"
    assert t["latency"] == 2.0 and (t["input_tokens"], t["output_tokens"], t["total_tokens"]) == (10, 5, 15)
    assert abs(t["cost"] - (10 * 1.0 + 5 * 2.0) / 1e6) < 1e-12

    runs = client.get(f"/api/traces/{root_id}").json()["runs"]
    assert [x["name"] for x in runs] == ["agent", "ChatModel", "search"]  # tree order, not arrival order
    assert all(x["parent_run_id"] == root_id for x in runs[1:])
    llm, tool = runs[1], runs[2]
    assert llm["run_type"] == "llm" and llm["model"] == "fake-model"
    assert llm["inputs"] == {"messages": [{"role": "user", "content": "hi"}]}
    assert llm["extra"]["metadata"] == {"llm.temperature": "0.2"}  # unmapped attrs kept
    assert llm["extra"]["runtime"] == {"service.name": "ignored-by-header", "otel.scope": "openinference"}
    assert tool["run_type"] == "tool" and tool["status"] == "error" and tool["error"] == "ValueError: bad q"
    assert tool["inputs"] == {"q": "weather"}
    assert client.get("/api/projects/proj/thread", params={"id": "t1"}).json()["stats"]["turns"] == 1


def test_otlp_rejects_bad_key_type_and_payload(client):
    assert client.post("/v1/traces", json={}, headers={"x-api-key": "nope"}).status_code == 401
    assert client.post("/v1/traces", content=b"x", headers={**H, "content-type": "text/plain"}).status_code == 415
    bad = {"resourceSpans": [{"scopeSpans": [{"spans": [{"traceId": "zz"}]}]}]}
    assert client.post("/v1/traces", json=bad, headers=H).status_code == 400
    assert client.post("/v1/traces", content=b"\xff\x00", headers={**H, "content-type": "application/x-protobuf"}
                       ).status_code == 400


def test_real_otel_sdk_protobuf_gzip(live_server):
    """The real OTel exporter (protobuf + gzip) with GenAI-convention attributes."""
    from opentelemetry.exporter.otlp.proto.http import Compression
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.trace import Status, StatusCode

    url, app = live_server
    provider = TracerProvider(resource=Resource.create({"service.name": "otel-app"}))
    provider.add_span_processor(SimpleSpanProcessor(OTLPSpanExporter(
        endpoint=f"{url}/v1/traces", headers={"x-api-key": API_KEY}, compression=Compression.Gzip)))
    tracer = provider.get_tracer("test")

    with tracer.start_as_current_span("invoke_agent", attributes={"gen_ai.conversation.id": "c9"}) as root:
        with tracer.start_as_current_span("chat gpt", attributes={
                "gen_ai.operation.name": "chat", "gen_ai.request.model": "gpt-x",
                "gen_ai.usage.input_tokens": 7, "gen_ai.usage.output_tokens": 3,
                "gen_ai.input.messages": json.dumps([{"role": "user", "parts": [{"type": "text", "content": "yo"}]}]),
                "gen_ai.output.messages": json.dumps([{"role": "assistant", "parts": [
                    {"type": "tool_call", "id": "c1", "name": "lookup", "arguments": {"k": 1}}]}])}):
            pass
        root.set_status(Status(StatusCode.ERROR, "agent failed"))
    provider.shutdown()

    store = app.state.store
    [t] = store.runs("otel-app", {}, "traces")["items"]
    assert t["thread_id"] == "c9" and t["status"] == "error" and t["total_tokens"] == 10
    runs = store.trace(t["id"])["runs"]
    assert [r["name"] for r in runs] == ["invoke_agent", "chat gpt"]
    llm = runs[1]
    assert llm["run_type"] == "llm" and llm["model"] == "gpt-x" and llm["parent_run_id"] == t["id"]
    assert llm["inputs"] == {"messages": [{"role": "user", "content": "yo"}]}
    assert llm["outputs"]["messages"][0]["tool_calls"] == [{"name": "lookup", "id": "c1", "args": {"k": 1}}]
