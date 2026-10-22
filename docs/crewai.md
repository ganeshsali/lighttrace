# CrewAI

```python
import lighttrace
lighttrace.register()
```

Set `CREWAI_DISABLE_TELEMETRY=true` too (that is CrewAI's own usage telemetry, not your traces).
OpenTelemetry has no official CrewAI listener yet (it is being built), so `register()` uses
**OpenInference's**, which ships with lighttrace: the crew kickoff, each agent and task.

Runnable: [`examples/crewai_demo.py`](../examples/crewai_demo.py) (a fake LLM, no API key).

Status: run by hand with crewai 1.9.3; the test in `tests/test_frameworks.py` skips where CrewAI isn't
installed. Current CrewAI (1.15) allows OpenTelemetry 1.42 or newer, which lighttrace needs; **CrewAI
1.9 pins OpenTelemetry 1.34 and can't be installed next to lighttrace**: upgrade CrewAI, or use the
[plain OpenTelemetry route](README.md#without-the-lighttrace-package).
