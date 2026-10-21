"""Google ADK with its built-in OpenTelemetry. A fake model stands in for Gemini (no LLM calls).

    pip install google-adk opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
    OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:1984 OTEL_EXPORTER_OTLP_HEADERS=x-api-key=<key> \
    OTEL_SERVICE_NAME=adk-demo python examples/google_adk_demo.py
"""

import asyncio

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

provider = TracerProvider()
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
trace.set_tracer_provider(provider)  # ADK uses the global provider; set it before importing ADK

from google.adk.agents import LlmAgent  # noqa: E402
from google.adk.models.base_llm import BaseLlm  # noqa: E402
from google.adk.models.llm_response import LlmResponse  # noqa: E402
from google.adk.runners import InMemoryRunner  # noqa: E402
from google.genai import types  # noqa: E402


class FakeLlm(BaseLlm):
    """Calls the weather tool once, then answers. Use model="gemini-2.5-flash" for the real thing."""
    model: str = "fake-gemini"

    async def generate_content_async(self, llm_request, stream=False):
        answered = any(p.function_response for p in llm_request.contents[-1].parts or [])
        part = types.Part(text="It is sunny in Pune.") if answered else types.Part(
            function_call=types.FunctionCall(name="weather", args={"city": "Pune"}))
        yield LlmResponse(content=types.Content(role="model", parts=[part]),
                          usage_metadata=types.GenerateContentResponseUsageMetadata(
                              prompt_token_count=10, candidates_token_count=4, total_token_count=14))


def weather(city: str) -> dict:
    """Weather for a city."""
    return {"sky": "sunny"}


async def main():
    runner = InMemoryRunner(agent=LlmAgent(name="weather_agent", model=FakeLlm(), instruction="Be brief.",
                                           tools=[weather]), app_name="adk-demo")
    session = await runner.session_service.create_session(app_name="adk-demo", user_id="u1")
    message = types.Content(role="user", parts=[types.Part(text="Weather in Pune?")])
    async for event in runner.run_async(user_id="u1", session_id=session.id, new_message=message):
        if event.content and event.content.parts and event.content.parts[0].text:
            print(event.content.parts[0].text)


asyncio.run(main())
provider.shutdown()
