# LangChain

```bash
pip install lighttrace
```

```python
import lighttrace
lighttrace.register()
```

with `LIGHTTRACE_ENDPOINT`, `LIGHTTRACE_API_KEY` and `LIGHTTRACE_PROJECT` set ([overview](README.md)).
`register()` turns on **OpenTelemetry's official LangChain listener**: every chain, model call (model,
tokens, time to first token, streamed or not), tool and retriever, in one trace per call. It records model
calls itself, so the OpenAI/Anthropic client listeners stay off (no call is counted twice).

Notes:
- The listener skips model calls whose model name it can't tell, which only affects test fakes. Real
  chat models (`ChatOpenAI`, `ChatAnthropic`, …) are fine; for a fake, set
  `.with_config(metadata={"ls_model_name": "<name>"})`.
- It doesn't send your own run metadata or tags; group turns into threads with a `thread_id` /
  `session_id` / `conversation_id` metadata key.
- It is OpenTelemetry's beta (`1.2b0`) package; lighttrace accepts updates within 1.x.

## Other ways

- **The LangSmith SDK's OpenTelemetry mode** (settings only, no lighttrace package):
  `LANGSMITH_TRACING=true LANGSMITH_OTEL_ENABLED=true LANGSMITH_OTEL_ONLY=true` plus the `OTEL_*`
  settings from the [overview](README.md#without-the-lighttrace-package). Complete, sends your run
  metadata too, but no time to first token. Tested end to end (`tests/test_e2e.py`).
- **OpenInference** (`openinference-instrumentation-langchain`): see
  [`examples/otel_demo.py`](../examples/otel_demo.py). No time to first token.
