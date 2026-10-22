# OpenAI Agents SDK

```python
from agents import set_trace_processors
import lighttrace

set_trace_processors([])   # optional: stop the SDK uploading traces to OpenAI too (call before register)
lighttrace.register()
```

`register()` turns on OpenTelemetry's official **OpenAI Agents listener** (the workflow, each agent,
tool and handoff) and the official **OpenAI client listener**, which records the model calls (tokens,
messages) inside the same trace.

Runnable: [`examples/openai_agents_demo.py`](../examples/openai_agents_demo.py) uses a fake model, so it
shows the agent and tool but no model call. `tests/test_frameworks.py` also runs the real
`OpenAIChatCompletionsModel` against a stub server: workflow, agent, tool and model calls in one trace.
