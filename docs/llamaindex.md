# LlamaIndex

```python
import lighttrace
lighttrace.register()
```

`register()` turns on **OpenInference's LlamaIndex listener**: each query is one trace with its
retrieval (documents with score and metadata), embedding, synthesis and model calls. OpenTelemetry's
official LlamaIndex listener (1.2b0) records only the retrieval step today, with the model call as a
separate trace, so lighttrace doesn't use it yet. Building an index is its own trace.

Runnable: [`examples/llamaindex_demo.py`](../examples/llamaindex_demo.py) (MockLLM + MockEmbedding),
tested end to end.
