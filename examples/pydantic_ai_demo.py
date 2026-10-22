"""Pydantic AI traced with lighttrace.register(), using its TestModel (no LLM calls).

    pip install lighttrace pydantic-ai-slim
    LIGHTTRACE_ENDPOINT=http://127.0.0.1:1984 LIGHTTRACE_API_KEY=<key> LIGHTTRACE_PROJECT=pydantic-ai-demo \
    python examples/pydantic_ai_demo.py
"""

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

import lighttrace

lighttrace.register()  # also switches on Pydantic AI's built-in instrumentation

agent = Agent(TestModel(), system_prompt="Be brief.")  # swap TestModel() for "openai:gpt-4o" etc.


@agent.tool_plain
def weather(city: str) -> str:
    """Weather for a city."""
    return "sunny"


print(agent.run_sync("Weather in Pune?").output)
