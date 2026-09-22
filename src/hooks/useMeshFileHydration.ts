import { useEffect, useState } from 'react';
import { supabase } from '@/lib/supabase';
import { hasMeshFile, setMeshFile } from '@/worker/meshFileStore';
import type { AppUIMessage } from '@shared/chatAi';
import type { Conversation } from '@shared/types';

type MeshContextPart = Extract<
  AppUIMessage['parts'][number],
  { type: 'data-mesh-context' }
>;

/**
 * Re-download the STL attachments a conversation references into the shared
 * mesh file store, and return a version that bumps each time new bytes land.
 *
 * The store is module-level and in-memory, so a page reload (or a dev-server
 * restart) empties it. Any `import("part.stl")` in an existing artifact then
 * fails with `Can't open import file '...'` — the file the model was told
 * about simply isn't there anymore. The bytes are still in the private
 * `meshes` bucket at `<user>/<conversation>/<meshId>.<ext>`, so re-fetch them
 * lazily whenever a message references a mesh we don't already hold.
 *
 * The returned version must be threaded into the preview's recompile deps:
 * the first compile on mount races this download and fails, so the compile
 * has to run again once the bytes arrive.
 */
export function useMeshFileHydration(
  conversation: Pick<Conversation, 'id' | 'user_id'>,
  messages: AppUIMessage[],
): number {
  const [version, setVersion] = useState(0);

  useEffect(() => {
    const meshParts = messages.flatMap((message) =>
      message.parts.filter(
        (part): part is MeshContextPart => part.type === 'data-mesh-context',
      ),
    );
    const missing = meshParts.filter(
      (part) => part.data.filename && !hasMeshFile(part.data.filename),
    );
    if (missing.length === 0) return;

    let cancelled = false;
    void (async () => {
      let added = 0;
      for (const part of missing) {
        const { meshId, fileType, filename } = part.data;
        if (!filename) continue;
        const path = `${conversation.user_id}/${conversation.id}/${meshId}.${fileType}`;
        const { data, error } = await supabase.storage
          .from('meshes')
          .download(path);
        if (cancelled) return;
        if (error || !data) {
          console.warn(
            `[MeshFiles] Could not rehydrate "${filename}" from ${path}`,
            error,
          );
          continue;
        }
        setMeshFile(filename, data);
        added += 1;
        console.log(
          `[MeshFiles] Rehydrated "${filename}" (${data.size} bytes)`,
        );
      }
      if (!cancelled && added > 0) setVersion((v) => v + 1);
    })();

    return () => {
      cancelled = true;
    };
  }, [conversation.id, conversation.user_id, messages]);

  return version;
}
