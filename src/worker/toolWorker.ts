// Module-singleton OpenSCAD worker for client-side tool execution.
//
// `useOpenSCAD` spawns a per-component worker that gets `terminate()`'d
// on unmount. That's correct for stateful viewers (live preview state
// would otherwise leak across mounts) but wrong for tool execution
// triggered by the AI SDK: when the user navigates to a different
// conversation while a `build_parametric_model` tool is in flight,
// `ChatSession` unmounts, the per-instance worker dies, the pending
// `previewScadColored` promise rejects with "Worker terminated", and
// `handleToolCall` persists `output-error` to DB. The user comes back
// to a failed build that actually never had a chance to run.
//
// The fix is to give the tool-execution path a worker that doesn't
// depend on component lifecycle. Per-request IDs route responses to
// the right caller, so concurrent calls don't cross-contaminate.

import {
  OpenSCADWorkerResponseData,
  WorkerMessage,
  WorkerMessageType,
} from '@/worker/types';
import { errorFromWorker } from '@/worker/workerError';
import { resolveMeshFile } from '@/worker/meshFileStore';
import { extractImportFilenames } from '@/utils/scadImports';

type PendingRequest = {
  resolve: (value: OpenSCADWorkerResponseData) => void;
  reject: (error: Error) => void;
};

const pending = new Map<string, PendingRequest>();
let workerInstance: Worker | null = null;

function getToolWorker(): Worker {
  if (workerInstance) return workerInstance;
  workerInstance = new Worker(new URL('./worker.ts', import.meta.url), {
    type: 'module',
  });
  workerInstance.addEventListener('message', (event: MessageEvent) => {
    const { id, err } = event.data;
    if (!id) return;
    const req = pending.get(id);
    if (!req) return;
    pending.delete(id);
    if (err) {
      // This rejection becomes the build tool's errorText — keep the
      // compiler's stderr so the model can self-correct the OpenSCAD.
      req.reject(errorFromWorker(err));
    } else {
      req.resolve(event.data.data);
    }
  });
  workerInstance.addEventListener('error', (event) => {
    const err = new Error(event.message || 'OpenSCAD worker error');
    pending.forEach((req) => req.reject(err));
    pending.clear();
  });
  return workerInstance;
}

/**
 * Write a blob into the tool worker's WASM filesystem. Uses the same
 * FS_WRITE request/response protocol as `useOpenSCAD` so the blob is staged
 * in the worker's persistent file list and replayed into the fresh OpenSCAD
 * instance every compile.
 */
function writeWorkerFile(
  worker: Worker,
  path: string,
  content: Blob,
): Promise<void> {
  const requestId = `fs-write-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const responsePromise = new Promise<void>((resolve, reject) => {
    pending.set(requestId, {
      resolve: () => resolve(),
      reject,
    });
  });
  return content.arrayBuffer().then((arrayBuffer) => {
    const message: WorkerMessage & { id: string } = {
      id: requestId,
      type: WorkerMessageType.FS_WRITE,
      data: { path, content: arrayBuffer, type: content.type },
    };
    worker.postMessage(message, [arrayBuffer]);
    return responsePromise;
  });
}

// Paths already staged in the tool worker. The worker keeps its file list for
// the process lifetime, so a given file only needs writing once.
const writtenMeshPaths = new Set<string>();

/** Stage every mesh an `import()` in `code` refers to. */
async function prepareMeshFiles(worker: Worker, code: string): Promise<void> {
  const imported = extractImportFilenames(code);
  if (imported.length === 0) return;

  for (const path of imported) {
    if (writtenMeshPaths.has(path)) continue;
    const content = resolveMeshFile(path);
    if (!content) continue;
    // Write under the *requested* path so OpenSCAD's import() resolves the
    // exact string the model wrote (the worker mkdir's parents as needed),
    // even when the model prefixed it with an invented directory.
    await writeWorkerFile(worker, path, content);
    writtenMeshPaths.add(path);
  }
}

export async function previewScadColoredViaToolWorker(
  code: string,
): Promise<{ stl: Blob; off: Blob | undefined }> {
  const worker = getToolWorker();
  const requestId = `tool-preview-${Date.now()}-${Math.random().toString(36).slice(2)}`;

  // Uploaded meshes must be in the worker FS *before* the compile runs,
  // otherwise `import()` fails and the model sees a bogus "file not found"
  // and falls back to a placeholder.
  await prepareMeshFiles(worker, code);

  const responsePromise = new Promise<OpenSCADWorkerResponseData>(
    (resolve, reject) => {
      pending.set(requestId, { resolve, reject });
    },
  );

  const message: WorkerMessage & { id: string } = {
    id: requestId,
    type: WorkerMessageType.PREVIEW,
    data: { code, params: [], fileType: 'stl' },
  };

  worker.postMessage(message);

  const response = await responsePromise;

  if (!response.output) {
    throw new Error('OpenSCAD did not return a preview output');
  }

  const stl = new Blob([new Uint8Array(response.output)], {
    type: 'model/stl',
  });
  const offBytes = response.extraOutputs?.off;
  const off = offBytes
    ? new Blob([new Uint8Array(offBytes)], { type: 'text/plain' })
    : undefined;

  return { stl, off };
}
