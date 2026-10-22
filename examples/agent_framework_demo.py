"""Microsoft Agent Framework traced with lighttrace.register() (its own OpenTelemetry).

    pip install lighttrace agent-framework-core agent-framework-openai
    OPENAI_API_KEY=<key> LIGHTTRACE_ENDPOINT=http://127.0.0.1:1984 LIGHTTRACE_API_KEY=<key> \
    LIGHTTRACE_PROJECT=agent-framework-demo python examples/agent_framework_demo.py

Any OpenAI-compatible server works too: set OPENAI_BASE_URL.
"""

import asyncio

import lighttrace

lighttrace.register()  # also sets ENABLE_SENSITIVE_DATA, so prompts and answers are recorded

from agent_framework import tool  # noqa: E402
from agent_framework.openai import OpenAIChatClient  # noqa: E402


@tool
def weather(city: str) -> str:
    """Weather for a city."""
    return "sunny"


agent = OpenAIChatClient(model="gpt-4o-mini").as_agent(name="weather-agent", instructions="Be brief.", tools=[weather])
print(asyncio.run(agent.run("Weather in Pune?")).text)
