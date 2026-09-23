import {
  Download,
  Ruler,
  TriangleAlert,
  CircleCheck,
  Eye,
  EyeOff,
} from 'lucide-react';
import { useState } from 'react';
import { Button } from '@/components/ui/button';
import {
  MultiPartViewer,
  type PartMesh,
} from '@/components/viewer/MultiPartViewer';
import { cn } from '@/lib/utils';

/**
 * Renders the result of a `build_brep_model` tool call.
 *
 * The B-Rep engine returns everything in one payload — an exact solid, a render,
 * the measured stats, the fit checks it verified, and STEP/STL bytes — so this
 * card is where the user finally sees the two things CADAM never had: a real
 * STEP file, and proof that the parts fit.
 */
export type BrepOutput = {
  status?: 'success' | 'error' | 'invalid';
  message?: string;
  stats?: {
    bbox?: number[];
    solids?: number | null;
    faces?: number | null;
    volume?: number | null;
    is_valid?: boolean | null;
  } | null;
  checks?: Array<{
    kind: string;
    a: string;
    b: string;
    value: number;
    expect?: number;
    pass?: boolean;
  }> | null;
  stepBase64?: string;
  stlBase64?: string;
  renderDataUrl?: string;
  partNames?: string[];
  /** One entry per named component when the plan declares a `parts` map. */
  parts?: PartMesh[];
};

function downloadBase64(base64: string, filename: string, mime: string) {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  const url = URL.createObjectURL(new Blob([bytes], { type: mime }));
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

const slug = (name: string) =>
  name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '') || 'model';

export function BrepBuildCard({
  title,
  output,
}: {
  title?: string;
  output: BrepOutput;
}) {
  const stats = output.stats ?? {};
  const bbox = stats.bbox;
  const checks = output.checks ?? [];
  const failed = checks.filter((c) => c.pass === false);
  const ok = output.status === 'success' && !failed.length;
  const base = slug(title ?? 'model');

  // Parts start HIDDEN. A generated model is usually one opaque lump, so the
  // useful first look is of the individual components with nothing overlapping —
  // you can then switch them on to see how they assemble.
  const parts = output.parts ?? [];
  const [visible, setVisible] = useState<Set<string>>(
    () => new Set(parts.length > 1 ? [] : parts.map((p) => p.name)),
  );
  const toggle = (name: string) =>
    setVisible((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });

  return (
    <div className="overflow-hidden rounded-lg border border-adam-neutral-700 bg-adam-bg-secondary-dark">
      <div className="flex items-center gap-2 border-b border-adam-neutral-700 px-3 py-2">
        {ok ? (
          <CircleCheck className="h-4 w-4 shrink-0 text-emerald-400" />
        ) : (
          <TriangleAlert className="h-4 w-4 shrink-0 text-amber-400" />
        )}
        <span className="truncate text-sm font-medium text-white">
          {title ?? 'B-Rep model'}
        </span>
        <span className="border-adam-neutral-600 ml-auto shrink-0 rounded-full border px-2 py-0.5 text-[10px] uppercase tracking-wide text-gray-400">
          exact B-Rep
        </span>
      </div>

      {parts.length > 0 ? (
        <div className="space-y-2 p-3 pb-0">
          <MultiPartViewer parts={parts} visible={visible} />
          {parts.length > 1 && (
            <div className="flex flex-wrap gap-1.5">
              {parts.map((part) => {
                const on = visible.has(part.name);
                return (
                  <button
                    key={part.name}
                    type="button"
                    onClick={() => toggle(part.name)}
                    aria-pressed={on}
                    className={cn(
                      'flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] transition-colors',
                      on
                        ? 'border-adam-blue/60 bg-adam-blue/15 text-white'
                        : 'border-gray-600/60 text-gray-400 hover:text-white',
                    )}
                    title={on ? `Hide ${part.name}` : `Show ${part.name}`}
                  >
                    {on ? (
                      <Eye className="h-3 w-3" />
                    ) : (
                      <EyeOff className="h-3 w-3" />
                    )}
                    {part.name}
                  </button>
                );
              })}
              <button
                type="button"
                onClick={() =>
                  setVisible(
                    visible.size === parts.length
                      ? new Set()
                      : new Set(parts.map((p) => p.name)),
                  )
                }
                className="rounded-full border border-gray-600/60 px-2.5 py-1 text-[11px] text-gray-400 hover:text-white"
              >
                {visible.size === parts.length ? 'Hide all' : 'Show all'}
              </button>
            </div>
          )}
        </div>
      ) : (
        output.renderDataUrl && (
          <img
            src={output.renderDataUrl}
            alt={title ?? 'Compiled model'}
            className="max-h-80 w-full bg-white object-contain"
          />
        )
      )}

      <div className="space-y-3 p-3">
        {bbox && (
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-300">
            <span className="flex items-center gap-1">
              <Ruler className="h-3 w-3" />
              {bbox.map((v) => v.toFixed(2)).join(' × ')} mm
            </span>
            {stats.solids != null && <span>{stats.solids} solid(s)</span>}
            {stats.faces != null && <span>{stats.faces} faces</span>}
            {stats.volume != null && <span>{stats.volume.toFixed(0)} mm³</span>}
            {stats.is_valid === false && (
              <span className="text-red-400">invalid solid</span>
            )}
          </div>
        )}

        {checks.length > 0 && (
          <div className="space-y-1">
            {checks.map((c, i) => (
              <div
                key={`${c.kind}-${c.a}-${c.b}-${i}`}
                className={cn(
                  'flex items-center gap-2 font-mono text-[11px]',
                  c.pass === false ? 'text-red-400' : 'text-gray-400',
                )}
              >
                <span>{c.kind === 'gap' ? '⇔' : '⊘'}</span>
                <span className="truncate">
                  {c.a} / {c.b}
                </span>
                <span className="ml-auto shrink-0">
                  {c.value.toFixed(4)}
                  {c.expect !== undefined &&
                    ` (want ${c.expect}) ${c.pass ? 'PASS' : 'FAIL'}`}
                </span>
              </div>
            ))}
          </div>
        )}

        {failed.length > 0 && (
          <p className="text-xs text-amber-400">
            {failed.length} declared fit check
            {failed.length === 1 ? '' : 's'} failed — the parts do not fit as
            specified.
          </p>
        )}

        {!ok && output.message && (
          <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded bg-adam-bg-dark p-2 text-[11px] text-gray-400">
            {output.message}
          </pre>
        )}

        {(output.stepBase64 || output.stlBase64) && (
          <div className="flex flex-wrap gap-2 pt-1">
            {output.stepBase64 && (
              <Button
                size="sm"
                variant="outline"
                className="h-8 gap-1.5 text-xs"
                onClick={() =>
                  downloadBase64(
                    output.stepBase64 as string,
                    `${base}.step`,
                    'application/step',
                  )
                }
              >
                <Download className="h-3.5 w-3.5" />
                STEP
              </Button>
            )}
            {output.stlBase64 && (
              <Button
                size="sm"
                variant="outline"
                className="h-8 gap-1.5 text-xs"
                onClick={() =>
                  downloadBase64(
                    output.stlBase64 as string,
                    `${base}.stl`,
                    'model/stl',
                  )
                }
              >
                <Download className="h-3.5 w-3.5" />
                STL
              </Button>
            )}
            {output.partNames?.length ? (
              <span className="self-center text-[11px] text-gray-500">
                parts exported: {output.partNames.join(', ')}
              </span>
            ) : null}
          </div>
        )}
      </div>
    </div>
  );
}
