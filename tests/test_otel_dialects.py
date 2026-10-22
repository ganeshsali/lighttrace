"""Agent frameworks and OTel dialects. Fixtures in tests/fixtures are real exporter payloads
(captured from the SDK named in the test, protobuf converted to OTLP/JSON) unless a test says otherwise."""
import json
import uuid
from pathlib import Path


from conftest import API_KEY
from test_otel import TRACE, kv, span

FIX = Path(__file__).parent / "fixtures"
H = {"x-api-key": API_KEY, "x-lighttrace-project": "proj"}


def post(client, body, path="/v1/traces"):
    r = client.post(path, json=body, headers=H)
    assert r.status_code == 200, r.text


def fixture(name):
    return json.loads((FIX / name).read_text())


def only_trace(client):
    [t] = client.get("/api/projects/proj/runs", params={"scope": "traces"}).json()["items"]
    return t, client.get(f"/api/traces/{t['id']}").json()["runs"]


def resource(service, spans):
    return {"resource": {"attributes": [kv("service.name", service)]}, "scopeSpans": [{"spans": spans}]}


def test_remote_parent_becomes_root_per_service(client):
    """Agent A calls server B twice in one trace (A2A / out-of-process MCP). B's spans have a remote parent."""
    a_root, a_call1, a_call2 = "a000000000000001", "a000000000000002", "a000000000000003"
    remote = {"flags": 0x301}
    b_spans = [
        span("b000000000000011", "b000000000000001", "db lookup", 120, 150, {}),  # local child of entry 1
        span("b000000000000001", a_call1, "tools/call search", 100, 200, {"input.value": "q1"}, **remote),
        span("b000000000000002", a_call2, "tools/call search", 300, 450, {}, **remote),
    ]
    post(client, {"resourceSpans": [resource("server-b", b_spans)]})  # B's spans arrive first
    post(client, {"resourceSpans": [resource("agent-a", [
        span(a_call1, a_root, "call", 90, 210, {}), span(a_call2, a_root, "call", 290, 460, {}),
        span(a_root, "", "agent", 0, 500, {"openinference.span.kind": "AGENT"})])]})

    traces = client.get("/api/projects/proj/runs", params={"scope": "traces"}).json()["items"]
    by_name = {t["name"]: t for t in traces}
    assert set(by_name) == {"agent", "tools/call search"}  # one trace per service, no id collision
    a, b = by_name["agent"], by_name["tools/call search"]
    assert a["id"] == str(uuid.uuid5(uuid.UUID(TRACE), "agent-a"))
    assert a["metadata"]["otel.trace_id"] == b["metadata"]["otel.trace_id"] == TRACE  # find both parts
    assert round(b["start_time"] - a["start_time"], 6) == 0.1 and round(b["latency"], 6) == 0.35  # widened over both entries
    assert b["input_preview"] == "q1"

    runs = client.get(f"/api/traces/{b['id']}").json()["runs"]
    assert [(r["name"], r["parent_run_id"] == b["id"]) for r in runs] == [
        ("tools/call search", False), ("tools/call search", True), ("db lookup", False), ("tools/call search", True)]
    assert runs[1]["extra"]["metadata"]["otel.remote_parent_span_id"] == a_call1
    assert runs[2]["parent_run_id"] == runs[1]["id"]
    assert [r["run_type"] for r in client.get(f"/api/traces/{a['id']}").json()["runs"]] == ["agent", "chain", "chain"]


def test_vercel_ai_sdk(client):
    """ai@5 generateText with MockLanguageModelV2, one tool round trip, experimental_telemetry on."""
    post(client, fixture("vercel_ai.json"))
    t, runs = only_trace(client)
    assert t["name"] == "ai.generateText" and t["thread_id"] == "t-v" and t["total_tokens"] == 30
    assert t["input_preview"] == "Weather in Pune?" and t["output_preview"] == "It is sunny in Pune."
    assert [(r["name"], r["run_type"]) for r in runs] == [
        ("ai.generateText", "chain"), ("ai.generateText.doGenerate", "llm"), ("weather", "tool"),
        ("ai.generateText.doGenerate", "llm")]
    first, tool, second = runs[1:]
    assert first["model"] == "mock-model-id" and (first["input_tokens"], first["output_tokens"]) == (10, 5)
    assert first["inputs"]["messages"] == [{"role": "system", "content": "You are terse."},
                                           {"role": "user", "content": "Weather in Pune?"}]
    assert first["outputs"]["messages"] == [{"role": "assistant", "content": "", "tool_calls": [
        {"name": "weather", "id": "call_1", "args": {"city": "Pune"}}]}]
    assert second["inputs"]["messages"][3]["tool_call_id"] == "call_1"
    assert json.loads(second["inputs"]["messages"][3]["content"]) == {"city": "Pune", "sky": "sunny"}
    assert tool["inputs"] == {"city": "Pune"} and tool["outputs"] == {"city": "Pune", "sky": "sunny"}
    assert first["extra"]["invocation_params"]["maxRetries"] == 2
    assert first["extra"]["metadata"]["ai.model.provider"] == "mock-provider"  # unmapped stays


def test_langfuse_sdk_v3(client):
    """langfuse 3.15 Python SDK spans (agent > generation + tool marked level=ERROR), sent to /v1/traces."""
    post(client, fixture("langfuse_v3.json"))
    t, runs = only_trace(client)
    assert t["name"] == "support-agent" and t["thread_id"] == "sess-1" and t["tags"] == ["prod", "beta"]
    assert t["metadata"]["tenant"] == "acme" and t["metadata"]["user.id"] == "u-1"
    assert t["input_preview"] == "where is my order?" and t["output_preview"] == "It shipped."
    root, gen, tool = runs
    assert root["run_type"] == "agent" and gen["run_type"] == "llm" and tool["run_type"] == "tool"
    assert gen["model"] == "fake-model" and (gen["input_tokens"], gen["output_tokens"], gen["total_tokens"]) == (12, 4, 16)
    assert gen["inputs"] == {"messages": [{"role": "user", "content": "where is my order?"}]}
    assert gen["outputs"] == {"messages": [{"role": "assistant", "content": "Let me check."}]}
    assert gen["extra"]["invocation_params"]["temperature"] == 0.1 and gen["extra"]["metadata"]["step"] == 1
    assert tool["status"] == "error" and tool["error"] == "tool exploded" and tool["inputs"] == {"order": 42}


def test_agent_spans(client):
    """OpenInference AGENT and GenAI invoke_agent/create_agent map to the agent run type."""
    post(client, {"resourceSpans": [resource("svc", [
        span("1000000000000001", "", "root", 0, 10, {"openinference.span.kind": "AGENT"}),
        span("1000000000000002", "1000000000000001", "invoke_agent helper", 1, 5, {"gen_ai.operation.name": "invoke_agent"}),
        span("1000000000000003", "1000000000000001", "create_agent x", 6, 7, {"gen_ai.operation.name": "create_agent"})])]})
    _, runs = only_trace(client)
    assert [r["run_type"] for r in runs] == ["agent", "agent", "agent"]
    agents = client.get("/api/projects/proj/runs", params={"scope": "runs", "run_type": "agent"}).json()["items"]
    assert len(agents) == 3


# ---------------------------------------------------------------- transports

def oai2(client, logs_first):
    """opentelemetry-instrumentation-openai-v2 2.4b0 (+ util-genai 0.4b0), OpenAI SDK against a stub
    server: prompt/response content comes as log events, each chat span is its own root."""
    traces, logs = fixture("oai2_traces.json"), fixture("oai2_logs.json")
    for body, path in ([(logs, "/v1/logs"), (traces, "/v1/traces")] if logs_first else
                       [(traces, "/v1/traces"), (logs, "/v1/logs")]):
        post(client, body, path)
    items = client.get("/api/projects/proj/runs", params={"scope": "traces"}).json()["items"]
    assert len(items) == 2 and all(t["run_type"] == "llm" and t["total_tokens"] == 16 for t in items)
    second, first = (client.get(f"/api/traces/{t['id']}").json()["runs"] for t in items)
    assert [len(r) for r in (first, second)] == [1, 1]  # no leftover placeholder rows
    return first[0], second[0]


def test_genai_log_events_before_and_after_spans(client):
    for logs_first in (True, False):
        first, second = oai2(client, logs_first)
        assert first["inputs"]["messages"] == [{"role": "system", "content": "Be brief."},
                                               {"role": "user", "content": "Weather in Pune?"}]
        assert first["outputs"]["messages"] == [{"role": "assistant", "content": None, "tool_calls": [
            {"name": "weather", "id": "call_1", "args": {"city": "Pune"}}]}]
        assert second["inputs"]["messages"][2]["tool_calls"][0]["id"] == "call_1"
        assert second["inputs"]["messages"][3] == {"role": "tool", "content": "sunny", "tool_call_id": "call_1"}
        assert second["outputs"]["messages"] == [{"role": "assistant", "content": "Hello from the stub."}]
        client.delete("/api/projects/proj")


def test_genai_span_events_and_child_log_events(client):
    """Documented shapes (no real payload): new-style events on a span, old-style gen_ai.content.*
    events, and a log event for a child span that arrives before the span."""
    root, child, old = "2000000000000001", "2000000000000002", "2000000000000003"
    ev = lambda name, **attrs: {"name": name, "timeUnixNano": "1", "attributes": [kv(k, v) for k, v in attrs.items()]}
    log = {"resourceLogs": [{"resource": {"attributes": [kv("service.name", "svc")]}, "scopeLogs": [{"logRecords": [
        {"eventName": "gen_ai.user.message", "traceId": TRACE, "spanId": child, "body": {"kvlistValue": {"values": [
            kv("content", "from a log")]}}},
        {"attributes": [kv("event.name", "gen_ai.choice")], "traceId": TRACE, "spanId": child,
         "body": {"kvlistValue": {"values": [{"key": "message", "value": {"kvlistValue": {"values": [
             kv("content", "answer")]}}}]}}},
        {"eventName": "something.else", "traceId": TRACE, "spanId": child, "body": {"stringValue": "ignored"}}]}]}]}
    post(client, log, "/v1/logs")
    post(client, {"resourceSpans": [resource("svc", [
        span(root, "", "root", 0, 10, {}),
        span(child, root, "chat", 1, 5, {"gen_ai.operation.name": "chat"}),
        span(old, root, "completion", 6, 9, {"gen_ai.operation.name": "chat"}, events=[
            ev("gen_ai.content.prompt", **{"gen_ai.prompt": json.dumps([{"role": "user", "content": "old q"}])}),
            ev("gen_ai.content.completion", **{"gen_ai.completion": "old a"})])])]})
    _, runs = only_trace(client)
    by = {r["name"]: r for r in runs}
    assert by["chat"]["inputs"] == {"messages": [{"role": "user", "content": "from a log"}]}
    assert by["chat"]["outputs"] == {"messages": [{"role": "assistant", "content": "answer"}]}
    assert by["completion"]["inputs"] == {"messages": [{"role": "user", "content": "old q"}]}
    assert by["completion"]["outputs"] == {"messages": [{"role": "assistant", "content": "old a"}]}


def test_otlp_json_base64_ids_and_metrics(client):
    """OTLP/JSON with base64 ids (what protobuf-to-JSON gives) is detected; /v1/metrics is accepted and dropped."""
    import base64
    b64 = lambda h: base64.b64encode(bytes.fromhex(h)).decode()
    s = span(b64("3000000000000001"), "", "b64-root", 0, 1, {})
    s["traceId"] = b64(TRACE)
    post(client, {"resourceSpans": [resource("svc", [s])]})
    t, _ = only_trace(client)
    assert t["name"] == "b64-root" and t["metadata"]["otel.trace_id"] == TRACE
    assert t["metadata"]["otel.span_id"] == "3000000000000001"
    post(client, {"resourceMetrics": []}, "/v1/metrics")
    r = client.post("/v1/metrics", content=b"", headers={**H, "content-type": "application/x-protobuf"})
    assert r.status_code == 200 and r.content == b""
    assert client.post("/v1/metrics", json={}).status_code == 401


# ---------------------------------------------------------------- dialects (real payloads, see FIX)

def dialect(client, name):
    """Post tests/fixtures/<name>_logs.json (if any) and <name>_traces.json; return {run name: run}
    of the one trace, plus the trace list item."""
    if (FIX / f"{name}_logs.json").exists():
        post(client, fixture(f"{name}_logs.json"), "/v1/logs")
    post(client, fixture(f"{name}_traces.json"))
    t, runs = only_trace(client)
    return t, {r["name"]: r for r in runs}


def test_openllmetry(client):
    """traceloop-sdk 0.62.4 (@workflow + @task around an OpenAI call to a stub server)."""
    t, runs = dialect(client, "openllmetry")
    assert (t["name"], t["input_preview"], t["output_preview"], t["total_tokens"]) == (
        "qa.workflow", "Weather in Pune?", "Hello from the stub.", 16)
    chat = runs["openai.chat"]
    assert chat["run_type"] == "llm" and chat["model"] == "gpt-4o-mini" and chat["total_tokens"] == 16
    assert chat["inputs"]["messages"][1] == {"role": "user", "content": "Weather in Pune?"}
    assert runs["ask.task"]["inputs"] == {"input": "Weather in Pune?"}


def test_openlit(client):
    """openlit 1.45.0 with OpenAI against a stub server: content as span attributes and in a
    gen_ai.client.inference.operation.details log (the span's own content wins, no duplicates)."""
    t, runs = dialect(client, "openlit")
    chat = runs["chat gpt-4o-mini"]
    assert chat["inputs"]["messages"] == [{"role": "system", "content": "Be brief."},
                                          {"role": "user", "content": "Weather in Pune?"}]
    assert chat["outputs"]["messages"] == [{"role": "assistant", "content": "Hello from the stub."}]
    assert chat["total_tokens"] == 16 and chat["extra"]["metadata"]["gen_ai.usage.cost"] > 0  # unmapped stays


def test_openlit_operation_details_log_alone(client):
    """Documented: with content only in the log (no span attributes), the log fills the span."""
    body = fixture("openlit_traces.json")
    for rs in body["resourceSpans"]:
        for ss in rs["scopeSpans"]:
            for s in ss["spans"]:
                s["attributes"] = [x for x in s["attributes"] if "messages" not in x["key"]
                                   and "instructions" not in x["key"]]
    post(client, body)
    post(client, fixture("openlit_logs.json"), "/v1/logs")
    _, runs = only_trace(client)
    assert runs[0]["outputs"]["messages"] == [{"role": "assistant", "content": "Hello from the stub."}]
    assert runs[0]["inputs"]["messages"][1]["content"] == "Weather in Pune?"


def test_langsmith_otel_mode(client):
    """langsmith 0.14 with LANGSMITH_OTEL_ENABLED + LANGSMITH_OTEL_ONLY (@traceable + a fake chat model)."""
    t, runs = dialect(client, "langsmith_otel")
    assert t["thread_id"] == "t-ls" and t["tags"] == ["prod"] and t["output_preview"] == "It is sunny."
    assert runs["weather_tool"]["run_type"] == "tool" and runs["weather_tool"]["outputs"] == {"sky": "sunny"}
    llm = runs["GenericFakeChatModel"]
    assert llm["run_type"] == "llm" and llm["total_tokens"] == 12
    assert llm["inputs"]["messages"][0][0]["kwargs"]["content"] == "Weather in Pune?"  # LangChain-serialized
    assert llm["extra"]["metadata"]["ls_provider"] == "genericfakechatmodel"


def test_langsmith_otel_mode_error(client):
    """A failing LangGraph run in LangSmith OTel mode: the SDK marks the root span OK but attaches the
    exception event; the trace must still show as an error."""
    t, runs = dialect(client, "langsmith_otel_error")
    assert t["status"] == "error" and "ERP API timeout" in t["error"]
    assert runs["get_stock"]["status"] == "success"


def test_mlflow(client):
    """mlflow-tracing 3.17 OTLP export (@mlflow.trace with AGENT / TOOL / CHAT_MODEL span types)."""
    t, runs = dialect(client, "mlflow")
    assert t["run_type"] == "agent" and t["output_preview"] == "It is sunny." and t["total_tokens"] == 12
    assert runs["weather"]["run_type"] == "tool" and runs["weather"]["inputs"] == {"city": "Pune"}
    chat = runs["chat"]
    assert chat["run_type"] == "llm" and (chat["input_tokens"], chat["output_tokens"]) == (9, 3)
    assert chat["inputs"] == {"messages": [{"role": "user", "content": "Weather in Pune?"}]}


def test_google_adk(client):
    """google-adk 2.12 LlmAgent with a fake BaseLlm and one function tool. call_llm is the llm run
    (content + usage); ADK 2's generate_content child repeats the usage and is not counted again."""
    t, runs = dialect(client, "google_adk")
    assert t["name"] == "invocation" and t["total_tokens"] == 28
    assert runs["invoke_agent weather_agent"]["run_type"] == "agent"
    all_runs = client.get(f"/api/traces/{t['id']}").json()["runs"]
    assert [r["name"] for r in all_runs if r["run_type"] == "llm"] == ["call_llm", "call_llm"]
    call = next(r for r in all_runs if r["name"] == "call_llm" and "tool_calls" in r["outputs"]["messages"][0])
    assert call["model"] == "fake-gemini" and call["total_tokens"] == 14
    assert call["inputs"]["messages"][0]["role"] == "system"
    assert call["inputs"]["messages"][1] == {"role": "user", "content": "Weather in Pune?"}
    assert call["outputs"]["messages"][0]["tool_calls"] == [{"name": "weather", "id": None, "args": {"city": "Pune"}}]
    tool = runs["execute_tool weather"]
    assert tool["run_type"] == "tool" and tool["inputs"] == {"city": "Pune"} and tool["outputs"] == {"sky": "sunny"}


def test_google_adk_1_10(client):
    """google-adk 1.10 (examples/google_adk_demo.py): no generate_content span and no invoke_agent
    operation; call_llm is the llm run and "agent_run [name]" the agent."""
    t, runs = dialect(client, "google_adk_1.10")
    assert t["total_tokens"] == 28 and runs["agent_run [weather_agent]"]["run_type"] == "agent"
    assert runs["call_llm"]["run_type"] == "llm" and runs["call_llm"]["model"] == "fake-gemini"


def test_openinference_retriever_and_embedding(client):
    """Documented OpenInference attributes (LlamaIndex fills them; its real run is in test_frameworks)."""
    post(client, {"resourceSpans": [resource("svc", [
        span("4000000000000001", "", "query", 0, 10, {"openinference.span.kind": "CHAIN", "input.value": "q?"}),
        span("4000000000000002", "4000000000000001", "retrieve", 1, 5, {
            "openinference.span.kind": "RETRIEVER", "output.value": "[<NodeWithScore ...>]",
            "retrieval.documents.0.document.content": "Pune is sunny.", "retrieval.documents.0.document.id": "d1",
            "retrieval.documents.0.document.score": 0.9, "retrieval.documents.0.document.metadata": '{"file": "a.txt"}'}),
        span("4000000000000003", "4000000000000002", "embed", 1, 2, {
            "openinference.span.kind": "EMBEDDING", "embedding.model_name": "mock",
            "embedding.embeddings.0.embedding.text": "q?", "embedding.embeddings.0.embedding.vector": [0.1, 0.2]})])]})
    _, runs = only_trace(client)
    by = {r["name"]: r for r in runs}
    assert by["retrieve"]["outputs"] == {"documents": [{"page_content": "Pune is sunny.", "metadata": {
        "file": "a.txt", "id": "d1", "score": 0.9}}]}
    assert by["embed"]["inputs"] == {"input": ["q?"]} and by["embed"]["run_type"] == "embedding"
    assert not any("vector" in k for k in by["embed"]["extra"]["metadata"])


def pydantic_ai_checks(t, runs, tool_name):
    assert t["run_type"] == "agent" and t["total_tokens"] == 127
    assert t["input_preview"] == "Weather in Pune?" and t["output_preview"] == '{"weather": "sunny"}'
    chats = [r for r in runs.values() if r["run_type"] == "llm"]
    assert chats and all(r["model"] == "test" for r in chats)
    assert runs[tool_name]["run_type"] == "tool" and runs[tool_name]["outputs"] == {"output": "sunny"}


def test_pydantic_ai(client):
    """pydantic-ai-slim 2.59 TestModel, instrumentation version 2 (no Logfire)."""
    pydantic_ai_checks(*dialect(client, "pydantic_ai_v2"), "running tool")


def test_pydantic_ai_logfire(client):
    """Same agent through logfire 5.2 (default instrumentation version): logfire.* attributes stay
    in metadata, and logfire.json_schema decodes the JSON-valued ones."""
    t, runs = dialect(client, "pydantic_ai_logfire")
    pydantic_ai_checks(t, runs, "execute_tool weather")
    meta = runs["invoke_agent agent"]["extra"]["metadata"]
    assert meta["logfire.msg"] == "agent run" and isinstance(meta["logfire.metrics"], str)
    assert isinstance(runs["chat test"]["extra"]["metadata"]["model_request_parameters"], dict)


def test_pydantic_ai_0_4_events(client):
    """pydantic-ai-slim 0.4.11 (instrumentation v1, event_mode="attributes"): messages as GenAI events in
    the `events` / `all_messages_events` attributes."""
    t, runs = dialect(client, "pydantic_ai_0.4")
    pydantic_ai_checks(t, runs, "running tool")
    chat = next(r for r in runs.values() if r["run_type"] == "llm")
    assert chat["inputs"]["messages"][1] == {"role": "user", "content": "Weather in Pune?"}
    assert "events" not in chat["extra"]["metadata"]


def test_nested_model_call_counts_once_and_first_token_events(client):
    """Framework LLM span with the client library's LLM span inside it (same call, traced twice):
    tokens count once; the client span's first-chunk event gives time to first token."""
    root, outer, inner = "5000000000000001", "5000000000000002", "5000000000000003"
    usage = {"gen_ai.operation.name": "chat", "gen_ai.request.model": "m", "gen_ai.usage.input_tokens": 12,
             "gen_ai.usage.output_tokens": 5}
    first = {"name": "First Token Stream Event", "timeUnixNano": str(int(span(inner, "", "x", 400, 0, {})[
        "startTimeUnixNano"]))}
    post(client, {"resourceSpans": [resource("svc", [
        span(inner, outer, "ChatCompletion", 100, 900, usage, events=[first]),
        span(outer, root, "ChatOpenAI", 50, 950, usage),
        span(root, "", "LangGraph", 0, 1000, {})])]})
    t, runs = only_trace(client)
    assert t["total_tokens"] == 17 and round(t["first_token_time"], 3) == 0.4
    assert [r["run_type"] for r in runs] == ["chain", "llm", "llm"]  # both stay visible
    d = client.get("/api/projects/proj/dashboard", params={"since": 0, "until": 2e9}).json()
    assert [(x["name"], x["count"]) for x in d["top"]["llm"]] == [("m", 1)]
    assert client.get("/api/projects/proj/stats").json()["total_tokens"] == 17


def test_openllmetry_chunk_events_give_first_token(client):
    chunk = lambda ms: {"name": "gen_ai.content.completion.chunk", "timeUnixNano": span(
        "6000000000000001", "", "x", ms, 0, {})["startTimeUnixNano"]}
    post(client, {"resourceSpans": [resource("svc", [span("6000000000000001", "", "openai.chat", 0, 900, {
        "gen_ai.operation.name": "chat"}, events=[chunk(300), chunk(350)])])]})
    t, _ = only_trace(client)
    assert round(t["first_token_time"], 3) == 0.3


def test_first_token_as_span_attribute(client):
    """The OTel GenAI attribute gen_ai.response.time_to_first_chunk (seconds; set by the OpenTelemetry
    LangChain instrumentation) and OpenLIT's gen_ai.server.time_to_first_token become the run's first token,
    and the root's comes from its model call."""
    post(client, {"resourceSpans": [resource("svc", [
        span("7000000000000001", "", "invoke_agent LangGraph", 0, 1000, {"gen_ai.operation.name": "invoke_agent"}),
        span("7000000000000002", "7000000000000001", "chat m", 100, 900, {
            "gen_ai.operation.name": "chat", "gen_ai.response.time_to_first_chunk": 0.48}),
        span("7000000000000003", "7000000000000001", "chat n", 920, 990, {
            "gen_ai.operation.name": "chat", "gen_ai.server.time_to_first_token": 0.02})])]})
    t, runs = only_trace(client)
    assert round(t["first_token_time"], 3) == 0.58  # first chunk of the first model call, from the request start
    assert [round(r["first_token_time"] or 0, 3) for r in runs] == [0.58, 0.48, 0.02]


def test_official_otel_langchain_instrumentation(client):
    """opentelemetry-instrumentation-genai-langchain 1.2b0 (OpenTelemetry's own), zero-code via
    opentelemetry-instrument: LangGraph + streamed ChatOpenAI against a stub server. Tokens and the
    official gen_ai.response.time_to_first_chunk arrive on the model call; the request gets both."""
    t, runs = dialect(client, "otel_genai_langchain")
    assert (t["name"], t["thread_id"], t["input_preview"], t["output_preview"]) == (
        "invoke_workflow LangGraph", "t-1", "Weather in Pune?", "It is sunny in Pune.")
    assert t["total_tokens"] == 17 and 0.4 < t["first_token_time"] < 0.6
    chat = runs["chat my-vllm-model"]
    assert chat["run_type"] == "llm" and (chat["input_tokens"], chat["output_tokens"]) == (12, 5)
    assert 0.4 < chat["first_token_time"] < 0.6 and chat["model"] == "my-vllm-model"
