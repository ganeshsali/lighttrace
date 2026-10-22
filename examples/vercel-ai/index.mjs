// Vercel AI SDK with its OpenTelemetry integration (@ai-sdk/otel), using the SDK's mock model (no LLM calls).
//
//   npm install
//   OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:1984 OTEL_EXPORTER_OTLP_HEADERS=x-api-key=<key> \
//   OTEL_SERVICE_NAME=vercel-ai-demo node index.mjs
import { OpenTelemetry } from "@ai-sdk/otel";
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-proto";
import { detectResources, envDetector } from "@opentelemetry/resources";
import { BatchSpanProcessor } from "@opentelemetry/sdk-trace-base";
import { NodeTracerProvider } from "@opentelemetry/sdk-trace-node";
import { generateText, registerTelemetry, stepCountIs, tool } from "ai";
import { MockLanguageModelV4 } from "ai/test";
import { z } from "zod";

// endpoint, headers and service name come from the OTEL_* environment variables
const provider = new NodeTracerProvider({
  resource: detectResources({ detectors: [envDetector] }),
  spanProcessors: [new BatchSpanProcessor(new OTLPTraceExporter())],
});
provider.register();
registerTelemetry(new OpenTelemetry());  // GenAI-convention spans for every AI SDK call

let step = 0;
const usage = {
  inputTokens: { total: 10, noCache: 10, cacheRead: 0, cacheWrite: 0 },
  outputTokens: { total: 5, text: 5, reasoning: 0 },
};
const model = new MockLanguageModelV4({  // swap for a real model, e.g. openai("gpt-4o-mini")
  doGenerate: async () => step++ === 0
    ? { finishReason: { unified: "tool-calls", raw: "tool_calls" }, usage, warnings: [],
        content: [{ type: "tool-call", toolCallId: "call_1", toolName: "weather", input: JSON.stringify({ city: "Pune" }) }] }
    : { finishReason: { unified: "stop", raw: "stop" }, usage, warnings: [],
        content: [{ type: "text", text: "It is sunny in Pune." }] },
});

const result = await generateText({
  model, system: "Be brief.", prompt: "Weather in Pune?", stopWhen: stepCountIs(3),
  tools: { weather: tool({ description: "Weather for a city", inputSchema: z.object({ city: z.string() }),
                           execute: async ({ city }) => ({ city, sky: "sunny" }) }) },
  telemetry: { functionId: "weather-agent" },
});
console.log(result.text);
await provider.shutdown();  // flush
