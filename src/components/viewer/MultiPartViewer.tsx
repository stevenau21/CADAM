import { useEffect, useMemo, useState } from 'react';
import { Canvas } from '@react-three/fiber';
import { Bounds, OrbitControls } from '@react-three/drei';
import { GLTFLoader } from 'three-stdlib';
import * as THREE from 'three';

/**
 * Multi-part viewer for B-Rep results.
 *
 * Each named component arrives as its own GLB, so they can be shown and hidden
 * INDEPENDENTLY — the thing a single rendered PNG can never do. The part list is
 * whatever the plan's `parts` map says, which the model authors: add a component
 * and a new toggle appears, merge two and one disappears. Nothing here is
 * hardcoded per part.
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

function decoded(glbBase64: string): ArrayBuffer {
  const binary = atob(glbBase64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}

function Part({
  part,
  visible,
  color,
}: {
  part: PartMesh;
  visible: boolean;
  color: string;
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
          if (cancelled) return;
          // Recolour so neighbouring components are distinguishable, and make
          // the surface read as a solid rather than a flat silhouette.
          gltf.scene.traverse((child) => {
            const mesh = child as THREE.Mesh;
            if (!mesh.isMesh) return;
            mesh.material = new THREE.MeshStandardMaterial({
              color,
              metalness: 0.05,
              roughness: 0.55,
            });
          });
          setObject(gltf.scene);
        },
        () => undefined,
      );
    } catch {
      setObject(null);
    }
    return () => {
      cancelled = true;
    };
  }, [part.glbBase64, color]);

  if (!object) return null;
  return <primitive object={object} visible={visible} />;
}

export function MultiPartViewer({
  parts,
  visible,
}: {
  parts: PartMesh[];
  /** Part names currently shown. Empty means show everything. */
  visible: Set<string>;
}) {
  // A stable colour per part name, so toggling never reshuffles the palette.
  const colours = useMemo(() => {
    const map = new Map<string, string>();
    parts.forEach((p, i) => map.set(p.name, PALETTE[i % PALETTE.length]));
    return map;
  }, [parts]);

  const anyVisible =
    visible.size === 0 || parts.some((p) => visible.has(p.name));

  return (
    <div className="h-80 w-full overflow-hidden rounded-md bg-adam-bg-dark">
      <Canvas camera={{ position: [90, -120, 90], fov: 40 }} dpr={[1, 2]}>
        <ambientLight intensity={0.9} />
        <directionalLight position={[120, -160, 200]} intensity={1.6} />
        <directionalLight position={[-140, 100, 80]} intensity={0.5} />
        <Bounds fit clip observe margin={1.15}>
          <group>
            {parts.map((part) => (
              <Part
                key={part.name}
                part={part}
                color={colours.get(part.name) ?? '#7fb2e5'}
                visible={visible.size === 0 || visible.has(part.name)}
              />
            ))}
          </group>
        </Bounds>
        <OrbitControls makeDefault enableDamping />
      </Canvas>
      {!anyVisible && (
        <p className="pointer-events-none -mt-44 text-center text-xs text-gray-500">
          Every component is hidden — tick one on.
        </p>
      )}
    </div>
  );
}
