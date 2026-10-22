"""OpenAI Agents SDK traced with lighttrace.register(), using a fake model (no LLM calls, no OpenAI key).

    pip install lighttrace openai-agents
    LIGHTTRACE_ENDPOINT=http://127.0.0.1:1984 LIGHTTRACE_API_KEY=<key> LIGHTTRACE_PROJECT=openai-agents-demo \
    python examples/openai_agents_demo.py
"""

from agents import Agent, ModelResponse, Runner, Usage, function_tool, set_trace_processors
from agents.models.interface import Model
from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText

import lighttrace

set_trace_processors([])  # don't also upload traces to OpenAI (before register(), which adds its own)
lighttrace.register()


class FakeModel(Model):
    """Calls the weather tool once, then answers. Drop `model=` below to use a real OpenAI model: its
    calls are then traced too (by the OpenAI client listener register() turns on)."""

    async def get_response(self, system_instructions, input, *args, **kwargs):
        usage = Usage(requests=1, input_tokens=10, output_tokens=5, total_tokens=15)
        if any(isinstance(i, dict) and i.get("type") == "function_call_output" for i in input):
            text = ResponseOutputText(type="output_text", text="It is sunny in Pune.", annotations=[])
            out = ResponseOutputMessage(id="msg_1", type="message", role="assistant", status="completed",
                                        content=[text])
        else:
            out = ResponseFunctionToolCall(type="function_call", id="fc_1", call_id="call_1", name="weather",
                                           arguments='{"city": "Pune"}', status="completed")
        return ModelResponse(output=[out], usage=usage, response_id=None)

    def stream_response(self, *args, **kwargs):
        raise NotImplementedError


@function_tool
def weather(city: str) -> str:
    """Weather for a city."""
    return "sunny"


agent = Agent(name="weather-agent", instructions="Be brief.", tools=[weather], model=FakeModel())
print(Runner.run_sync(agent, "Weather in Pune?").final_output)
