"""A DSPy ReAct agent traced with lighttrace.register(), using DSPy's DummyLM (no LLM calls).

    pip install lighttrace dspy
    LIGHTTRACE_ENDPOINT=http://127.0.0.1:1984 LIGHTTRACE_API_KEY=<key> LIGHTTRACE_PROJECT=dspy-demo \
    python examples/dspy_demo.py

DSPy's listener records ReAct agents, tools and retrievers. Model calls are recorded by the model
client's listener when a real model is used, e.g. dspy.LM("openai/gpt-4o-mini").
"""

import dspy
from dspy.utils import DummyLM

import lighttrace

lighttrace.register()


def weather(city: str) -> str:
    """Weather for a city."""
    return "sunny"


# DummyLM answers each model call in turn: call the tool, finish, then the final answer.
lm = DummyLM([
    {"next_thought": "Look up the weather.", "next_tool_name": "weather", "next_tool_args": {"city": "Pune"}},
    {"next_thought": "I know it.", "next_tool_name": "finish", "next_tool_args": {}},
    {"reasoning": "The tool said sunny.", "answer": "It is sunny in Pune."},
])
dspy.configure(lm=lm)
print(dspy.ReAct("question -> answer", tools=[weather])(question="Weather in Pune?").answer)
