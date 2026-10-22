"""Agno traced with lighttrace.register().

    pip install lighttrace agno openai
    OPENAI_API_KEY=<key> LIGHTTRACE_ENDPOINT=http://127.0.0.1:1984 LIGHTTRACE_API_KEY=<key> \
    LIGHTTRACE_PROJECT=agno-demo python examples/agno_demo.py

Any OpenAI-compatible server works too: set OPENAI_BASE_URL.
"""

import lighttrace

lighttrace.register()

from agno.agent import Agent  # noqa: E402
from agno.models.openai import OpenAIChat  # noqa: E402


def weather(city: str) -> str:
    """Weather for a city."""
    return "sunny"


agent = Agent(name="weather-agent", model=OpenAIChat(id="gpt-4o-mini"), tools=[weather], instructions="Be brief.")
print(agent.run("Weather in Pune?").content)
