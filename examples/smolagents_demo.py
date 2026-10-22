"""smolagents traced with lighttrace.register().

    pip install lighttrace "smolagents[openai]"
    OPENAI_API_KEY=<key> LIGHTTRACE_ENDPOINT=http://127.0.0.1:1984 LIGHTTRACE_API_KEY=<key> \
    LIGHTTRACE_PROJECT=smolagents-demo python examples/smolagents_demo.py

Any OpenAI-compatible server works too: set OPENAI_BASE_URL.
"""

import lighttrace

lighttrace.register()

from smolagents import OpenAIModel, ToolCallingAgent, tool  # noqa: E402


@tool
def weather(city: str) -> str:
    """Weather for a city.

    Args:
        city: The city.
    """
    return "sunny"


agent = ToolCallingAgent(tools=[weather], model=OpenAIModel(model_id="gpt-4o-mini"), max_steps=3, verbosity_level=0)
print(agent.run("Weather in Pune?"))
