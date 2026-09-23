import type { Model } from './types';

// Model ids persisted in conversation settings (and submitted by stale
// clients) outlive the picker catalog. Map retired ids to their successors
// so old conversations keep resolving to a routable, correctly priced model.
export const LEGACY_MODEL_IDS: Record<string, Model> = {
  'openai/gpt-5.5': 'openai/gpt-5.6-sol',
  'google/gemini-3.6-flash': 'google/gemini-3.8-flash',
  'google/gemini-3.7-flash': 'google/gemini-3.8-flash',
  'anthropic/claude-fable-5': 'anthropic/claude-fable-5.1',
  'z-ai/glm-5.2': 'z-ai/glm-5.3',
  'stealth/ox-alpha': 'z-ai/glm-5.3-flash',
};

export function normalizeModelId(model: Model): Model {
  return LEGACY_MODEL_IDS[model] ?? model;
}

/**
 * Models that accept IMAGE input.
 *
 * Keep this in sync with `supportsVision` on the picker entries in
 * src/lib/utils.ts. If the two disagree the B-Rep path attaches a render to a
 * model that rejects images and the whole request fails, so this is a guard,
 * not a hint.
 *
 * Verified against the live endpoint: `glm-5.3` answers "this model does not
 * support image input", while `glm-5.3-flash`, `gemma4:31b`, `qwen3.5:397b`
 * and `minimax-m3` all describe a test image correctly.
 */
export const VISION_CAPABLE_MODELS: ReadonlySet<string> = new Set([
  'openai/glm-5.3-flash',
  'openai/gemma4:31b',
  'openai/qwen3.5:397b',
  'openai/minimax-m3',
  'openai/minimax-m2.7',
  'openai/gemma4',
  'openai/qwen3.5',
]);

/**
 * Whether a model can be shown a render.
 *
 * Unknown ids answer true: the picker's provider list is the authority for
 * cloud models (Anthropic, Google and OpenRouter all take images), and
 * defaulting to false would silently disable the vision loop for every future
 * model. Only ids we have actually seen reject images answer false.
 */
export function modelSeesImages(model: string): boolean {
  const bare = model.startsWith('openai/') ? model : model;
  if (VISION_CAPABLE_MODELS.has(bare)) return true;
  // The one confirmed refusal, plus anything else on the Ollama gateway that is
  // not on the allowlist above.
  if (bare.startsWith('openai/')) return false;
  return true;
}
