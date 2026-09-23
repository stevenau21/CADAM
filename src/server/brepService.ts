/**
 * Node-side client for the local B-Rep build service.
 *
 * CADAM compiles OpenSCAD in the BROWSER via a WASM worker. OpenCascade has no
 * practical browser build, so the B-Rep engine runs as a separate local Python
 * process (services/brep) and this module is the only place that talks to it.
 *
 * That has one happy consequence: unlike `build_parametric_model`, which has to
 * be executed client-side, `build_brep_model` can execute HERE, on the server.
 * The browser never needs to know how the geometry was produced.
 */
import { env } from './env';

const DEFAULT_URL = 'http://127.0.0.1:8765';

export type BrepStats = {
  bbox: number[];
  bbox_min: number[];
  bbox_max: number[];
  is_valid: boolean | null;
  volume: number | null;
  solids: number | null;
  faces: number | null;
};

export type BrepCheck = {
  kind: 'gap' | 'interference';
  a: string;
  b: string;
  value: number;
  expect?: number;
  tolerance?: number;
  pass?: boolean;
};

export type BrepResult = {
  ok: boolean;
  ms: number;
  error?: string | null;
  traceback?: string | null;
  validationErrors?: Array<{ path: string; msg: string; type?: string }> | null;
  stats?: BrepStats | null;
  checks?: BrepCheck[] | null;
  resultId?: string | null;
  symbols?: Record<string, number> | null;
  stdout?: string;
  /** base64 payloads keyed by format: step | stl | glb | `step:<partName>` */
  files?: Record<string, string>;
  outputs?: Record<string, { path: string; bytes: number }>;
  artifactDir?: string | null;
};

function baseUrl(): string {
  return (env('BREP_SERVICE_URL') || DEFAULT_URL).replace(/\/$/, '');
}

async function call(
  path: string,
  body: unknown,
  timeoutMs: number,
): Promise<BrepResult> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${baseUrl()}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    // The service answers 4xx/5xx with the same envelope, so parse first.
    const data = (await response.json()) as Record<string, unknown>;
    return {
      ok: Boolean(data.ok),
      ms: Number(data.ms ?? 0),
      error: (data.error as string | null) ?? null,
      traceback: (data.traceback as string | null) ?? null,
      validationErrors:
        (data.validation_errors as BrepResult['validationErrors']) ?? null,
      stats: (data.stats as BrepStats | null) ?? null,
      checks: (data.checks as BrepCheck[] | null) ?? null,
      resultId: (data.result_id as string | null) ?? null,
      symbols: (data.symbols as Record<string, number> | null) ?? null,
      stdout: (data.stdout as string) ?? '',
      files: (data.files as Record<string, string>) ?? {},
      outputs: (data.outputs as BrepResult['outputs']) ?? {},
      artifactDir: (data.artifact_dir as string | null) ?? null,
    };
  } catch (error) {
    const message =
      error instanceof Error ? error.message : 'unknown transport error';
    const hint = message.includes('abort')
      ? `timed out after ${timeoutMs}ms`
      : message;
    return {
      ok: false,
      ms: timeoutMs,
      error: `B-Rep service unreachable at ${baseUrl()}: ${hint}`,
      files: {},
    };
  } finally {
    clearTimeout(timer);
  }
}

/** Compile a ModelPlan into a solid. Returns STEP/STL/GLB plus the fit checks. */
export function buildPlan(
  plan: unknown,
  timeoutMs = 300_000,
): Promise<BrepResult> {
  return call('/plan', { plan }, timeoutMs);
}

/** Validate a ModelPlan without compiling it — cheap enough to gate a retry. */
export function validatePlan(
  plan: unknown,
  timeoutMs = 30_000,
): Promise<BrepResult> {
  return call('/validate', { plan }, timeoutMs);
}

export async function brepHealthy(): Promise<boolean> {
  try {
    const response = await fetch(`${baseUrl()}/health`, {
      signal: AbortSignal.timeout(4000),
    });
    return response.ok;
  } catch {
    return false;
  }
}

/**
 * Render a compiled artefact to a PNG the model can look at, via cadgen.
 * This is what closes the vision loop: the model is shown its own geometry
 * instead of being asked to trust it.
 */
export async function serviceVersion(): Promise<Record<
  string,
  unknown
> | null> {
  try {
    const response = await fetch(`${baseUrl()}/health`, {
      signal: AbortSignal.timeout(4000),
    });
    return response.ok
      ? ((await response.json()) as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}
