"""Plain OpenTelemetry spans, no framework: an agent with one LLM call and one tool call.
lighttrace reads the OTel GenAI attributes (gen_ai.*) on them.

    pip install opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
    OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:1984 OTEL_EXPORTER_OTLP_HEADERS=x-api-key=<key> \
    OTEL_SERVICE_NAME=manual-demo python examples/manual_otel.py
"""

import json

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

provider = TracerProvider()  # service.name from OTEL_SERVICE_NAME
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))  # endpoint + headers from OTEL_* env
tracer = provider.get_tracer("manual-demo")


def call_model(messages):  # stands in for your LLM client
    return {"role": "assistant", "content": "It is sunny in Pune."}, 12, 7


with tracer.start_as_current_span("invoke_agent weather", attributes={
        "gen_ai.operation.name": "invoke_agent", "gen_ai.conversation.id": "thread-1"}):
    question = [{"role": "user", "parts": [{"type": "text", "content": "Weather in Pune?"}]}]
    with tracer.start_as_current_span("execute_tool weather", attributes={
            "gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "weather",
            "gen_ai.tool.call.arguments": json.dumps({"city": "Pune"})}) as tool:
        tool.set_attribute("gen_ai.tool.call.result", json.dumps({"sky": "sunny"}))
    with tracer.start_as_current_span("chat my-model", attributes={
            "gen_ai.operation.name": "chat", "gen_ai.request.model": "my-model",
            "gen_ai.input.messages": json.dumps(question)}) as llm:
        answer, tokens_in, tokens_out = call_model(question)
        llm.set_attributes({
            "gen_ai.output.messages": json.dumps([{"role": "assistant", "parts": [
                {"type": "text", "content": answer["content"]}]}]),
            "gen_ai.usage.input_tokens": tokens_in, "gen_ai.usage.output_tokens": tokens_out})

provider.shutdown()  # flush before exit
print("sent 1 trace")
