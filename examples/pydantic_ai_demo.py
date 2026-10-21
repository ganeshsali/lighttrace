"""Pydantic AI with its built-in OpenTelemetry, using its TestModel (no LLM calls).

    pip install pydantic-ai-slim opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
    OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:1984 OTEL_EXPORTER_OTLP_HEADERS=x-api-key=<key> \
    OTEL_SERVICE_NAME=pydantic-ai-demo python examples/pydantic_ai_demo.py
"""

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

provider = TracerProvider()
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
trace.set_tracer_provider(provider)  # Pydantic AI uses the global provider
Agent.instrument_all()

agent = Agent(TestModel(), system_prompt="Be brief.")  # swap TestModel() for "openai:gpt-4o" etc.


@agent.tool_plain
def weather(city: str) -> str:
    """Weather for a city."""
    return "sunny"


print(agent.run_sync("Weather in Pune?").output)
provider.shutdown()
