"""LlamaIndex RAG query with OpenInference, using MockLLM and MockEmbedding (no LLM calls).

    pip install llama-index-core openinference-instrumentation-llama-index \
        opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
    OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:1984 OTEL_EXPORTER_OTLP_HEADERS=x-api-key=<key> \
    OTEL_SERVICE_NAME=llamaindex-demo python examples/llamaindex_demo.py
"""

from llama_index.core import Document, Settings, VectorStoreIndex
from llama_index.core.embeddings import MockEmbedding
from llama_index.core.llms import MockLLM
from openinference.instrumentation.llama_index import LlamaIndexInstrumentor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

provider = TracerProvider()
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
LlamaIndexInstrumentor().instrument(tracer_provider=provider)

Settings.llm = MockLLM(max_tokens=16)  # swap for a real LLM, e.g. llama_index.llms.openai.OpenAI()
Settings.embed_model = MockEmbedding(embed_dim=8)
index = VectorStoreIndex.from_documents([Document(text="Pune is sunny today."),
                                         Document(text="Mumbai has heavy rain.")])
print(index.as_query_engine(similarity_top_k=1).query("Weather in Pune?"))
provider.shutdown()
