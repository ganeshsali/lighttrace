"""A fake model server for the framework tests: no real model, no key. It answers the OpenAI chat
completions (plain and streamed) and Responses, Anthropic messages, Gemini generateContent and
Bedrock Converse APIs the same way: a call to the first tool when tools are offered and no tool result came back yet,
else "It is sunny in Pune.", always with 11 input and 5 output tokens."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ANSWER = "It is sunny in Pune."
ARGS = {"city": "Pune"}
IN, OUT = 11, 5


def openai(req):
    """A first turn calls the first tool; after that (any assistant turn in the history) it answers,
    through a final_answer tool when one is offered (smolagents)."""
    msgs, names = req.get("messages") or [], [t["function"]["name"] for t in req.get("tools") or []]
    called = any(m.get("role") in ("assistant", "tool") for m in msgs)
    name, args = (names[0], ARGS) if names and not called else ("final_answer", {"answer": ANSWER}) \
        if "final_answer" in names else (None, None)
    if name:
        msg = {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1", "type": "function", "function": {
            "name": name, "arguments": json.dumps(args)}}]}
    else:
        msg = {"role": "assistant", "content": ANSWER}
    return msg, {"prompt_tokens": IN, "completion_tokens": OUT, "total_tokens": IN + OUT}


def openai_stream(req):
    """The same reply as server-sent chunks: the delta, the finish reason, then usage."""
    msg, usage = openai(req)
    base = {"id": "c1", "object": "chat.completion.chunk", "created": 1, "model": req["model"]}
    delta = {"role": "assistant", "content": msg["content"]}
    if msg.get("tool_calls"):
        delta["tool_calls"] = [{"index": 0, **msg["tool_calls"][0]}]
    finish = "tool_calls" if msg.get("tool_calls") else "stop"
    chunks = [{**base, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
              {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
              {**base, "choices": [], "usage": usage}]
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"


def responses(req):
    """OpenAI's Responses API (non-streamed)."""
    items, tools = req.get("input") or [], [t for t in req.get("tools") or [] if t.get("type") == "function"]
    answered = isinstance(items, list) and any(isinstance(i, dict) and i.get("type") == "function_call_output"
                                               for i in items)
    if tools and not answered:
        out = {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": tools[0]["name"],
               "arguments": json.dumps(ARGS), "status": "completed"}
    else:
        out = {"type": "message", "id": "msg_1", "role": "assistant", "status": "completed",
               "content": [{"type": "output_text", "text": ANSWER, "annotations": []}]}
    return {"id": "resp_1", "object": "response", "created_at": 1, "status": "completed", "model": req["model"],
            "output": [out], "parallel_tool_calls": True, "tool_choice": "auto", "tools": req.get("tools") or [],
            "usage": {"input_tokens": IN, "output_tokens": OUT, "total_tokens": IN + OUT,
                      "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}}}


def anthropic(req):
    msgs, tools = req.get("messages") or [], req.get("tools")
    last = msgs[-1].get("content") if msgs else None
    answered = isinstance(last, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in last)
    if tools and not answered:
        content, stop = [{"type": "tool_use", "id": "toolu_1", "name": tools[0]["name"], "input": ARGS}], "tool_use"
    else:
        content, stop = [{"type": "text", "text": ANSWER}], "end_turn"
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": req["model"], "content": content,
            "stop_reason": stop, "stop_sequence": None, "usage": {"input_tokens": IN, "output_tokens": OUT}}


def gemini(req):
    tools = [d for t in req.get("tools") or [] for d in t.get("functionDeclarations") or []]
    last = (req.get("contents") or [{}])[-1].get("parts") or []
    if tools and not any("functionResponse" in p for p in last):
        part = {"functionCall": {"name": tools[0]["name"], "args": ARGS}}
    else:
        part = {"text": ANSWER}
    return {"candidates": [{"content": {"role": "model", "parts": [part]}, "finishReason": "STOP", "index": 0}],
            "usageMetadata": {"promptTokenCount": IN, "candidatesTokenCount": OUT, "totalTokenCount": IN + OUT},
            "modelVersion": "stub-model"}


def bedrock(req):
    tools = (req.get("toolConfig") or {}).get("tools") or []
    last = (req.get("messages") or [{}])[-1].get("content") or []
    if tools and not any("toolResult" in b for b in last):
        use = {"toolUseId": "tool_1", "name": tools[0]["toolSpec"]["name"], "input": ARGS}
        content, stop = [{"toolUse": use}], "tool_use"
    else:
        content, stop = [{"text": ANSWER}], "end_turn"
    return {"output": {"message": {"role": "assistant", "content": content}}, "stopReason": stop,
            "usage": {"inputTokens": IN, "outputTokens": OUT, "totalTokens": IN + OUT}, "metrics": {"latencyMs": 1}}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        path, ctype = self.path.split("?")[0], "application/json"
        if path.endswith("/chat/completions") and req.get("stream"):
            body, ctype = openai_stream(req), "text/event-stream"
        elif path.endswith("/chat/completions"):
            msg, usage = openai(req)
            body = {"id": "c1", "object": "chat.completion", "created": 1, "model": req["model"], "usage": usage,
                    "choices": [{"index": 0, "message": msg,
                                 "finish_reason": "tool_calls" if msg.get("tool_calls") else "stop"}]}
        elif path.endswith("/responses"):
            body = responses(req)
        elif path.endswith("/messages"):
            body = anthropic(req)
        elif path.endswith(":generateContent"):
            body = gemini(req)
        elif path.endswith("/converse"):
            body = bedrock(req)
        else:
            self.send_error(404)
            return
        data = (body if isinstance(body, str) else json.dumps(body)).encode()
        self.send_response(200)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def start():
    """Serve on a free port in a daemon thread; returns (server, "http://127.0.0.1:<port>")."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"
