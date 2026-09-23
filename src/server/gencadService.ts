/**
 * Server-side bridge to GenCAD, the local image-to-CAD model.
 *
 * GenCAD is a trained model that turns an image into CAD topology. It lives in
 * its own micromamba environment because it needs pythonocc-core, which is
 * conda-only. It must be asked for its result via a file rather than stdout, so
 * this never depends on message formatting.
 *
 * What GenCAD is and is not, since the distinction decides when to call it:
 *   - It IS the right tool for a simple part from a clean CAD-style image. It
 *     produces real analytic B-Rep, not a mesh.
 *   - It is NOT able to read a scale from a picture, so its output is in
 *     normalised units. `scaleTo` is how a real dimension gets applied.
 *   - It is NOT able to handle complex multi-feature parts. Measured on a
 *     ribbed container with a funnel and an arch handle: sequences ran to 42-56
 *     commands against a 60-command ceiling and every sample failed. For that
 *     class, build from the image with the planner instead.
 *
 * A failure here is therefore expected and normal, and callers must treat it as
 * "fall back to planning from the image" rather than as an error.
 */
import { spawn } from 'node:child_process';
import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { env } from './env';

const GENCAD_DIR = env('GENCAD_DIR') || 'F:/projects/3D/apps/gencad';
const MICROMAMBA =
  env('MICROMAMBA_EXE') ||
  join(GENCAD_DIR, '.tools', 'Library', 'bin', 'micromamba.exe');
const ENV_PREFIX = env('GENCAD_ENV') || '.mamba/env';
const SCRIPT = 'gencad_to_plan.py';
const OUT_DIR = join(GENCAD_DIR, 'from_cadam');

const TIMEOUT_MS = 8 * 60 * 1000;

export type GencadResult =
  | {
      ok: true;
      step: string;
      plan: string;
      normalisedSize: number[];
      solids: number;
      faces: number;
      samplesTried: number;
      chosenSample: number;
      ms: number;
    }
  | { ok: false; reason: string; ms: number };

function run(
  args: string[],
  cwd: string,
): Promise<{ code: number; out: string }> {
  return new Promise((resolve) => {
    const child = spawn(MICROMAMBA, args, { cwd });
    let out = '';
    const timer = setTimeout(() => child.kill(), TIMEOUT_MS);
    child.stdout.on('data', (chunk) => {
      out += chunk.toString();
    });
    child.stderr.on('data', (chunk) => {
      out += chunk.toString();
    });
    child.on('error', (error) => {
      clearTimeout(timer);
      resolve({ code: -1, out: `${out}\n${String(error)}` });
    });
    child.on('close', (code) => {
      clearTimeout(timer);
      resolve({ code: code ?? -1, out });
    });
  });
}

/** True when the local GenCAD install looks usable at all. */
export function gencadAvailable(): boolean {
  return existsSync(MICROMAMBA) && existsSync(join(GENCAD_DIR, ENV_PREFIX));
}

/**
 * Convert image bytes to a STEP file, optionally scaled to a real dimension.
 *
 * `scaleTo` is the size in mm for the model's largest dimension. It has to come
 * from the user: a single image carries no scale, so there is no factor to
 * derive from the picture itself.
 */
export async function gencadFromImage(
  imageBase64: string,
  extension: string,
  scaleTo: number,
  samples = 3,
): Promise<GencadResult> {
  const started = Date.now();
  if (!gencadAvailable()) {
    return {
      ok: false,
      reason: `GenCAD is not installed where expected (${GENCAD_DIR})`,
      ms: Date.now() - started,
    };
  }

  mkdirSync(OUT_DIR, { recursive: true });
  const stamp = `${Date.now()}`;
  const imagePath = join(
    OUT_DIR,
    `input_${stamp}.${extension.replace(/^\./, '')}`,
  );
  writeFileSync(imagePath, Buffer.from(imageBase64, 'base64'));

  // Fresh output directory per run so a stale result.json can never be read as
  // this run's answer.
  const outDir = join(OUT_DIR, `run_${stamp}`);
  mkdirSync(outDir, { recursive: true });

  const { code, out } = await run(
    [
      'run',
      '-p',
      ENV_PREFIX,
      'python',
      SCRIPT,
      '-image',
      imagePath,
      '-size',
      String(scaleTo),
      '-samples',
      String(samples),
      '-out',
      outDir,
    ],
    GENCAD_DIR,
  );

  const resultPath = join(outDir, 'result.json');
  if (!existsSync(resultPath)) {
    return {
      ok: false,
      // Include the tail of the output: when GenCAD dies inside OCCT the reason
      // is only in the process log, and "no result" alone helps nobody.
      reason:
        `GenCAD produced no result (exit ${code}): ` +
        out.trim().split('\n').slice(-2).join(' | ').slice(0, 240),
      ms: Date.now() - started,
    };
  }

  try {
    const parsed = JSON.parse(await readFile(resultPath, 'utf8')) as Record<
      string,
      unknown
    >;
    if (!parsed.ok) {
      return {
        ok: false,
        reason: String(parsed.reason ?? 'GenCAD could not build a solid'),
        ms: Date.now() - started,
      };
    }
    return {
      ok: true,
      step: String(parsed.step),
      plan: String(parsed.plan),
      normalisedSize: (parsed.normalised_size as number[]) ?? [],
      solids: Number(parsed.solids ?? 0),
      faces: Number(parsed.faces ?? 0),
      samplesTried: Number(parsed.samples_tried ?? samples),
      chosenSample: Number(parsed.chosen_sample ?? 0),
      ms: Date.now() - started,
    };
  } catch (error) {
    return {
      ok: false,
      reason: `GenCAD result unreadable: ${String(error)}`,
      ms: Date.now() - started,
    };
  }
}
