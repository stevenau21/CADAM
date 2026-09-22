/**
 * Module-level registry of uploaded mesh blobs.
 *
 * The React `MeshFilesProvider` owns the UI-facing API, but the OpenSCAD
 * **tool-execution** worker runs outside the component tree (it's a module
 * singleton so in-flight AI builds survive navigation), so it cannot read
 * React context. Both paths read and write this one store, which keeps the
 * viewer preview and the AI build in sync: a file attached for one is
 * available to the other.
 *
 * Keys are bare filenames (`part.stl`). Lookups should go through
 * {@link resolveMeshFile}, which also matches the basename of a path so a
 * model emitting `import("/uploads/part.stl")` still finds `part.stl`.
 */
import { importBasename } from '@/utils/scadImports';

const meshFiles = new Map<string, Blob>();

export function setMeshFile(filename: string, content: Blob): void {
  meshFiles.set(filename, content);
}

export function getMeshFile(filename: string): Blob | undefined {
  return meshFiles.get(filename);
}

/**
 * Exact-match, then basename fallback. Returns the matched blob for a path
 * the model wrote, regardless of any directory prefix it invented.
 */
export function resolveMeshFile(path: string): Blob | undefined {
  return meshFiles.get(path) ?? meshFiles.get(importBasename(path));
}

export function hasMeshFile(filename: string): boolean {
  return meshFiles.has(filename);
}

export function clearMeshFiles(): void {
  meshFiles.clear();
}
