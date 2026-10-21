"""OpenAI Agents SDK with OpenInference, using a fake model (no LLM calls, no OpenAI key).

    pip install openai-agents openinference-instrumentation-openai-agents \
        opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
    OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:1984 OTEL_EXPORTER_OTLP_HEADERS=x-api-key=<key> \
    OTEL_SERVICE_NAME=openai-agents-demo python examples/openai_agents_demo.py
"""

from agents import Agent, ModelResponse, Runner, Usage, function_tool, generation_span, set_trace_processors
from agents.models.interface import Model
from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText
from openinference.instrumentation.openai_agents import OpenAIAgentsInstrumentor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

provider = TracerProvider()
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
set_trace_processors([])  # don't also upload traces to OpenAI
OpenAIAgentsInstrumentor().instrument(tracer_provider=provider)


class FakeModel(Model):
    """Calls the weather tool once, then answers. Drop `model=` below to use a real OpenAI model."""

    async def get_response(self, system_instructions, input, *args, **kwargs):
        usage = Usage(requests=1, input_tokens=10, output_tokens=5, total_tokens=15)
        if any(isinstance(i, dict) and i.get("type") == "function_call_output" for i in input):
            text = ResponseOutputText(type="output_text", text="It is sunny in Pune.", annotations=[])
            out = ResponseOutputMessage(id="msg_1", type="message", role="assistant", status="completed", content=[text])
        else:
            out = ResponseFunctionToolCall(type="function_call", id="fc_1", call_id="call_1", name="weather",
                                           arguments='{"city": "Pune"}', status="completed")
        # The SDK's own models record each call like this; the instrumentor turns it into an LLM span.
        with generation_span(model="fake-model", input=[{"role": "user", "content": str(input)}],
                             usage={"input_tokens": 10, "output_tokens": 5}) as span:
            span.span_data.output = [out.model_dump()]
        return ModelResponse(output=[out], usage=usage, response_id=None)

    def stream_response(self, *args, **kwargs):
        raise NotImplementedError


@function_tool
def weather(city: str) -> str:
    """Weather for a city."""
    return "sunny"


agent = Agent(name="weather-agent", instructions="Be brief.", tools=[weather], model=FakeModel())
print(Runner.run_sync(agent, "Weather in Pune?").final_output)
provider.shutdown()
