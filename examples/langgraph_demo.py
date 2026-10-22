"""examples/demo_app.py (a LangGraph agent, fake model, no tracing code) traced with lighttrace.register().

    pip install lighttrace langgraph
    LIGHTTRACE_ENDPOINT=http://127.0.0.1:1984 LIGHTTRACE_API_KEY=<key> LIGHTTRACE_PROJECT=langgraph-demo \
    python examples/langgraph_demo.py
"""

import lighttrace

lighttrace.register()

import demo_app  # noqa: E402

demo_app.main()
