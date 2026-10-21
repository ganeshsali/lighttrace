# Plain OpenTelemetry (your own spans, any language)

No framework needed: create spans with the OpenTelemetry SDK of your language and use the
[OTel GenAI attribute names](https://opentelemetry.io/docs/specs/semconv/gen-ai/) so lighttrace knows
what each span is.

```bash
pip install opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
export OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:1984
export OTEL_EXPORTER_OTLP_HEADERS=x-api-key=<your key>
export OTEL_SERVICE_NAME=my-app
```

```python
tracer = provider.get_tracer("my-app")   # provider from the overview
with tracer.start_as_current_span("chat my-model", attributes={
        "gen_ai.operation.name": "chat", "gen_ai.request.model": "my-model",
        "gen_ai.input.messages": json.dumps([{"role": "user", "parts": [{"type": "text", "content": q}]}])}) as span:
    answer = call_my_model(q)
    span.set_attributes({"gen_ai.usage.input_tokens": 12, "gen_ai.usage.output_tokens": 7,
        "gen_ai.output.messages": json.dumps([{"role": "assistant", "parts": [{"type": "text", "content": answer}]}])})
```

| What | Attribute |
|---|---|
| Run type | `gen_ai.operation.name`: `chat` → llm, `execute_tool` → tool, `invoke_agent` → agent, `embeddings` → embedding; anything else is a chain |
| Inputs / outputs | `gen_ai.input.messages` / `gen_ai.output.messages` (JSON), or `input.value` / `output.value` for any JSON or text |
| Tool calls | `gen_ai.tool.name`, `gen_ai.tool.call.arguments`, `gen_ai.tool.call.result` |
| Tokens and model | `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.request.model` |
| Threads | `gen_ai.conversation.id` or `session.id` (or `thread_id` inside a `metadata` JSON attribute) |
| Anything else | kept as run metadata, filterable with `key:value` |

Runnable: [`examples/manual_otel.py`](../examples/manual_otel.py), tested end to end.

Other languages work the same way: point their OTLP exporter at `http://<host>:1984` with the HTTP
protocol (`OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf` or `http/json`).

## OpenLLMetry alternative

If your spans wrap calls to an LLM client (OpenAI, Anthropic, Bedrock, ...), OpenLLMetry can create
the LLM spans for you: see [the overview](README.md#openllmetry-one-alternative-for-all-of-them).
