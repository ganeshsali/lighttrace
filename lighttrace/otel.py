"""OTLP/HTTP trace ingest: OpenTelemetry spans -> lighttrace runs.

Spans become the same run dicts the LangSmith SDK sends, so storage and UI are shared.
Understands the OpenInference (Arize Phoenix) and OTel GenAI semantic conventions and
OpenLLMetry's entity input/output; every attribute it does not map lands in metadata.
"""

import base64
import json
import uuid

from .store import THREAD_KEYS

KINDS = {"llm": "llm", "tool": "tool", "retriever": "retriever", "reranker": "retriever",
         "embedding": "embedding", "prompt": "prompt"}
GENAI_OPS = {"chat": "llm", "text_completion": "llm", "generate_content": "llm", "embeddings": "embedding",
             "execute_tool": "tool"}
SESSION_KEYS = ("session.id", "gen_ai.conversation.id", "langfuse.session.id")
STATUS_ERROR = 2


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
        proj = project or str(resource.get("service.name") or "")
        if not proj or proj.startswith("unknown_service"):
            proj = "default"
        for ss in rs.get("scopeSpans") or []:
            scope = (ss.get("scope") or {}).get("name")
            for span in ss.get("spans") or []:
                runs.append(_run(span, b64, proj, {**resource, **({"otel.scope": scope} if scope else {})}))
    return runs


# ---------------------------------------------------------------- span -> run

def _run(span, b64, project, runtime):
    trace_hex = _hex(span["traceId"], b64)
    parent_hex = _hex(span.get("parentSpanId"), b64)
    trace = uuid.UUID(hex=trace_hex)
    # The root's run id is the trace id (the store's invariant). Its children point at the root
    # span's own id, which no row has; Store.trace() hangs such orphans under the root.
    # ponytail: a span whose parent lives in another service counts as a child, so a trace that
    # starts elsewhere has no root here; honour the OTLP is_remote flag if that shows up.
    run_id = str(trace) if not parent_hex else _child_id(trace, _hex(span["spanId"], b64))
    a = _attrs(span.get("attributes"))

    run = {
        "id": run_id, "trace_id": str(trace), "parent_run_id": _child_id(trace, parent_hex) if parent_hex else None,
        "name": span.get("name") or "span", "run_type": _run_type(a), "session_name": project,
        "start_time": _secs(span.get("startTimeUnixNano")), "end_time": _secs(span.get("endTimeUnixNano")),
    }
    run["inputs"], run["outputs"] = _io(a, run["run_type"] == "llm")

    model = a.pop("llm.model_name", None) or a.pop("gen_ai.response.model", None) or a.pop("gen_ai.request.model", None)
    params = _json(a.pop("llm.invocation_parameters", None))
    params = params if isinstance(params, dict) else {}
    if model:
        params.setdefault("model", model)

    i = _first(a, "llm.token_count.prompt", "gen_ai.usage.input_tokens", "gen_ai.usage.prompt_tokens")
    o = _first(a, "llm.token_count.completion", "gen_ai.usage.output_tokens", "gen_ai.usage.completion_tokens")
    t = _first(a, "llm.token_count.total", "llm.usage.total_tokens")
    if i is not None or o is not None:
        run["input_tokens"], run["output_tokens"] = i, o
        run["total_tokens"] = t if t is not None else (i or 0) + (o or 0)

    meta = _json(a.pop("metadata", None))
    meta = meta if isinstance(meta, dict) else {}
    session = next((a.pop(k) for k in SESSION_KEYS if a.get(k) not in (None, "")), None)
    if session is not None and not any(meta.get(k) not in (None, "") for k in THREAD_KEYS):
        meta["session_id"] = session
    tags = a.pop("tag.tags", None)
    run["tags"] = [str(x) for x in tags] if isinstance(tags, list) else None

    events = span.get("events") or []
    run["error"] = _error(span.get("status") or {}, events)
    run["events"] = [{"name": e.get("name"), "time": _secs(e.get("timeUnixNano")), "kwargs": _attrs(e.get("attributes"))}
                     for e in events] or None
    # Leftover attributes are still useful (temperature, user.id, ...); explicit metadata wins.
    run["extra"] = {"metadata": {**a, **meta}, "runtime": runtime, **({"invocation_params": params} if params else {})}
    return run


def _run_type(a):
    kind = str(a.pop("openinference.span.kind", None) or a.get("traceloop.span.kind") or "").lower()
    if kind in KINDS:
        return KINDS[kind]
    op = a.get("gen_ai.operation.name")
    if op in GENAI_OPS:
        return GENAI_OPS[op]
    if a.get("llm.request.type") in ("chat", "completion"):
        return "llm"
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

    raw_in = _first(a, "input.value", "traceloop.entity.input", "gen_ai.tool.call.arguments")
    raw_out = _first(a, "output.value", "traceloop.entity.output", "gen_ai.tool.call.result")
    a.pop("input.mime_type", None)
    a.pop("output.mime_type", None)

    msgs_in = oi_in or new_in or old_in
    msgs_out = oi_out or new_out or old_out
    inputs = {"messages": msgs_in} if msgs_in and (llm or raw_in is None) else _wrap(raw_in, "input")
    outputs = {"messages": msgs_out} if msgs_out and (llm or raw_out is None) else _wrap(raw_out, "output")
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
    if not isinstance(m, dict):
        return {"role": "user", "content": str(m)}
    out, text, calls = {"role": m.get("role") or "user"}, [], []
    for p in _list(m.get("parts")):
        kind = p.get("type") if isinstance(p, dict) else None
        if kind == "tool_call":
            calls.append({"name": p.get("name"), "id": p.get("id"), "args": p.get("arguments")})
        elif kind == "tool_call_response":
            out["tool_call_id"] = p.get("id")
            r = p.get("response")
            text.append(r if isinstance(r, str) else json.dumps(r, ensure_ascii=False, default=str))
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


def _error(status, events):
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
    return str(uuid.uuid5(trace, span_hex))


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


def _wrap(v, key):
    v = _lc_messages(_json(v))
    if v is None or isinstance(v, dict):
        return v
    return {key: v}
