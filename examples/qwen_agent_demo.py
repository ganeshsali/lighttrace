"""Qwen-Agent traced with lighttrace.register().

    pip install lighttrace qwen-agent numpy soundfile tqdm   # qwen-agent 0.0.34 imports these without declaring them
    OPENAI_API_KEY=<key> LIGHTTRACE_ENDPOINT=http://127.0.0.1:1984 LIGHTTRACE_API_KEY=<key> \
    LIGHTTRACE_PROJECT=qwen-agent-demo python examples/qwen_agent_demo.py

Any OpenAI-compatible server works (Qwen's own, vLLM, Ollama, ...): set OPENAI_BASE_URL.
"""

import json
import os

import lighttrace

lighttrace.register()

from qwen_agent.agents import Assistant  # noqa: E402
from qwen_agent.tools.base import BaseTool, register_tool  # noqa: E402


@register_tool("weather")
class Weather(BaseTool):
    description = "Weather for a city."
    parameters = {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}  # noqa: RUF012

    def call(self, params, **kwargs):
        return json.dumps({"city": json.loads(params)["city"], "sky": "sunny"})


llm = {"model": "gpt-4o-mini", "model_type": "oai", "api_key": os.environ.get("OPENAI_API_KEY"),
       "model_server": os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
       "generate_cfg": {"use_raw_api": True}}  # the server's own tool calling
*_, final = Assistant(llm=llm, function_list=["weather"]).run([{"role": "user", "content": "Weather in Pune?"}])
print(final[-1]["content"])
