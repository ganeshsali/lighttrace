"""A Semantic Kernel agent traced with lighttrace.register() (its own OpenTelemetry).

    pip install lighttrace semantic-kernel
    OPENAI_API_KEY=<key> LIGHTTRACE_ENDPOINT=http://127.0.0.1:1984 LIGHTTRACE_API_KEY=<key> \
    LIGHTTRACE_PROJECT=semantic-kernel-demo python examples/semantic_kernel_demo.py

Any OpenAI-compatible server works too: set OPENAI_BASE_URL.
"""

import asyncio

import lighttrace

lighttrace.register()  # before importing Semantic Kernel: it reads its diagnostics switches on import

from openai import AsyncOpenAI  # noqa: E402
from semantic_kernel.agents import ChatCompletionAgent  # noqa: E402
from semantic_kernel.connectors.ai.open_ai import OpenAIChatCompletion  # noqa: E402
from semantic_kernel.functions import kernel_function  # noqa: E402


class Weather:
    @kernel_function(description="Weather for a city.")
    def weather(self, city: str) -> str:
        return "sunny"


agent = ChatCompletionAgent(service=OpenAIChatCompletion(ai_model_id="gpt-4o-mini", async_client=AsyncOpenAI()),
                            name="weather-agent", instructions="Be brief.", plugins=[Weather()])
print(asyncio.run(agent.get_response(messages="Weather in Pune?")).content)
