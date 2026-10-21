// Vercel AI SDK with its built-in OpenTelemetry, using the SDK's mock model (no LLM calls).
//
//   npm install
//   OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:1984 OTEL_EXPORTER_OTLP_HEADERS=x-api-key=<key> \
//   OTEL_SERVICE_NAME=vercel-ai-demo node index.mjs
import { NodeTracerProvider } from "@opentelemetry/sdk-trace-node";
import { BatchSpanProcessor } from "@opentelemetry/sdk-trace-base";
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-proto";
import { detectResources, envDetector } from "@opentelemetry/resources";
import { generateText, tool, stepCountIs } from "ai";
import { MockLanguageModelV2 } from "ai/test";
import { z } from "zod";

// endpoint, headers and service name come from the OTEL_* environment variables
const provider = new NodeTracerProvider({
  resource: detectResources({ detectors: [envDetector] }),
  spanProcessors: [new BatchSpanProcessor(new OTLPTraceExporter())],
});
provider.register();

let step = 0;
const usage = { inputTokens: 10, outputTokens: 5, totalTokens: 15 };
const model = new MockLanguageModelV2({  // swap for a real model, e.g. openai("gpt-4o-mini")
  doGenerate: async () => step++ === 0
    ? { finishReason: "tool-calls", usage, warnings: [], content: [{ type: "tool-call", toolCallId: "call_1", toolName: "weather", input: JSON.stringify({ city: "Pune" }) }] }
    : { finishReason: "stop", usage, warnings: [], content: [{ type: "text", text: "It is sunny in Pune." }] },
});

const result = await generateText({
  model, system: "Be brief.", prompt: "Weather in Pune?", stopWhen: stepCountIs(3),
  tools: { weather: tool({ description: "Weather for a city", inputSchema: z.object({ city: z.string() }), execute: async ({ city }) => ({ city, sky: "sunny" }) }) },
  experimental_telemetry: { isEnabled: true, functionId: "weather-agent", metadata: { thread_id: "demo-thread" } },
});
console.log(result.text);
await provider.shutdown();  // flush
