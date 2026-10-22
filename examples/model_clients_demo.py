"""Raw model clients (no framework) traced with lighttrace.register(): one call with one tool.

    pip install lighttrace openai anthropic google-genai boto3 portkey-ai   # whichever you use
    LIGHTTRACE_ENDPOINT=http://127.0.0.1:1984 LIGHTTRACE_API_KEY=<key> LIGHTTRACE_PROJECT=clients-demo \
    python examples/model_clients_demo.py openai      # or anthropic | gemini | bedrock | portkey

Each client reads its usual key (OPENAI_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY, AWS credentials,
PORTKEY_API_KEY) and, for another server, its usual base URL setting (OPENAI_BASE_URL,
ANTHROPIC_BASE_URL, GEMINI_BASE_URL, AWS_ENDPOINT_URL_BEDROCK_RUNTIME, PORTKEY_BASE_URL).
"""

import os
import sys

import lighttrace

lighttrace.register(clients=True)  # trace these clients even if a framework is installed too

PROMPT = "Weather in Pune?"
TOOL = {"name": "weather", "description": "Weather for a city.",
        "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}


def openai():
    from openai import OpenAI

    r = OpenAI().chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": PROMPT}],
                                         tools=[{"type": "function", "function": TOOL}])
    return r.choices[0].message


def anthropic():
    from anthropic import Anthropic

    tool = {"name": TOOL["name"], "description": TOOL["description"], "input_schema": TOOL["parameters"]}
    return Anthropic().messages.create(model="claude-haiku-4-5", max_tokens=200, tools=[tool],
                                       messages=[{"role": "user", "content": PROMPT}]).content


def gemini():
    from google import genai
    from google.genai import types

    base = os.environ.get("GEMINI_BASE_URL")
    client = genai.Client(http_options=types.HttpOptions(base_url=base) if base else None)
    tools = [types.Tool(function_declarations=[types.FunctionDeclaration(**TOOL)])]
    return client.models.generate_content(model="gemini-2.5-flash", contents=PROMPT,
                                          config=types.GenerateContentConfig(tools=tools)).candidates[0].content


def bedrock():
    import boto3

    tool = {"toolSpec": {"name": TOOL["name"], "description": TOOL["description"],
                         "inputSchema": {"json": TOOL["parameters"]}}}
    r = boto3.client("bedrock-runtime").converse(
        modelId="anthropic.claude-3-haiku-20240307-v1:0", toolConfig={"tools": [tool]},
        messages=[{"role": "user", "content": [{"text": PROMPT}]}])
    return r["output"]["message"]


def portkey():
    from portkey_ai import Portkey

    client = Portkey(base_url=os.environ.get("PORTKEY_BASE_URL"), provider="openai",
                     Authorization=f"Bearer {os.environ.get('OPENAI_API_KEY', '')}")
    r = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": PROMPT}],
                                       tools=[{"type": "function", "function": TOOL}])
    return r.choices[0].message


print(globals()[sys.argv[1] if len(sys.argv) > 1 else "openai"]())
