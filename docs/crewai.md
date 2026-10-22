# CrewAI

```python
import lighttrace
lighttrace.register()
```

Set `CREWAI_DISABLE_TELEMETRY=true` too (that is CrewAI's own usage telemetry, not your traces).
OpenTelemetry has no official CrewAI listener yet (it is being built), so `register()` uses
**OpenInference's**, which ships with lighttrace: the crew kickoff, each agent and task.

Runnable: [`examples/crewai_demo.py`](../examples/crewai_demo.py) (a fake LLM, no API key).

Status: tested end to end through `register()` with crewai 1.6.1, which is what pip chose when
installing `lighttrace` and `crewai` together (Python 3.12); the test in `tests/test_frameworks.py` skips
where CrewAI isn't installed. Some CrewAI releases pin an older OpenTelemetry (1.9 pins 1.34) and can't
sit next to lighttrace; pip then picks a compatible CrewAI, or use the
[plain OpenTelemetry route](README.md#without-the-lighttrace-package).
