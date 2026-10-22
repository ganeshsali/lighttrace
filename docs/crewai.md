# CrewAI

```python
import lighttrace
lighttrace.register()
```

Set `CREWAI_DISABLE_TELEMETRY=true` too (that is CrewAI's own usage telemetry, not your traces).
OpenTelemetry has no official CrewAI listener yet, so `register()` uses **OpenInference's**, which
ships with LightTrace: the crew kickoff, each agent and task.

Runnable: [`examples/crewai_demo.py`](../examples/crewai_demo.py) (a fake LLM, no API key).

Status: tested end to end through `register()` with **crewai 1.15.28** (Linux, Python 3.13).

Notes on installing:

- **Versions:** CrewAI 1.7 to 1.14 pin OpenTelemetry 1.34, so they can't sit next to LightTrace. Use 1.15 or newer.
- **Apple Silicon Macs:** CrewAI 1.15 needs `lancedb` 0.29, which has no build for macOS on Apple Silicon. There, pip falls back to CrewAI 1.6.1, which also works with LightTrace.
