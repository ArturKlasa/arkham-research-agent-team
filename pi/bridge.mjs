// The harness's connection to pi-ai, the model layer of the pi agent toolkit (https://github.com/earendil-works/pi).
// One model call per process. stdin: {provider, model, effort, context} where context is a pi-ai Context.
// stdout, one JSON object per line: {"text": delta} while the reply streams, then {"message": AssistantMessage},
// or {"error": "..."} when no call could be made. Only the model call happens here: tools are returned, never run.
import { text } from 'node:stream/consumers';

const emit = (line) => process.stdout.write(JSON.stringify(line) + '\n');

try {
  const { builtinModels } = await import('@earendil-works/pi-ai/providers/all');
  const { provider, model: id, effort, context } = JSON.parse(await text(process.stdin));
  const models = builtinModels({ identity: { userAgent: 'short' } });
  const model = models.getModel(provider, id);
  if (!model) throw new Error(`pi has no model ${provider}/${id}; known for ${provider}: ${models.getModels(provider).map((m) => m.id).join(', ') || 'none'}`);
  for (const m of context.messages) {
    m.timestamp ??= Date.now();
    // an assistant turn that did not come from pi (a reopened session keeps only the text) gets the fields pi requires
    if (m.role === 'assistant') Object.assign(m, { api: model.api, provider: model.provider, model: model.id, stopReason: 'stop', usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } }, ...m });
  }
  // maxRetries 0: the harness owns retries, so every one of them is in its trail
  const stream = models.streamSimple(model, context, { reasoning: model.reasoning ? effort : undefined, maxTokens: Math.min(64000, model.maxTokens), maxRetries: 0 });
  for await (const event of stream) if (event.type === 'text_delta') emit({ text: event.delta });
  emit({ message: await stream.result() });
} catch (error) {
  emit({ error: String(error?.message ?? error) });
  process.exitCode = 1;
}
