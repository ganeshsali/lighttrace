"""lighttrace's one receiver: OTLP/HTTP (traces, plus GenAI log events) -> lighttrace runs.

Each span becomes one lighttrace run (see store.py).
Understands the OpenInference (Arize Phoenix) and OTel GenAI semantic conventions (attributes,
span events and log events), OpenLLMetry, OpenLIT, the Vercel AI SDK (`ai.*`), Langfuse
(`langfuse.*`), MLflow (`mlflow.*`), the LangSmith SDK's OTel mode (`langsmith.*`), Google ADK
(`gcp.vertex.agent.*`) and Pydantic AI / Logfire; every attribute it does not map lands in metadata.

Traces: one lighttrace trace per (OTel trace id, service.name), so several services that share
an OTel trace id (A2A, out-of-process MCP servers) never collide; each shows as its own trace,
findable together with the `otel.trace_id` metadata filter. A span whose parent is remote (in
another process) hangs under a stand-in root for its service's part of the trace.
"""

import base64
import json
import string
import uuid

from .store import THREAD_KEYS

KINDS = {"llm": "llm", "tool": "tool", "retriever": "retriever", "reranker": "retriever",
         "embedding": "embedding", "prompt": "prompt", "agent": "agent", "generation": "llm", "chat_model": "llm",
         "parser": "parser"}
GENAI_OPS = {"chat": "llm", "text_completion": "llm", "generate_content": "llm", "embeddings": "embedding",
             "execute_tool": "tool", "invoke_agent": "agent", "create_agent": "agent"}
SESSION_KEYS = ("session.id", "gen_ai.conversation.id", "langfuse.session.id")
META_PREFIXES = ("ai.telemetry.metadata.", "langfuse.observation.metadata.", "langfuse.trace.metadata.",
                 "langsmith.metadata.")
STATUS_ERROR = 2
REMOTE_PARENT = 0x300  # span flags: HAS_IS_REMOTE | IS_REMOTE


def parse(body, content_type, kind="traces"):
    """OTLP request body (protobuf or JSON) -> the Export*ServiceRequest as a JSON-style dict."""
    if "protobuf" not in content_type:
        return json.loads(body)
    from google.protobuf.json_format import MessageToDict
    from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

    msg = ExportTraceServiceRequest if kind == "traces" else ExportLogsServiceRequest
    return MessageToDict(msg.FromString(body), use_integers_for_enums=True)


def _resources(request, key, project):
    """(project, service, resource attrs, scope name, items) per scope in an OTLP request."""
    for rs in request.get(f"resource{key}") or []:
        resource = _attrs((rs.get("resource") or {}).get("attributes"))
        service = str(resource.get("service.name") or "")
        proj = project or service
        if not proj or proj.startswith("unknown_service"):
            proj = "default"
        for ss in rs.get(f"scope{key}") or []:
            scope = (ss.get("scope") or {}).get("name")
            yield proj, service, {**resource, **({"otel.scope": scope} if scope else {})}, ss


def to_runs(request, project=None):
    runs = []
    for proj, service, runtime, ss in _resources(request, "Spans", project):
        for span in ss.get("spans") or []:
            runs += _run(span, proj, runtime, service)
    return runs


def log_messages(request):
    """GenAI log events (the official OTel GenAI instrumentations put prompt/response content here)
    -> [{id, root, span, side, messages}] for Store.attach_messages. Other logs are ignored."""
    out = []
    for _, service, _, sl in _resources(request, "Logs", None):
        for rec in sorted(sl.get("logRecords") or [], key=lambda r: int(r.get("timeUnixNano") or 0)):
            a = _attrs(rec.get("attributes"))
            body = _json(_value(rec.get("body") or {}))  # a message as a JSON string (Semantic Kernel)
            name = rec.get("eventName") or a.get("event.name")
            found = _event_messages(name, {**a, **body} if isinstance(body, dict) else body if body else a)
            trace_hex, span_hex = _hex(rec.get("traceId")), _hex(rec.get("spanId"))
            if not trace_hex or not span_hex:
                continue
            trace = str(uuid.uuid5(uuid.UUID(hex=trace_hex), service))
            out += [{"id": _child_id(trace, span_hex), "root": trace, "span": span_hex, "side": side, "messages": msgs}
                    for side, msgs in found.items()]
    return out


# ---------------------------------------------------------------- span -> run

def _run(span, project, runtime, service):
    """One span -> [run], plus a stand-in root when the span's parent is in another process."""
    trace_hex, span_hex = _hex(span["traceId"]), _hex(span["spanId"])
    parent_hex = _hex(span.get("parentSpanId"))
    remote = bool(parent_hex) and int(span.get("flags") or 0) & REMOTE_PARENT == REMOTE_PARENT
    trace = str(uuid.uuid5(uuid.UUID(hex=trace_hex), service))
    # The root's run id is the trace id (the store's invariant). Its children point at the root
    # span's own id, which no row has; Store.trace() hangs such orphans under the root.
    run_id = trace if not parent_hex else _child_id(trace, span_hex)
    parent = None if not parent_hex else trace if remote else _child_id(trace, parent_hex)
    a = _attrs(span.get("attributes"))
    schema = _json(a.get("logfire.json_schema"))  # Logfire says which attributes hold JSON
    for k, v in (schema.get("properties") or {}).items() if isinstance(schema, dict) else ():
        if k in a and isinstance(v, dict) and v.get("type") in ("object", "array"):
            a[k] = _json(a[k])

    run = {
        "id": run_id, "trace_id": trace, "parent_run_id": parent,
        "name": span.get("name") or "span", "run_type": _run_type(a), "project": project,
        "start_time": _secs(span.get("startTimeUnixNano")), "end_time": _secs(span.get("endTimeUnixNano")),
    }
    if runtime.get("otel.scope") == "openinference.instrumentation.langchain" and run["run_type"] == "agent":
        run["run_type"] = "chain"  # OpenInference calls any LangChain run with "agent" in its name an agent
    if runtime.get("otel.scope") == "gcp.vertex.agent":  # Google ADK
        if run["run_type"] == "llm" and not _json(a.get("gcp.vertex.agent.llm_request")):
            run["run_type"] = "chain"  # ADK 2 repeats call_llm's usage on a generate_content child: count it once
        elif run["name"].startswith("agent_run"):
            run["run_type"] = "agent"  # ADK 1.x: "agent_run [name]", no attributes
    op = str(a.get("gen_ai.operation.name") or "")
    if op and run["name"].startswith(op + " ") and len(run["name"]) > len(op) + 1:
        run["name"] = run["name"][len(op) + 1:]  # "execute_tool get_stock" -> "get_stock": the type tag says the rest
    if op in ("invoke_agent", "create_agent") and a.get("gen_ai.agent.name"):
        run["name"] = str(a["gen_ai.agent.name"])  # the AI SDK names agent spans after the model
    if run["run_type"] == "tool" and a.get("ai.toolCall.name"):
        run["name"] = a.pop("ai.toolCall.name")  # Vercel names every tool span "ai.toolCall"
    run["inputs"], run["outputs"] = _io(a, run["run_type"] == "llm")

    model = _first(a, "llm.model_name", "gen_ai.response.model", "gen_ai.request.model", "ai.model.id",
                   "langfuse.observation.model.name", "embedding.model_name")
    params = _json(_first(a, "llm.invocation_parameters", "langfuse.observation.model.parameters"))
    params = {**_prefixed(a, "ai.settings."), **(params if isinstance(params, dict) else {})}
    if model:
        params.setdefault("model", model)

    usage = _json(_first(a, "langfuse.observation.usage_details", "mlflow.chat.tokenUsage"))
    if not isinstance(usage, dict) and run["run_type"] == "llm" and isinstance(run["outputs"], dict):
        usage = run["outputs"].get("usage")  # an OpenAI-shaped response as the output (MLflow)
    usage = usage if isinstance(usage, dict) else {}
    i = _first(a, "llm.token_count.prompt", "gen_ai.usage.input_tokens", "gen_ai.usage.prompt_tokens",
               "ai.usage.inputTokens", "ai.usage.promptTokens") or _pick(
        usage, "input", "prompt_tokens", "input_tokens")
    o = _first(a, "llm.token_count.completion", "gen_ai.usage.output_tokens", "gen_ai.usage.completion_tokens",
               "ai.usage.outputTokens", "ai.usage.completionTokens") or _pick(
        usage, "output", "completion_tokens", "output_tokens")
    t = _first(a, "llm.token_count.total", "llm.usage.total_tokens", "gen_ai.usage.total_tokens",
               "ai.usage.totalTokens") or _pick(usage, "total", "total_tokens")
    # seconds from the request to the first streamed chunk (OTel GenAI spec name first)
    ttft = _first(a, "gen_ai.response.time_to_first_chunk", "gen_ai.client.operation.time_to_first_chunk",
                  "gen_ai.response.time_to_first_token", "gen_ai.server.time_to_first_token")
    if isinstance(ttft, (int, float)) and ttft > 0:
        run["first_token_time"] = float(ttft)
    if i is not None or o is not None:
        run["input_tokens"], run["output_tokens"] = i, o
        run["total_tokens"] = t if t is not None else (i or 0) + (o or 0)

    meta = _json(a.pop("metadata", None))
    meta = meta if isinstance(meta, dict) else {}
    for prefix in META_PREFIXES:
        meta = {**_prefixed(a, prefix), **meta}
    session = next((a.pop(k) for k in SESSION_KEYS if a.get(k) not in (None, "")), None)
    if session is not None and not any(meta.get(k) not in (None, "") for k in THREAD_KEYS):
        meta["session_id"] = session
    tags = _json(_first(a, "tag.tags", "langfuse.trace.tags", "langsmith.span.tags"))
    tags = [t for t in tags.split(",") if t] if isinstance(tags, str) else tags  # LangSmith: comma-separated
    run["tags"] = [str(x) for x in tags] if isinstance(tags, list) else None

    events = span.get("events") or []
    found = {"inputs": [], "outputs": []}
    for e in events:  # GenAI content as span events (older instrumentations)
        for side, msgs in _event_messages(e.get("name"), _attrs(e.get("attributes"))).items():
            found[side] += msgs
    for side, msgs in found.items():
        if msgs and run[side] is None:
            run[side] = {"messages": msgs}
    # The LangSmith SDK marks a failed root span OK but still records its exception event.
    run["error"] = _error(span.get("status") or {}, events, a, runtime.get("otel.scope") == "langsmith")
    run["events"] = [{"name": e.get("name"), "time": _secs(e.get("timeUnixNano")),
                      "kwargs": _attrs(e.get("attributes"))} for e in events] or None
    if run_id == trace:
        meta["otel.trace_id"], meta["otel.span_id"] = trace_hex, span_hex
        run["absorb"] = _child_id(trace, span_hex)  # GenAI log events that arrived first (Store.attach_messages)
    if remote:
        meta["otel.remote_parent_span_id"] = parent_hex
    # Leftover attributes are still useful (temperature, user.id, ...); explicit metadata wins.
    run["extra"] = {"metadata": {**a, **meta}, "runtime": runtime, **({"invocation_params": params} if params else {})}
    if not remote:
        return [run]
    # Several spans of one service can enter the same trace from outside (a server called twice),
    # so the root they share only fills in what is missing and widens to cover them all (see Store).
    stand_in = {k: run[k] for k in ("project", "start_time", "end_time", "inputs", "outputs", "error")}
    stand_in.update(id=trace, trace_id=trace, name=run["name"], stand_in=True,
                    run_type="agent" if run["run_type"] == "agent" else "chain",
                    extra={"metadata": {"otel.trace_id": trace_hex}, "runtime": runtime})
    return [run, stand_in]


def _run_type(a):
    kind = str(a.pop("openinference.span.kind", None) or a.get("traceloop.span.kind")
               or a.pop("langfuse.observation.type", None) or a.pop("langsmith.span.kind", None)
               or _json(a.pop("mlflow.spanType", None)) or "").lower()
    if kind in KINDS:
        return KINDS[kind]
    op = a.get("gen_ai.operation.name")
    if op in GENAI_OPS:
        return GENAI_OPS[op]
    if _json(a.get("gcp.vertex.agent.llm_request")):  # Google ADK's call_llm
        return "llm"
    if "pydantic_ai.all_messages" in a or "all_messages_events" in a:  # Pydantic AI's agent run
        return "agent"
    if a.get("gen_ai.tool.name") and not op:  # Pydantic AI < 1.0 "running tool"
        return "tool"
    if a.get("llm.request.type") in ("chat", "completion"):
        return "llm"
    vercel = str(a.get("ai.operationId") or "")
    if vercel.endswith((".doGenerate", ".doStream")):
        return "llm"
    if vercel.endswith(".doEmbed"):
        return "embedding"
    if vercel == "ai.toolCall":
        return "tool"
    return "chain"


def _io(a, llm):
    """(inputs, outputs) from whichever convention the span uses; consumed keys are removed from a.
    Only an LLM span prefers message attributes: on a chain span OpenInference sets them from a
    partial view of its inputs, while input.value holds all of them."""
    oi_in = [_oi_msg(m) for m in _indexed(a, "llm.input_messages")]
    oi_out = [_oi_msg(m) for m in _indexed(a, "llm.output_messages")]
    old_in = [{"role": m.get("role"), "content": m.get("content")} for m in _indexed(a, "gen_ai.prompt")]
    old_out = [{"role": m.get("role") or "assistant", "content": m.get("content")}
               for m in _indexed(a, "gen_ai.completion")]
    system = [{"role": "system", "content": _parts_text(_json(a.pop("gen_ai.system_instructions", None)))}]
    new_in = [_genai_msg(m) for m in _list(_json(a.pop("gen_ai.input.messages", None)))]
    new_out = [_genai_msg(m) for m in _list(_json(a.pop("gen_ai.output.messages", None)))]
    if new_in and system[0]["content"] and not any(m["role"] == "system" for m in new_in):
        new_in = system + new_in
    # Vercel AI SDK: ai.prompt.messages on model calls, ai.prompt {system, prompt|messages} on the outer span
    prompt = _json(a.pop("ai.prompt", None))
    ai_in = _list(_json(a.pop("ai.prompt.messages", None)))
    if not ai_in and isinstance(prompt, dict):
        ai_in = ([{"role": "system", "content": prompt["system"]}] if prompt.get("system") else []) + (
            _list(prompt.get("messages")) or [{"role": "user", "content": prompt.get("prompt")}])
    ai_in = [_genai_msg(m) for m in ai_in]
    text, calls = a.pop("ai.response.text", None), _list(_json(a.pop("ai.response.toolCalls", None)))
    ai_out = [_genai_msg({"role": "assistant", "content": ([{"type": "text", "text": text}] if text else []) + [
        {"type": "tool-call", **c} for c in calls if isinstance(c, dict)]})] if text is not None or calls else []

    # Pydantic AI: the agent run's whole conversation (cut after the last user turn, the answer is
    # final_result); before 1.0 as GenAI events, on model calls too ("events").
    convo = [_genai_msg(m) for m in _list(_json(a.pop("pydantic_ai.all_messages", None)))]
    pyd = {"inputs": [], "outputs": []}
    for key in ("all_messages_events", "events"):
        events = _list(_json(a.get(key)))
        if events and all(isinstance(e, dict) and str(e.get("event.name")).startswith("gen_ai.") for e in events):
            del a[key]
            for e in events:
                for side, msgs in _event_messages(e["event.name"], e).items():
                    pyd[side] += msgs
    convo = convo or pyd["inputs"]
    last_user = max((i for i, m in enumerate(convo) if m["role"] == "user" and "tool_call_id" not in m),
                    default=len(convo) - 1)
    pyd_in = convo[:last_user + 1]
    # OpenInference retrievers and embeddings (vectors are dropped: big, and unreadable in a trace)
    docs = [{"page_content": d.get("document.content"), "metadata": {
        **(_json(d.get("document.metadata")) if isinstance(_json(d.get("document.metadata")), dict) else {}),
        **{k: d[f"document.{k}"] for k in ("id", "score") if d.get(f"document.{k}") is not None}}}
        for d in _indexed(a, "retrieval.documents")]
    texts = [e["embedding.text"] for e in _indexed(a, "embedding.embeddings") if e.get("embedding.text") is not None]
    adk_in, adk_out = _gemini(_json(a.pop("gcp.vertex.agent.llm_request", None))), _gemini(
        _json(a.pop("gcp.vertex.agent.llm_response", None)))

    raw_in = _first(a, "input.value", "traceloop.entity.input", "gen_ai.tool.call.arguments", "ai.toolCall.args",
                    "langfuse.observation.input", "langfuse.trace.input", "mlflow.spanInputs",
                    "gcp.vertex.agent.tool_call_args", "tool_arguments", "gen_ai.prompt")
    raw_out = _first(a, "output.value", "traceloop.entity.output", "gen_ai.tool.call.result", "ai.toolCall.result",
                     "ai.response.object", "langfuse.observation.output", "langfuse.trace.output", "mlflow.spanOutputs",
                     "gcp.vertex.agent.tool_response", "tool_response", "final_result", "gen_ai.completion")
    a.pop("input.mime_type", None)
    a.pop("output.mime_type", None)

    msgs_in = oi_in or new_in or ai_in or pyd_in or adk_in or old_in
    msgs_out = oi_out or new_out or ai_out or pyd["outputs"] or adk_out or old_out
    inputs = {"messages": msgs_in} if msgs_in and (llm or raw_in is None) else _wrap(raw_in, "input", llm)
    outputs = {"messages": msgs_out} if msgs_out and (llm or raw_out is None) else _wrap(raw_out, "output", llm)
    if docs:  # beats output.value, which LlamaIndex fills with object reprs
        outputs = {"documents": docs}
    if texts and inputs is None:
        inputs = {"input": texts}
    return inputs, outputs


def _oi_msg(d):
    m = {"role": d.get("message.role"), "content": d.get("message.content")}
    if m["content"] is None:
        m["content"] = "".join(p.get("message_content.text") or "" for p in _indexed(d, "message.contents"))
    calls = _indexed(d, "message.tool_calls")
    if calls:
        m["tool_calls"] = [{"name": c.get("tool_call.function.name"), "id": c.get("tool_call.id"),
                            "args": _json(c.get("tool_call.function.arguments"))} for c in calls]
    for src, dst in (("message.tool_call_id", "tool_call_id"), ("message.name", "name")):
        if d.get(src):
            m[dst] = d[src]
    return m


def _genai_msg(m):
    """A GenAI-convention message ({role, parts}) or a Vercel AI SDK one ({role, content: str | parts})."""
    if not isinstance(m, dict):
        return {"role": "user", "content": str(m)}
    out, text, calls = {"role": m.get("role") or "user"}, [], []
    parts = m.get("parts") if "parts" in m else m.get("content")
    for p in [{"type": "text", "content": parts}] if isinstance(parts, str) else _list(parts):
        kind = p.get("type") if isinstance(p, dict) else None
        if kind in ("tool_call", "tool-call"):
            calls.append({"name": _pick(p, "name", "toolName"), "id": _pick(p, "id", "toolCallId"),
                          "args": _json(_pick(p, "arguments", "input", "args"))})
        elif kind in ("tool_call_response", "tool-result") and out["role"] == "assistant":
            continue  # the AI SDK lists tool results in its agent's answer too; they belong to the tool run
        elif kind in ("tool_call_response", "tool-result"):
            out["tool_call_id"] = _pick(p, "id", "toolCallId")
            r = _pick(p, "response", "output", "result")
            r = r.get("value", r) if isinstance(r, dict) and set(r) <= {"type", "value"} else r  # Vercel {type, value}
            text.append(r if isinstance(r, str) else json.dumps(r, ensure_ascii=False, default=str))
        elif kind in ("text", "reasoning") and isinstance(p.get("text"), str):
            text.append(p["text"])
        else:
            text.append(_parts_text([p]))
    out["content"] = "\n".join(t for t in text if t)
    if calls:
        out["tool_calls"] = calls
    return out


EVENT_ROLES = {"gen_ai.system.message": "system", "gen_ai.user.message": "user",
               "gen_ai.assistant.message": "assistant", "gen_ai.tool.message": "tool"}


def _event_messages(name, body):
    """A GenAI event (log record or span event) -> {"inputs" | "outputs": [message]}, empty if it isn't one.
    One event per message (gen_ai.user.message, ..., gen_ai.choice) with the message as its body;
    gen_ai.client.inference.operation.details with gen_ai.input/output.messages; or the oldest
    gen_ai.content.prompt / .completion with a gen_ai.prompt / .completion attribute."""
    body = body if isinstance(body, dict) else {"content": body}
    if name in ("gen_ai.content.prompt", "gen_ai.content.completion"):
        side, key, role = ("inputs", "gen_ai.prompt", "user") if name.endswith("prompt") else (
            "outputs", "gen_ai.completion", "assistant")
        v = _json(body.get(key))
        msgs = v if isinstance(v, list) else [{"role": role, "content": v}] if v is not None else []
        return {side: [_event_msg(m if isinstance(m, dict) else {"role": role, "content": m}, role) for m in msgs]}
    if name == "gen_ai.client.inference.operation.details":
        found = {side: [_genai_msg(m) for m in _list(_json(body.get(f"gen_ai.{key}.messages")))]
                 for side, key in (("inputs", "input"), ("outputs", "output"))}
        return {k: v for k, v in found.items() if v}
    if name == "gen_ai.choice":
        m = _json(body.get("message"))
        return {"outputs": [_event_msg(m if isinstance(m, dict) else {"content": m}, "assistant")]}
    if name in EVENT_ROLES:
        return {"inputs": [_event_msg(body, EVENT_ROLES[name])]}
    return {}


def _gemini(v):
    """Google ADK's llm_request ({config.system_instruction, contents}) or llm_response ({content})
    -> messages. Gemini parts: text, function_call, function_response."""
    if not isinstance(v, dict):
        return []
    system = (v.get("config") or {}).get("system_instruction")
    out = [{"role": "system", "content": system}] if isinstance(system, str) and system else []
    for c in _list(v.get("contents")) or ([v["content"]] if isinstance(v.get("content"), dict) else []):
        m = {"role": {"model": "assistant"}.get(c.get("role"), c.get("role") or "user"), "content": ""}
        for p in _list(c.get("parts")):
            if p.get("text"):
                m["content"] += p["text"]
            elif p.get("function_call"):
                f = p["function_call"]
                m.setdefault("tool_calls", []).append({"name": f.get("name"), "id": f.get("id"), "args": f.get("args")})
            elif p.get("function_response"):
                f = p["function_response"]
                m.update(role="tool", name=f.get("name"), tool_call_id=f.get("id"),
                         content=json.dumps(f.get("response"), ensure_ascii=False, default=str))
        out.append(m)
    return out


def _event_msg(d, role):
    """{role?, content, tool_calls: [{id, function: {name, arguments}}], id} -> message dict."""
    content = _json(d.get("content"))
    m = {"role": d.get("role") or role,
         "content": content if isinstance(content, str) or content is None else _parts_text(content)
         if isinstance(content, list) else json.dumps(content, ensure_ascii=False, default=str)}
    calls = [c for c in _list(_json(d.get("tool_calls"))) if isinstance(c, dict)]
    if calls:
        m["tool_calls"] = [{"name": (c.get("function") or {}).get("name"), "id": c.get("id"),
                            "args": _json((c.get("function") or {}).get("arguments"))} for c in calls]
    if m["role"] == "tool" and (d.get("tool_call_id") or d.get("id")):
        m["tool_call_id"] = d.get("tool_call_id") or d["id"]
    return m


def _parts_text(parts):
    if isinstance(parts, str):
        return parts
    return "\n".join(p.get("content") or p.get("text") or "" if isinstance(p, dict) and p.get("type") == "text"
                     else json.dumps(p, ensure_ascii=False, default=str) for p in _list(parts))


def _error(status, events, a, exception_fails=False):
    level, message = a.pop("langfuse.observation.level", None), a.pop("langfuse.observation.status_message", None)
    if level == "ERROR":
        return message or "error"
    raised = exception_fails and any(e.get("name") == "exception" for e in events)
    if status.get("code") != STATUS_ERROR and not raised:
        return None
    for e in events:
        if e.get("name") == "exception":
            x = _attrs(e.get("attributes"))
            if x.get("exception.stacktrace"):
                return str(x["exception.stacktrace"])
            if x.get("exception.type") or x.get("exception.message"):
                return f"{x.get('exception.type', 'Error')}: {x.get('exception.message', '')}"
    return status.get("message") or "error"


# ---------------------------------------------------------------- small helpers

def _hex(v):
    """OTLP/JSON ids are hex by the spec; protobuf-to-JSON (and some exporters) give base64.
    Hex ids are 16 or 32 characters, base64 ones 12 or 24, so the length tells them apart."""
    if not v:
        return None
    v = str(v)
    h = v.lower() if len(v) in (16, 32) and all(c in string.hexdigits for c in v) else base64.b64decode(v).hex()
    return None if not h.strip("0") else h  # all-zero parent id means "no parent"


def _child_id(trace, span_hex):
    return str(uuid.uuid5(uuid.UUID(trace), span_hex))


def _secs(nanos):
    return int(nanos) / 1e9 if nanos not in (None, "", "0", 0) else None


def _value(v):
    for k in ("stringValue", "boolValue", "doubleValue", "bytesValue"):
        if k in v:
            return v[k]
    if "intValue" in v:
        return int(v["intValue"])
    if "arrayValue" in v:
        return [_value(x) for x in v["arrayValue"].get("values") or []]
    if "kvlistValue" in v:
        return _attrs(v["kvlistValue"].get("values"))
    return None


def _attrs(kvs):
    return {kv["key"]: _value(kv.get("value") or {}) for kv in kvs or []}


def _indexed(a, prefix):
    """Pop flattened `prefix.N.rest` keys into a list of {rest: value} dicts, in index order."""
    out = {}
    for k in [k for k in a if k.startswith(prefix + ".")]:
        idx, _, rest = k[len(prefix) + 1:].partition(".")
        if idx.isdigit():
            out.setdefault(int(idx), {})[rest] = a.pop(k)
    return [out[i] for i in sorted(out)]


def _first(a, *keys):
    vals = [a.pop(k, None) for k in keys]
    return next((v for v in vals if v is not None), None)


def _pick(d, *keys):
    return next((d[k] for k in keys if d.get(k) is not None), None)


def _prefixed(a, prefix):
    """Pop `prefix.rest` keys into {rest: value}."""
    return {k[len(prefix):]: a.pop(k) for k in [k for k in a if k.startswith(prefix)]}


def _json(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _list(v):
    return v if isinstance(v, list) else []


def _lc_messages(v):
    """LangChain's message_to_dict shape {"type": "ai", "data": {...}} (OpenInference serializes
    messages this way) -> {"type": "ai", "content": ...}, which previews and the chat view read."""
    if isinstance(v, list):
        return [_lc_messages(x) for x in v]
    if isinstance(v, dict):
        if set(v) == {"type", "data"} and isinstance(v["data"], dict) and "content" in v["data"]:
            return {**v["data"], "type": v["type"]}
        return {k: _lc_messages(x) for k, x in v.items()}
    return v


def _wrap(v, key, llm=False):
    v = _lc_messages(_json(v))
    if isinstance(v, dict) and set(v) == {"args", "kwargs"} and not v["kwargs"] and len(_list(v["args"])) == 1:
        v = v["args"][0]  # OpenLLMetry's @task / @workflow input with one positional argument
    if llm and isinstance(v, dict) and "role" in v:  # one chat message (Langfuse generation output)
        v = [v]
    if llm and isinstance(v, list) and v and all(isinstance(m, dict) and "role" in m for m in v):
        return {"messages": v}
    if v is None or isinstance(v, dict):
        return v
    return {key: v}
