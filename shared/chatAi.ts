import { tool, type InferUITools, type UIMessage } from 'ai';
import { z } from 'zod';
import type { MeshFileType, Model } from './types.ts';

export const createMeshInputSchema = z.object({
  text: z.string().optional(),
  imageIds: z.array(z.string()).optional(),
  meshId: z.string().optional(),
  model: z.enum(['fast', 'quality', 'ultra']).optional(),
  meshTopology: z.enum(['quads', 'polys']).optional(),
  polygonCount: z.number().optional(),
});

export const createMeshOutputSchema = z.object({
  id: z.string(),
  fileType: z.enum(['glb', 'stl', 'obj', 'fbx']),
});

export const parametricArtifactSchema = z.object({
  title: z.string().min(1),
  version: z.string().default('v1'),
  code: z.string().min(20),
});

export const parametricCompileOutputSchema = z.object({
  status: z.literal('success'),
  message: z.string(),
  inspection: z
    .object({
      views: z.array(
        z.enum(['ISO', 'FRONT', 'BACK', 'LEFT', 'RIGHT', 'TOP', 'BOTTOM']),
      ),
      imageAttached: z.boolean(),
    })
    .optional(),
});

export const answerUserSchema = z.object({
  message: z.string().min(1),
});

/**
 * B-Rep artefacts. The `plan` is deliberately a loose record: the authoritative
 * schema is pydantic's ModelPlan inside services/brep, and duplicating ~15 ops
 * in zod here would guarantee the two drift apart. The model learns the
 * vocabulary from the system prompt; the service is what rejects a bad plan, and
 * it rejects it with an exact field path.
 */
export const brepPlanInputSchema = z.object({
  title: z.string().min(1).describe('Short name for the artefact'),
  version: z.string().default('v1'),
  summary: z
    .string()
    .optional()
    .describe('One line describing what changed in this revision'),
  /**
   * The plan's TOP LEVEL is described so the model has a shape to fill in, but
   * the entries stay loose: pydantic's ModelPlan inside services/brep is the
   * authority for operations, and duplicating ~15 ops here would guarantee the
   * two drift apart.
   *
   * The top level is NOT optional detail. A schema of
   * `{plan: {type: object, additionalProperties: {}}}` has no described fields
   * at all, and models respond by writing the plan as prose instead of calling
   * the tool -- measured: gemma4:31b returned empty text with the loose schema
   * and called the tool correctly with a shaped one.
   */
  plan: z
    .object({
      units: z.literal('mm').optional(),
      description: z.string().optional(),
      parameters: z
        .array(z.record(z.unknown()))
        .optional()
        .describe('User-facing sliders: name, value, min, max, step, label'),
      derived: z
        .array(z.record(z.unknown()))
        .optional()
        .describe('Computed symbols: {name, expr} over the parameters'),
      features: z
        .array(z.record(z.unknown()))
        .describe(
          'The operations in order. Each is {op, id, ...fields}. The operation ' +
            'vocabulary is in the system prompt.',
        ),
      checks: z
        .array(z.record(z.unknown()))
        .optional()
        .describe('Declared fit checks: {kind, a, b, expect, tolerance}'),
      parts: z
        .record(z.string())
        .optional()
        .describe('Component export map: {part_name: feature_id}'),
      result: z.string().optional().describe('Feature id of the final model'),
    })
    .describe('A ModelPlan: parameters, derived symbols, features, fit checks'),
});

export const brepCompileOutputSchema = z.object({
  status: z.enum(['success', 'error', 'invalid']),
  message: z.string(),
  stats: z.record(z.unknown()).optional(),
  checks: z.array(z.record(z.unknown())).optional(),
  /** STEP for the whole model, base64 — the format CADAM has never had. */
  stepBase64: z.string().optional(),
  stlBase64: z.string().optional(),
  /** data: URL of the render, so the model can be shown its own geometry. */
  renderDataUrl: z.string().optional(),
  partNames: z.array(z.string()).optional(),
});

/** Model IDs that can accept image input. `supportsVision` in lib/utils is the
 * UI's source of truth; this is the server's guard for the image path, because
 * some models in the picker reject images outright (glm-5.3 among them). */
export const brepPlanToolName = 'build_brep_model' as const;

export const chatTools = {
  build_parametric_model: tool({
    description:
      'Create or update the complete OpenSCAD CAD artifact. After the browser compiles it, inspect the returned multi-view preview sheet and call this tool again if the model needs another revision.',
    inputSchema: parametricArtifactSchema,
    outputSchema: parametricCompileOutputSchema,
  }),
  answer_user: tool({
    description:
      'Send the final user-facing chat message. Use this for normal non-CAD replies, and after a CAD build when the multi-view preview satisfies the user request.',
    inputSchema: answerUserSchema,
    outputSchema: answerUserSchema,
  }),
  create_mesh: tool({
    description:
      'Create a 3D mesh from text, images, or an existing mesh plus edit instructions.',
    inputSchema: createMeshInputSchema,
    outputSchema: createMeshOutputSchema,
  }),
  build_brep_model: tool({
    description:
      'Create or update an EXACT B-Rep CAD model by emitting a ModelPlan — a list of ' +
      'typed operations (sketches, extrude, revolve, loft, boolean, fillet, chamfer, ' +
      'shell, hole, patterns) with numeric parameters. Unlike build_parametric_model ' +
      'this produces real analytic geometry and a STEP file, and the service verifies ' +
      'declared fit checks. Inspect the returned render before finalising.',
    inputSchema: brepPlanInputSchema,
    outputSchema: brepCompileOutputSchema,
  }),
};

export type AppTools = InferUITools<typeof chatTools>;

export type MeshContextData = {
  meshId: string;
  fileType: MeshFileType;
  filename?: string;
  boundingBox?: { x: number; y: number; z: number };
};

export type MeshPreferencesData = {
  topology: 'quads' | 'polys';
  polygonCount: number;
};

/**
 * Conversation-level signals the server emits as transient stream parts
 * (`writer.write({ transient: true, type: 'data-X', data })`). Transient
 * parts never land in `messages.parts` — they're side-channel updates the
 * client folds straight into the conversation query cache.
 *
 *  * `title-update`    fires once when the server generates a title for
 *    a fresh conversation; client updates `conversations.title`.
 *  * `suggestions-update` fires after each assistant turn finishes;
 *    client updates `conversations.settings.suggestions` so the pills
 *    below the input refresh in lock-step with the response.
 */
export type ConversationTitleUpdate = {
  conversationId: string;
  title: string;
};
export type ConversationSuggestionsUpdate = {
  conversationId: string;
  suggestions: string[];
};

export type AppDataTypes = {
  'mesh-context': MeshContextData;
  'mesh-preferences': MeshPreferencesData;
  'title-update': ConversationTitleUpdate;
  'suggestions-update': ConversationSuggestionsUpdate;
};

export const meshContextDataSchema = z.object({
  meshId: z.string(),
  fileType: z.enum(['glb', 'stl', 'obj', 'fbx']),
  filename: z.string().optional(),
  boundingBox: z
    .object({ x: z.number(), y: z.number(), z: z.number() })
    .optional(),
});

export const meshPreferencesDataSchema = z.object({
  topology: z.enum(['quads', 'polys']),
  polygonCount: z.number(),
});

export type AppUIMessage = UIMessage<
  {
    model?: Model;
    billingTokens?: number;
    // The model's original OpenSCAD for this message's artifact, captured
    // lazily on the FIRST parameter edit (see `persistParameterEdit`).
    // Parameter edits rewrite the live `tool-build_parametric_model` input
    // code in place, which would otherwise move the derived `defaultValue`
    // to the edited value on every reload. Stashing the original here —
    // message metadata is UI-only and NOT sent to the model by
    // `convertToModelMessages` — lets the client re-derive stable defaults
    // (Reset / slider home / auto range) with no second code copy in the
    // model's context, no migration, and no storage cost on the (common)
    // never-edited artifacts.
    originalCode?: string;
  },
  AppDataTypes,
  AppTools
>;
