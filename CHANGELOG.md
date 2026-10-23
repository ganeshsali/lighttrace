# Changelog

All notable changes to LightTrace are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.1] - 2026-10-23

### Added

- `lighttrace.tracing_context(enabled=False)`: leave the calls in a block out of the traces, with
  any framework (the listeners skip them, and LightTrace's sender drops whatever else records them).

## [0.1.0] - 2026-10-22

First release.

### Added

- `lighttrace.register()`: two lines in an app turn on the right OpenTelemetry listener for each
  installed framework and send its traces to LightTrace.
- Supported and tested end to end:
  - **Frameworks:** LangChain/LangGraph, LlamaIndex, OpenAI Agents, CrewAI, Pydantic AI, Google ADK, Strands, Microsoft Agent Framework, Semantic Kernel, DSPy, smolagents, Agno and Qwen-Agent.
  - **Raw model clients:** OpenAI, Anthropic, Gemini, Bedrock and Portkey.
  - **JavaScript:** the Vercel AI SDK.
- One OpenTelemetry receiver (`/v1/traces`, `/v1/logs`) that also reads OpenInference,
  OpenLLMetry, OpenLIT, Langfuse, MLflow, Logfire and the LangSmith SDK's OpenTelemetry mode.
- The `lighttrace` server, with a web UI:
  - **Views:** projects, threads, traces, runs and a dashboard.
  - **What it shows:** tokens, cost, latency and time to first token.
  - **Tools:** saved filters, and trace comparison.
- Storage in SQLite (default) or Postgres, retention in days or forever, API-key ingest and a
  password-protected UI.
- Docker image and a PyPI package.

[Unreleased]: https://github.com/ganeshsali/lighttrace/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/ganeshsali/lighttrace/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/ganeshsali/lighttrace/releases/tag/v0.1.0
