"""lighttrace.register(): send an app's AI traces to a LightTrace server.

LightTrace writes no instrumentation of its own. register() turns on the listeners that
already exist for whatever the app has installed (OpenTelemetry's official GenAI
instrumentations, or the framework's built-in OpenTelemetry) and points one OTLP/HTTP
sender at LightTrace's receiver.
"""

import logging
import os
from importlib import import_module
from importlib.metadata import PackageNotFoundError, distribution

log = logging.getLogger("lighttrace")

# (distribution the app has installed, "module:Instrumentor", records model calls itself).
# OpenTelemetry's official listeners where they are complete; OpenInference's (Apache-2.0)
# where the official one is missing or records too little.
FRAMEWORKS = [
    # + LangGraph. OpenInference's: it records every node and chain with LangChain's own names (the
    # run tree) and the run's metadata; the official one keeps only workflow, agent,
    # model and tool spans. Time to first token comes from _mark_first_tokens() below.
    ("langchain-core", "openinference.instrumentation.langchain:LangChainInstrumentor", True),
    # The official one (1.2b0) records only retrievals: no query, no model call, one trace each.
    ("llama-index-core", "openinference.instrumentation.llama_index:LlamaIndexInstrumentor", True),
    ("crewai", "openinference.instrumentation.crewai:CrewAIInstrumentor", True),  # no official one yet
    # These record the workflow, agents and tools; the model calls come from the client listener.
    ("openai-agents", "opentelemetry.instrumentation.genai.openai_agents:OpenAIAgentsInstrumentor", False),
    ("dspy", "opentelemetry.instrumentation.genai.dspy:DSPyInstrumentor", False),
    ("smolagents", "opentelemetry.instrumentation.genai.smolagents:SmolagentsInstrumentor", False),
    ("agno", "opentelemetry.instrumentation.genai.agno:AgnoInstrumentor", False),
    ("qwen-agent", "opentelemetry.instrumentation.genai.qwen_agent:QwenAgentInstrumentor", False),
]
# Frameworks that emit OpenTelemetry themselves: they only need the sender (Pydantic AI
# also needs switching on, below).
BUILT_IN = ["google-adk", "strands-agents", "agent-framework-core", "semantic-kernel", "pydantic-ai-slim"]
# Model clients, traced unless every framework present records its model calls itself: then the
# same call would be recorded twice (and, in async LangChain code, as a separate trace).
CLIENTS = [
    ("openai", "opentelemetry.instrumentation.genai.openai:OpenAIInstrumentor"),
    ("anthropic", "opentelemetry.instrumentation.genai.anthropic:AnthropicInstrumentor"),
    ("boto3", "opentelemetry.instrumentation.genai.bedrock:BedrockInstrumentor"),
    ("google-genai", "opentelemetry.instrumentation.google_genai:GoogleGenAiSdkInstrumentor"),
    ("portkey-ai", "opentelemetry.instrumentation.genai.portkey:PortkeyInstrumentor"),
]

_provider = None


def _installed(name):
    try:
        distribution(name)
        return True
    except PackageNotFoundError:
        return False


def _targets(clients=None):
    """The (distribution, "module:Instrumentor") listeners to turn on for this environment."""
    frameworks = [t for t in FRAMEWORKS if _installed(t[0])]
    if clients is None:
        covered = [own for _, _, own in frameworks] + [True for d in BUILT_IN if _installed(d)]
        clients = not covered or not all(covered)
    return [t[:2] for t in frameworks] + ([t for t in CLIENTS if _installed(t[0])] if clients else [])


def _mark_first_tokens(instrumentor):
    """Put a `new_token` event on each streamed LangChain model call's span at its first token,
    which is how LightTrace measures time to first token (OpenInference doesn't record it).
    LangChain's register_configure_hook adds the handler to every run, with no app code."""
    from contextvars import ContextVar

    from langchain_core.callbacks import BaseCallbackHandler
    from langchain_core.tracers.context import register_configure_hook

    class FirstToken(BaseCallbackHandler):
        def __init__(self):
            self.seen = set()  # ponytail: grows by one id per streamed model call; fine per process

        def on_llm_new_token(self, token, *, run_id, **kwargs):
            if run_id not in self.seen:
                self.seen.add(run_id)
                if (span := instrumentor.get_span(run_id)) is not None:
                    span.add_event("new_token")

    # One handler for the whole process, shared on purpose: it is what LangChain adds to every run.
    register_configure_hook(ContextVar("lighttrace_first_token", default=FirstToken()), inheritable=True)  # noqa: B039


def _semantic_kernel_events():
    """Semantic Kernel logs prompts and answers as GenAI events (Python log records, one JSON message
    each) for an OpenTelemetry log exporter; put them on the model call's span as span events instead."""
    import json

    from opentelemetry import trace

    class SpanEvents(logging.Handler):
        def emit(self, record):
            name = getattr(record, "event.name", None)
            if not name:
                return
            try:
                body = json.loads(record.getMessage())
            except ValueError:
                body = record.getMessage()
            body = body if isinstance(body, dict) else {"content": body}
            trace.get_current_span().add_event(name, {k: v if isinstance(v, (str, bool, int, float)) else json.dumps(v)
                                                      for k, v in body.items() if v is not None})

    events = logging.getLogger("semantic_kernel.utils.telemetry.model_diagnostics.decorators")
    events.addHandler(SpanEvents())
    events.setLevel(logging.INFO)
    events.propagate = False  # these records hold prompts and answers: keep them out of the app's own logs


def register(project=None, *, endpoint=None, api_key=None, capture_content=True, clients=None):
    """Trace this app to LightTrace. Call once at startup, before the app does any AI work.

    Settings default to the environment: LIGHTTRACE_ENDPOINT (the server's base URL),
    LIGHTTRACE_API_KEY (one of the server's LIGHTTRACE_API_KEYS) and LIGHTTRACE_PROJECT
    (else OTEL_SERVICE_NAME). Without an endpoint and a key it does nothing, so the same
    code runs untraced wherever they aren't set.

    capture_content: send prompts, answers, tool arguments and results (LightTrace is
    self-hosted). clients: also trace raw model clients (OpenAI, Anthropic, Bedrock, Google
    GenAI, Portkey); by default only when no framework is installed.

    Returns the OpenTelemetry TracerProvider, or None when tracing is off.
    """
    global _provider
    if _provider is not None:
        return _provider
    endpoint = (endpoint or os.environ.get("LIGHTTRACE_ENDPOINT") or "").strip().rstrip("/")
    api_key = api_key or os.environ.get("LIGHTTRACE_API_KEY") or ""
    project = project or os.environ.get("LIGHTTRACE_PROJECT") or os.environ.get("OTEL_SERVICE_NAME") or "default"
    if not endpoint or not api_key:
        log.info("lighttrace: LIGHTTRACE_ENDPOINT or LIGHTTRACE_API_KEY not set, not tracing")
        return None

    # Read by the listeners when they start, so set before instrumenting; explicit settings win.
    os.environ.setdefault("OTEL_SEMCONV_STABILITY_OPT_IN", "gen_ai_latest_experimental")
    if capture_content:
        os.environ.setdefault("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "SPAN_ONLY")
        os.environ.setdefault("ENABLE_SENSITIVE_DATA", "true")  # Microsoft Agent Framework
        os.environ.setdefault("SEMANTICKERNEL_EXPERIMENTAL_GENAI_ENABLE_OTEL_DIAGNOSTICS_SENSITIVE", "true")
    else:  # OpenInference listeners record content unless told not to
        for var in ("OPENINFERENCE_HIDE_INPUTS", "OPENINFERENCE_HIDE_OUTPUTS",
                    "OPENINFERENCE_HIDE_INPUT_MESSAGES", "OPENINFERENCE_HIDE_OUTPUT_MESSAGES"):
            os.environ.setdefault(var, "true")
    os.environ.setdefault("SEMANTICKERNEL_EXPERIMENTAL_GENAI_ENABLE_OTEL_DIAGNOSTICS", "true")

    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    url = endpoint if endpoint.endswith("/v1/traces") else endpoint + "/v1/traces"
    sender = BatchSpanProcessor(OTLPSpanExporter(endpoint=url, headers={
        "x-api-key": api_key, "x-lighttrace-project": project}))
    existing = trace.get_tracer_provider()
    if isinstance(existing, TracerProvider):  # the app already set up OpenTelemetry: add to it
        existing.add_span_processor(sender)
        provider = existing
    else:
        provider = TracerProvider(resource=Resource.create({"service.name": project}))
        provider.add_span_processor(sender)
        trace.set_tracer_provider(provider)  # built-in frameworks read the global provider

    on = []
    for dist, target in _targets(clients):
        module, _, cls = target.partition(":")
        try:
            instrumentor = getattr(import_module(module), cls)()
        except ImportError:  # a listener missing from this install: say so, keep going
            log.warning("lighttrace: no listener installed for %s", dist)
            continue
        try:
            # The library is installed (checked above); its version range is the listener's
            # concern, and a mismatch shouldn't stop the app from starting.
            instrumentor.instrument(tracer_provider=provider, skip_dep_check=True)
            on.append(dist)
            if dist == "langchain-core":
                _mark_first_tokens(instrumentor)
        except Exception:
            log.warning("lighttrace: could not trace %s", dist, exc_info=True)
    if _installed("pydantic-ai-slim"):
        try:
            from pydantic_ai import Agent

            Agent.instrument_all()
        except Exception:
            log.warning("lighttrace: could not trace pydantic-ai", exc_info=True)
    if capture_content and _installed("semantic-kernel"):
        _semantic_kernel_events()
    on +=[d for d in BUILT_IN if _installed(d)]
    log.info("lighttrace: tracing %s to %s, project %r", ", ".join(on) or "nothing found yet", endpoint, project)
    _provider = provider
    return provider
