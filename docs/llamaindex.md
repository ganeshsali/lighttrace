# LlamaIndex

```bash
pip install openinference-instrumentation-llama-index opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
export OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:1984
export OTEL_EXPORTER_OTLP_HEADERS=x-api-key=<your key>
export OTEL_SERVICE_NAME=my-rag-app
```

```python
from openinference.instrumentation.llama_index import LlamaIndexInstrumentor
# provider = TracerProvider() ... as in the overview
LlamaIndexInstrumentor().instrument(tracer_provider=provider)
```

Queries show as traces with their retriever (documents with score and metadata), embedding,
synthesizer and LLM steps. Building an index is a separate trace.

Runnable: [`examples/llamaindex_demo.py`](../examples/llamaindex_demo.py) (MockLLM + MockEmbedding),
tested end to end in `tests/test_frameworks.py`.

## OpenLLMetry alternative

```bash
pip install traceloop-sdk
```

```python
from traceloop.sdk import Traceloop
Traceloop.init(app_name="my-rag-app", api_endpoint="http://127.0.0.1:1984", headers={"x-api-key": "<your key>"})
```
