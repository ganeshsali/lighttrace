"""LlamaIndex RAG query traced with lighttrace.register(), using MockLLM and MockEmbedding (no LLM calls).

    pip install lighttrace llama-index-core
    LIGHTTRACE_ENDPOINT=http://127.0.0.1:1984 LIGHTTRACE_API_KEY=<key> LIGHTTRACE_PROJECT=llamaindex-demo \
    python examples/llamaindex_demo.py
"""

from llama_index.core import Document, Settings, VectorStoreIndex
from llama_index.core.embeddings import MockEmbedding
from llama_index.core.llms import MockLLM

import lighttrace

lighttrace.register()

Settings.llm = MockLLM(max_tokens=16)  # swap for a real LLM, e.g. llama_index.llms.openai.OpenAI()
Settings.embed_model = MockEmbedding(embed_dim=8)
index = VectorStoreIndex.from_documents([Document(text="Pune is sunny today."),
                                         Document(text="Mumbai has heavy rain.")])
print(index.as_query_engine(similarity_top_k=1).query("Weather in Pune?"))
