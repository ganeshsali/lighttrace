# Plain OpenTelemetry (your own spans, any language)

LightTrace's receiver is standard OpenTelemetry, so you can send your own spans, from any language,
without the LightTrace package. Use the [OpenTelemetry GenAI attribute names](https://opentelemetry.io/docs/specs/semconv/gen-ai/)
so LightTrace knows what each span is.

In Python, `lighttrace.register()` gives you the sender (and returns the provider):

```python
import json, lighttrace
tracer = lighttrace.register().get_tracer("my-app")

with tracer.start_as_current_span("chat my-model", attributes={
        "gen_ai.operation.name": "chat", "gen_ai.request.model": "my-model",
        "gen_ai.input.messages": json.dumps([{"role": "user", "parts": [{"type": "text", "content": q}]}])}) as span:
    answer = call_my_model(q)
    span.set_attributes({"gen_ai.usage.input_tokens": 12, "gen_ai.usage.output_tokens": 7,
        "gen_ai.output.messages": json.dumps([{"role": "assistant", "parts": [{"type": "text", "content": answer}]}])})
```

Elsewhere, point the language's OTLP exporter (HTTP, protobuf or JSON) at the receiver with the `OTEL_*`
settings from the [overview](README.md#without-the-lighttrace-package).

| What | Attribute |
|---|---|
| Run type | `gen_ai.operation.name`: `chat` → llm, `execute_tool` → tool, `invoke_agent` → agent, `embeddings` → embedding; anything else is a chain |
| Inputs / outputs | `gen_ai.input.messages` / `gen_ai.output.messages` (JSON), or `input.value` / `output.value` for any JSON or text |
| Tool calls | `gen_ai.tool.name`, `gen_ai.tool.call.arguments`, `gen_ai.tool.call.result` |
| Tokens, model, first token | `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.request.model`, `gen_ai.response.time_to_first_chunk` (seconds) |
| Threads | `gen_ai.conversation.id` or `session.id` (or `thread_id` inside a `metadata` JSON attribute) |
| Anything else | kept as run metadata, filterable with `key:value` |

Runnable: [`examples/manual_otel.py`](../examples/manual_otel.py) (plain OpenTelemetry SDK), tested end to end.
