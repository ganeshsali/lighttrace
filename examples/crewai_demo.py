"""A one-agent CrewAI crew traced with lighttrace.register(), using a fake LLM (no LLM calls).

    pip install lighttrace crewai   # and CREWAI_DISABLE_TELEMETRY=true
    LIGHTTRACE_ENDPOINT=http://127.0.0.1:1984 LIGHTTRACE_API_KEY=<key> LIGHTTRACE_PROJECT=crewai-demo \
    python examples/crewai_demo.py
"""

from crewai import Agent, BaseLLM, Crew, Task

import lighttrace

lighttrace.register()


class FakeLLM(BaseLLM):
    """Always gives the final answer. Use llm="gpt-4o-mini" (or any LiteLLM model) for the real thing."""

    def call(self, messages, *args, **kwargs):
        return "Thought: I know the answer.\nFinal Answer: It is sunny in Pune."


agent = Agent(role="Weather reporter", goal="Answer weather questions", backstory="Brief and exact.",
              llm=FakeLLM(model="fake-llm"))
task = Task(description="Weather in Pune?", expected_output="One sentence.", agent=agent)
print(Crew(agents=[agent], tasks=[task]).kickoff())
