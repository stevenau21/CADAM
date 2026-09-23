import { useCallback, useEffect, useMemo, useState } from 'react';
import { Canvas } from '@react-three/fiber';
import { Bounds, Grid, OrbitControls } from '@react-three/drei';
import { GLTFLoader } from 'three-stdlib';
import * as THREE from 'three';
import { Eye, EyeOff, Maximize2, Layers } from 'lucide-react';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { cn } from '@/lib/utils';

/**
 * Multi-part viewer for B-Rep results — display modes, per-part visibility and
 * isolate, modelled on the panel Ragnar shows in its viewport.
 *
 * Each named component arrives as its own GLB, so parts are independent objects
 * in the scene: showing, hiding or isolating one costs nothing, because there is
 * no recompile involved. The component list is whatever the plan's `parts` map
 * says, and the MODEL authors that map — so the toggles follow the design.
 *
 * Display modes are pure presentation and live entirely here:
 *   W wireframe · S x-ray · T materials · G grid · O orthographic
 */
export type PartMesh = { name: string; glbBase64: string };

/** Distinct, print-friendly tints so components read apart from each other. */
const PALETTE = [
  '#7fb2e5',
  '#e8a87c',
  '#9ed3a7',
  '#c9a0dc',
  '#f0c674',
  '#e58f8f',
  '#8fd4d4',
];

const FLAT_COLOUR = '#b8bcc4';

type DisplayFlags = {
  wireframe: boolean;
  xray: boolean;
  materials: boolean;
  grid: boolean;
  orthographic: boolean;
};

const DEFAULT_DISPLAY: DisplayFlags = {
  wireframe: false,
  xray: false,
  materials: true,
  grid: true,
  orthographic: false,
};

function decoded(glbBase64: string): ArrayBuffer {
  const binary = atob(glbBase64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}

function Part({
  part,
  visible,
  colour,
  display,
}: {
  part: PartMesh;
  visible: boolean;
  colour: string;
  display: DisplayFlags;
}) {
  const [object, setObject] = useState<THREE.Object3D | null>(null);

  useEffect(() => {
    let cancelled = false;
    const loader = new GLTFLoader();
    try {
      loader.parse(
        decoded(part.glbBase64),
        '',
        (gltf) => {
          if (!cancelled) setObject(gltf.scene);
        },
        () => setObject(null),
      );
    } catch {
      setObject(null);
    }
    return () => {
      cancelled = true;
    };
  }, [part.glbBase64]);

  // Display changes only restyle the existing meshes; the GLB is parsed once.
  useEffect(() => {
    if (!object) return;
    const material = new THREE.MeshStandardMaterial({
      color: display.materials ? colour : FLAT_COLOUR,
      metalness: display.materials ? 0.05 : 0,
      roughness: display.materials ? 0.55 : 0.9,
      wireframe: display.wireframe,
      transparent: display.xray,
      opacity: display.xray ? 0.35 : 1,
      depthWrite: !display.xray,
    });
    object.traverse((child) => {
      const mesh = child as THREE.Mesh;
      if (!mesh.isMesh) return;
      mesh.material = material;
      // Transparent solids need two-sided rendering or back faces vanish.
      mesh.material.side = display.xray ? THREE.DoubleSide : THREE.FrontSide;
    });
  }, [object, colour, display]);

  if (!object) return null;
  return <primitive object={object} visible={visible} />;
}

function DisplayMenu({
  display,
  setDisplay,
}: {
  display: DisplayFlags;
  setDisplay: (next: DisplayFlags) => void;
}) {
  const rows: Array<[keyof DisplayFlags, string, string]> = [
    ['wireframe', 'Wireframe', 'W'],
    ['xray', 'X-Ray', 'S'],
    ['materials', 'Materials', 'T'],
    ['grid', 'Grid', 'G'],
    ['orthographic', 'Orthographic', 'O'],
  ];
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className="flex items-center gap-1.5 rounded-md border border-white/10 bg-black/40 px-2.5 py-1.5 text-[11px] text-white backdrop-blur hover:bg-black/60"
        >
          <Layers className="h-3.5 w-3.5" />
          Display
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-52">
        {rows.map(([key, label, hint]) => (
          <DropdownMenuItem
            key={key}
            onClick={() => setDisplay({ ...display, [key]: !display[key] })}
            className="flex items-center gap-2"
          >
            <span className="w-4 text-center text-xs">
              {display[key] ? '✓' : ''}
            </span>
            <span className="flex-1 text-xs">{label}</span>
            <span className="text-[10px] text-gray-500">{hint}</span>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function MultiPartViewer({
  parts,
  visible,
  onVisibleChange,
  showPartsPanel = true,
}: {
  parts: PartMesh[];
  /** Part names currently shown. Empty means show everything. */
  visible: Set<string>;
  onVisibleChange?: (next: Set<string>) => void;
  showPartsPanel?: boolean;
}) {
  const [display, setDisplay] = useState<DisplayFlags>(DEFAULT_DISPLAY);

  // Stable colour per part name, so toggling never reshuffles the palette.
  const colours = useMemo(() => {
    const map = new Map<string, string>();
    parts.forEach((p, i) => map.set(p.name, PALETTE[i % PALETTE.length]));
    return map;
  }, [parts]);

  const shown = useCallback(
    (name: string) => visible.size === 0 || visible.has(name),
    [visible],
  );

  const toggle = (name: string) => {
    const next = new Set(
      visible.size === 0 ? parts.map((p) => p.name) : visible,
    );
    if (next.has(name)) next.delete(name);
    else next.add(name);
    onVisibleChange?.(next);
  };

  /** Show this one, hide the rest — what hidden parts are usually used for. */
  const isolate = (name: string) => onVisibleChange?.(new Set([name]));

  // Keyboard shortcuts, matching the hints in the menu.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.target instanceof HTMLInputElement) return;
      if (event.target instanceof HTMLTextAreaElement) return;
      const key = event.key.toLowerCase();
      const map: Record<string, keyof DisplayFlags> = {
        w: 'wireframe',
        s: 'xray',
        t: 'materials',
        g: 'grid',
        o: 'orthographic',
      };
      const flag = map[key];
      if (!flag) return;
      setDisplay((prev) => ({ ...prev, [flag]: !prev[flag] }));
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const ortho = display.orthographic;

  return (
    <div className="space-y-2">
      <div className="relative h-80 w-full overflow-hidden rounded-md bg-adam-bg-dark">
        <Canvas
          // Remounting on the camera change is the simple, reliable way to swap
          // projection: the parts are small and the scene rebuilds instantly.
          key={ortho ? 'ortho' : 'persp'}
          orthographic={ortho}
          camera={
            ortho
              ? { position: [90, -120, 90], zoom: 2.4 }
              : { position: [90, -120, 90], fov: 40 }
          }
          dpr={[1, 2]}
        >
          <ambientLight intensity={display.materials ? 0.9 : 1.25} />
          <directionalLight position={[120, -160, 200]} intensity={1.6} />
          <directionalLight position={[-140, 100, 80]} intensity={0.5} />
          {display.grid && (
            <Grid
              infiniteGrid
              cellSize={10}
              sectionSize={50}
              cellColor="#3a3a42"
              sectionColor="#4a4a55"
              fadeDistance={600}
              fadeStrength={1.5}
              position={[0, 0, -0.1]}
            />
          )}
          <Bounds fit clip observe margin={1.15}>
            <group>
              {parts.map((part) => (
                <Part
                  key={part.name}
                  part={part}
                  colour={colours.get(part.name) ?? PALETTE[0]}
                  display={display}
                  visible={shown(part.name)}
                />
              ))}
            </group>
          </Bounds>
          <OrbitControls makeDefault enableDamping />
        </Canvas>

        <div className="pointer-events-auto absolute bottom-3 left-3">
          <DisplayMenu display={display} setDisplay={setDisplay} />
        </div>

        {parts.length > 1 && onVisibleChange && (
          <div className="absolute right-3 top-3 flex gap-1.5">
            <button
              type="button"
              onClick={() =>
                onVisibleChange(
                  visible.size === parts.length
                    ? new Set()
                    : new Set(parts.map((p) => p.name)),
                )
              }
              className="rounded-md border border-white/10 bg-black/40 px-2 py-1 text-[11px] text-white backdrop-blur hover:bg-black/60"
            >
              {visible.size === parts.length ? 'Hide all' : 'Show all'}
            </button>
          </div>
        )}
      </div>

      {showPartsPanel && parts.length > 1 && (
        <div className="space-y-1">
          {parts.map((part) => {
            const on = shown(part.name);
            const solo = visible.size === 1 && visible.has(part.name);
            return (
              <div
                key={part.name}
                className={cn(
                  'flex items-center gap-2 rounded-md border px-2 py-1.5 text-xs transition-colors',
                  on
                    ? 'border-adam-neutral-600 bg-adam-bg-dark text-white'
                    : 'border-adam-neutral-700 text-gray-500',
                )}
              >
                <span
                  className="h-3 w-3 shrink-0 rounded-sm"
                  style={{
                    backgroundColor: display.materials
                      ? (colours.get(part.name) ?? PALETTE[0])
                      : FLAT_COLOUR,
                    opacity: on ? 1 : 0.3,
                  }}
                />
                <span className="min-w-0 flex-1 truncate font-mono text-[11px]">
                  {part.name}
                </span>
                <button
                  type="button"
                  onClick={() => isolate(part.name)}
                  title={
                    solo ? 'Show all parts again' : `Show only ${part.name}`
                  }
                  className={cn(
                    'rounded p-1 transition-colors',
                    solo ? 'text-adam-blue' : 'hover:text-white',
                  )}
                >
                  <Maximize2 className="h-3.5 w-3.5" />
                </button>
                <button
                  type="button"
                  onClick={() => toggle(part.name)}
                  title={on ? `Hide ${part.name}` : `Show ${part.name}`}
                  className="rounded p-1 transition-colors hover:text-white"
                >
                  {on ? (
                    <Eye className="h-3.5 w-3.5" />
                  ) : (
                    <EyeOff className="h-3.5 w-3.5" />
                  )}
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
