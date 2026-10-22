# Changelog

All notable changes to lighttrace are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-10-22

First release.

### Added

- `lighttrace.register()`: two lines in an app turn on the right OpenTelemetry listener for each
  installed framework and send its traces to lighttrace.
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
