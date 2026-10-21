"""A one-agent CrewAI crew with OpenInference, using a fake LLM (no LLM calls).

    pip install crewai openinference-instrumentation-crewai opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
    CREWAI_DISABLE_TELEMETRY=true OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:1984 \
    OTEL_EXPORTER_OTLP_HEADERS=x-api-key=<key> OTEL_SERVICE_NAME=crewai-demo python examples/crewai_demo.py
"""

from crewai import Agent, BaseLLM, Crew, Task
from openinference.instrumentation.crewai import CrewAIInstrumentor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

provider = TracerProvider()
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
CrewAIInstrumentor().instrument(tracer_provider=provider)


class FakeLLM(BaseLLM):
    """Always gives the final answer. Use llm="gpt-4o-mini" (or any LiteLLM model) for the real thing."""

    def call(self, messages, *args, **kwargs):
        return "Thought: I know the answer.\nFinal Answer: It is sunny in Pune."


agent = Agent(role="Weather reporter", goal="Answer weather questions", backstory="Brief and exact.",
              llm=FakeLLM(model="fake-llm"))
task = Task(description="Weather in Pune?", expected_output="One sentence.", agent=agent)
print(Crew(agents=[agent], tasks=[task]).kickoff())
provider.shutdown()
