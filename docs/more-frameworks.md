# DSPy, smolagents, Agno, Qwen-Agent and raw model clients

```python
import lighttrace
lighttrace.register()
```

For these, `register()` turns on OpenTelemetry's official listener for the framework, plus the model
client's listener for the model calls. Every example below runs against OpenAI with `OPENAI_API_KEY`,
or against any OpenAI-compatible server with `OPENAI_BASE_URL` set. Each is tested end to end against
a stub server (no LLM).

| Framework | Example | What is recorded | Tested with |
|---|---|---|---|
| DSPy | [`dspy_demo.py`](../examples/dspy_demo.py) (DummyLM) | ReAct agents, tools, retrievers; model calls from the client listener when a real `dspy.LM` is used | dspy 3.4.1 |
| smolagents | [`smolagents_demo.py`](../examples/smolagents_demo.py) | the agent and each model call (its listener records no tool spans) | smolagents 1.26.0 |
| Agno | [`agno_demo.py`](../examples/agno_demo.py) | the agent, model calls, tools | agno 3.1.8 |
| Qwen-Agent | [`qwen_agent_demo.py`](../examples/qwen_agent_demo.py) | the agent, model calls, tools | qwen-agent 0.0.34 |

Qwen-Agent 0.0.34 imports `numpy`, `soundfile` and `tqdm` without declaring them:
`pip install qwen-agent numpy soundfile tqdm`.

## Raw model clients (no framework)

OpenAI, Anthropic, Google GenAI (Gemini), Bedrock (`boto3`) and Portkey calls are traced with their
official listeners: the messages, tool calls, model and tokens. Runnable:
[`model_clients_demo.py`](../examples/model_clients_demo.py) `openai | anthropic | gemini | bedrock | portkey`.
It was tested with openai 3.36.1, anthropic 2.1.0, google-genai 2.32.0, boto3 1.43.117 and
portkey-ai 2.3.4.

**When the client listeners are on.** By default `register()` turns them on only if no installed
framework records its own model calls. That rule avoids recording the same call twice.

- **Which frameworks count:** LangChain/LangGraph, LlamaIndex and CrewAI, plus the frameworks with built-in OpenTelemetry.
- **The catch:** they count when they are merely installed. If such a package comes in as a dependency of something else (`langchain-core` often does) and your app calls a client directly, pass `register(clients=True)`.
- **Anthropic:** the Anthropic SDK 2.x also emits a span of its own inside the listener's. It shows in the trace, and its tokens are counted only once.
