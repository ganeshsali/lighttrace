"""demo_app.py's traces over OpenTelemetry (OpenInference instrumentation) instead of the LangSmith SDK.

    pip install openinference-instrumentation-langchain opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
    OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:1984 OTEL_EXPORTER_OTLP_HEADERS=x-api-key=<key> \
    OTEL_SERVICE_NAME=demo-otel python examples/otel_demo.py
"""

import demo_app
from openinference.instrumentation.langchain import LangChainInstrumentor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

provider = TracerProvider()  # service.name from OTEL_SERVICE_NAME
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))  # endpoint + headers from OTEL_* env
LangChainInstrumentor().instrument(tracer_provider=provider)

demo_app.main()
provider.shutdown()  # flush
