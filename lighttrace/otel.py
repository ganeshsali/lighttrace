"""OTLP/HTTP trace ingest: OpenTelemetry spans -> lighttrace runs.

Spans become the same run dicts the LangSmith SDK sends, so storage and UI are shared.
Understands the OpenInference (Arize Phoenix) and OTel GenAI semantic conventions,
OpenLLMetry's entity input/output, the Vercel AI SDK's `ai.*` attributes and the Langfuse
SDK's `langfuse.*` ones; every attribute it does not map lands in metadata.

Traces: one lighttrace trace per (OTel trace id, service.name), so several services that share
an OTel trace id (A2A, out-of-process MCP servers) never collide; each shows as its own trace,
findable together with the `otel.trace_id` metadata filter. A span whose parent is remote (in
another process) hangs under a stand-in root for its service's part of the trace.
"""

import base64
import json
import uuid

from .store import THREAD_KEYS

KINDS = {"llm": "llm", "tool": "tool", "retriever": "retriever", "reranker": "retriever",
         "embedding": "embedding", "prompt": "prompt", "agent": "agent", "generation": "llm"}
GENAI_OPS = {"chat": "llm", "text_completion": "llm", "generate_content": "llm", "embeddings": "embedding",
             "execute_tool": "tool", "invoke_agent": "agent", "create_agent": "agent"}
SESSION_KEYS = ("session.id", "gen_ai.conversation.id", "langfuse.session.id")
META_PREFIXES = ("ai.telemetry.metadata.", "langfuse.observation.metadata.", "langfuse.trace.metadata.")
STATUS_ERROR = 2
REMOTE_PARENT = 0x300  # span flags: HAS_IS_REMOTE | IS_REMOTE


def parse(body, content_type):
    """OTLP request body -> (ExportTraceServiceRequest as a JSON-style dict, ids_are_base64)."""
    if "protobuf" in content_type:
        from google.protobuf.json_format import MessageToDict
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

        msg = ExportTraceServiceRequest()
        msg.ParseFromString(body)
        return MessageToDict(msg, use_integers_for_enums=True), True
    return json.loads(body), False  # OTLP/JSON: ids are hex


def to_runs(request, b64, project=None):
    runs = []
    for rs in request.get("resourceSpans") or []:
        resource = _attrs((rs.get("resource") or {}).get("attributes"))
        service = str(resource.get("service.name") or "")
        proj = project or service
        if not proj or proj.startswith("unknown_service"):
            proj = "default"
        for ss in rs.get("scopeSpans") or []:
            scope = (ss.get("scope") or {}).get("name")
            for span in ss.get("spans") or []:
                runs += _run(span, b64, proj, {**resource, **({"otel.scope": scope} if scope else {})}, service)
    return runs


# ---------------------------------------------------------------- span -> run

def _run(span, b64, project, runtime, service):
    """One span -> [run], plus a stand-in root when the span's parent is in another process."""
    trace_hex = _hex(span["traceId"], b64)
    parent_hex = _hex(span.get("parentSpanId"), b64)
    remote = bool(parent_hex) and int(span.get("flags") or 0) & REMOTE_PARENT == REMOTE_PARENT
    trace = str(uuid.uuid5(uuid.UUID(hex=trace_hex), service))
    # The root's run id is the trace id (the store's invariant). Its children point at the root
    # span's own id, which no row has; Store.trace() hangs such orphans under the root.
    run_id = trace if not parent_hex else _child_id(trace, _hex(span["spanId"], b64))
    parent = None if not parent_hex else trace if remote else _child_id(trace, parent_hex)
    a = _attrs(span.get("attributes"))

    run = {
        "id": run_id, "trace_id": trace, "parent_run_id": parent,
        "name": span.get("name") or "span", "run_type": _run_type(a), "session_name": project,
        "start_time": _secs(span.get("startTimeUnixNano")), "end_time": _secs(span.get("endTimeUnixNano")),
    }
    if run["run_type"] == "tool" and a.get("ai.toolCall.name"):
        run["name"] = a.pop("ai.toolCall.name")  # Vercel names every tool span "ai.toolCall"
    run["inputs"], run["outputs"] = _io(a, run["run_type"] == "llm")

    model = _first(a, "llm.model_name", "gen_ai.response.model", "gen_ai.request.model", "ai.model.id",
                   "langfuse.observation.model.name")
    params = _json(_first(a, "llm.invocation_parameters", "langfuse.observation.model.parameters"))
    params = {**_prefixed(a, "ai.settings."), **(params if isinstance(params, dict) else {})}
    if model:
        params.setdefault("model", model)

    usage = _json(a.pop("langfuse.observation.usage_details", None))
    usage = usage if isinstance(usage, dict) else {}
    i = _first(a, "llm.token_count.prompt", "gen_ai.usage.input_tokens", "gen_ai.usage.prompt_tokens",
               "ai.usage.inputTokens", "ai.usage.promptTokens") or _pick(usage, "input", "prompt_tokens", "input_tokens")
    o = _first(a, "llm.token_count.completion", "gen_ai.usage.output_tokens", "gen_ai.usage.completion_tokens",
               "ai.usage.outputTokens", "ai.usage.completionTokens") or _pick(usage, "output", "completion_tokens",
                                                                             "output_tokens")
    t = _first(a, "llm.token_count.total", "llm.usage.total_tokens", "ai.usage.totalTokens") or _pick(
        usage, "total", "total_tokens")
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
    tags = _json(_first(a, "tag.tags", "langfuse.trace.tags"))
    run["tags"] = [str(x) for x in tags] if isinstance(tags, list) else None

    events = span.get("events") or []
    run["error"] = _error(span.get("status") or {}, events, a)
    run["events"] = [{"name": e.get("name"), "time": _secs(e.get("timeUnixNano")), "kwargs": _attrs(e.get("attributes"))}
                     for e in events] or None
    if run_id == trace:
        meta["otel.trace_id"] = trace_hex
    if remote:
        meta["otel.remote_parent_span_id"] = parent_hex
    # Leftover attributes are still useful (temperature, user.id, ...); explicit metadata wins.
    run["extra"] = {"metadata": {**a, **meta}, "runtime": runtime, **({"invocation_params": params} if params else {})}
    if not remote:
        return [run]
    # Several spans of one service can enter the same trace from outside (a server called twice),
    # so the root they share only fills in what is missing and widens to cover them all (see Store).
    stand_in = {k: run[k] for k in ("session_name", "start_time", "end_time", "inputs", "outputs", "error")}
    stand_in.update(id=trace, trace_id=trace, name=run["name"], stand_in=True,
                    run_type="agent" if run["run_type"] == "agent" else "chain",
                    extra={"metadata": {"otel.trace_id": trace_hex}, "runtime": runtime})
    return [run, stand_in]


def _run_type(a):
    kind = str(a.pop("openinference.span.kind", None) or a.get("traceloop.span.kind")
               or a.pop("langfuse.observation.type", None) or "").lower()
    if kind in KINDS:
        return KINDS[kind]
    op = a.get("gen_ai.operation.name")
    if op in GENAI_OPS:
        return GENAI_OPS[op]
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
    if new_in and system[0]["content"]:
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

    raw_in = _first(a, "input.value", "traceloop.entity.input", "gen_ai.tool.call.arguments", "ai.toolCall.args",
                    "langfuse.observation.input", "langfuse.trace.input")
    raw_out = _first(a, "output.value", "traceloop.entity.output", "gen_ai.tool.call.result", "ai.toolCall.result",
                     "ai.response.object", "langfuse.observation.output", "langfuse.trace.output")
    a.pop("input.mime_type", None)
    a.pop("output.mime_type", None)

    msgs_in = oi_in or new_in or ai_in or old_in
    msgs_out = oi_out or new_out or ai_out or old_out
    inputs = {"messages": msgs_in} if msgs_in and (llm or raw_in is None) else _wrap(raw_in, "input", llm)
    outputs = {"messages": msgs_out} if msgs_out and (llm or raw_out is None) else _wrap(raw_out, "output", llm)
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


def _parts_text(parts):
    if isinstance(parts, str):
        return parts
    return "\n".join(p.get("content") or "" if isinstance(p, dict) and p.get("type") == "text"
                     else json.dumps(p, ensure_ascii=False, default=str) for p in _list(parts))


def _error(status, events, a):
    level, message = a.pop("langfuse.observation.level", None), a.pop("langfuse.observation.status_message", None)
    if level == "ERROR":
        return message or "error"
    if status.get("code") != STATUS_ERROR:
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

def _hex(v, b64):
    if not v:
        return None
    h = base64.b64decode(v).hex() if b64 else str(v).lower()
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
    if llm and isinstance(v, dict) and "role" in v:  # one chat message (Langfuse generation output)
        v = [v]
    if llm and isinstance(v, list) and v and all(isinstance(m, dict) and "role" in m for m in v):
        return {"messages": v}
    if v is None or isinstance(v, dict):
        return v
    return {key: v}
