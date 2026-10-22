# LangChain

```bash
pip install lighttrace
```

```python
import lighttrace
lighttrace.register()
```

with `LIGHTTRACE_ENDPOINT`, `LIGHTTRACE_API_KEY` and `LIGHTTRACE_PROJECT` set ([overview](README.md)).

`register()` turns on **OpenInference's LangChain listener**: every chain, prompt, parser, retriever, model
call and tool, nested as they ran and named the way LangChain names them, with
your run metadata and tags, model, tokens and messages. OpenInference doesn't record time to first
token, so `register()` also adds a small LangChain callback (through LangChain's public
`register_configure_hook`, no app code) that marks the first streamed token of each model call. Model
clients (OpenAI, Anthropic, …) stay untraced, so no call is counted twice.

Group turns into threads with a `thread_id` / `session_id` / `conversation_id` metadata key (LangGraph:
`config={"configurable": {"thread_id": ...}}`).

## Why not OpenTelemetry's official LangChain listener?

`opentelemetry-instrumentation-genai-langchain` (beta) records only the workflow, agents, model calls and
tools: it drops every other node and chain by design (so a graph shows flat), names spans the
OpenTelemetry way (`invoke_workflow …`, `chat …`) and sends no run metadata. lighttrace still reads its
output if you send it (tested with a real payload), but `register()` uses OpenInference for the tree.

## Other ways

- **The LangSmith SDK's OpenTelemetry mode** (settings only, no lighttrace package):
  `LANGSMITH_TRACING=true LANGSMITH_OTEL_ENABLED=true LANGSMITH_OTEL_ONLY=true` plus the `OTEL_*`
  settings from the [overview](README.md#without-the-lighttrace-package). Full tree, but no time to first
  token. Tested end to end (`tests/test_e2e.py`).
