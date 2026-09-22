/**
 * Extract the file paths referenced by OpenSCAD `import()` calls.
 *
 * The regex only requires a string literal to follow `import(`, so optional
 * arguments such as `convexity = 10` or `layer = "x"` don't defeat the match
 * (the previous `"..."\s*\)` form silently skipped every `import("f.stl",
 * convexity = 10)` — a very common idiom, and the reason uploaded models
 * appeared to "never reach the build environment").
 */
export function extractImportFilenames(code: string): string[] {
  const importRegex = /import\s*\(\s*"([^"]+)"/g;
  const filenames: string[] = [];
  let match: RegExpExecArray | null;
  while ((match = importRegex.exec(code)) !== null) {
    filenames.push(match[1]);
  }
  return filenames;
}

/**
 * True when OpenSCAD code reads an external mesh/geometry file. Used to skip
 * the mesh-write prep entirely for the common self-contained model.
 */
export function hasImports(code: string): boolean {
  return /import\s*\(\s*"/.test(code);
}

/**
 * The last path segment of an `import()` argument. Models frequently emit a
 * web-style path (`/uploads/part.stl`) even though the uploaded blob is keyed
 * by its bare filename (`part.stl`), so lookups fall back to this.
 */
export function importBasename(path: string): string {
  const normalized = path.replace(/\\/g, '/');
  return normalized.split('/').pop() || normalized;
}
