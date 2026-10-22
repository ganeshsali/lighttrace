"""Strands Agents traced with lighttrace.register() (Strands' own OpenTelemetry).

    pip install lighttrace "strands-agents[openai]"
    OPENAI_API_KEY=<key> LIGHTTRACE_ENDPOINT=http://127.0.0.1:1984 LIGHTTRACE_API_KEY=<key> \
    LIGHTTRACE_PROJECT=strands-demo python examples/strands_demo.py

Any OpenAI-compatible server works too: set OPENAI_BASE_URL.
"""

import lighttrace

lighttrace.register()  # before creating agents: Strands picks up the global provider

from strands import Agent, tool  # noqa: E402
from strands.models.openai import OpenAIModel  # noqa: E402


@tool
def weather(city: str) -> str:
    """Weather for a city."""
    return "sunny"


agent = Agent(model=OpenAIModel(model_id="gpt-4o-mini"), tools=[weather], system_prompt="Be brief.",
              callback_handler=None)
print(agent("Weather in Pune?"))
