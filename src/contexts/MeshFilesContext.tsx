import { createContext, useContext, useCallback } from 'react';
import {
  clearMeshFiles as clearStore,
  getMeshFile as getStoreFile,
  hasMeshFile as hasStoreFile,
  setMeshFile as setStoreFile,
} from '@/worker/meshFileStore';

interface MeshFilesContextType {
  // Store a mesh file by filename
  setMeshFile: (filename: string, content: Blob) => void;
  // Get a mesh file by filename
  getMeshFile: (filename: string) => Blob | undefined;
  // Check if a mesh file exists
  hasMeshFile: (filename: string) => boolean;
  // Clear all mesh files
  clearMeshFiles: () => void;
}

export const MeshFilesContext = createContext<MeshFilesContextType | undefined>(
  undefined,
);

// Backed by the module-level store in `@/worker/meshFileStore` rather than a
// component-local ref, so the non-React OpenSCAD tool worker can read the same
// files when the AI runs `build_parametric_model` on code that import()s an
// uploaded mesh.
export function MeshFilesProvider({ children }: { children: React.ReactNode }) {
  const setMeshFile = useCallback((filename: string, content: Blob) => {
    console.log(`[MeshFiles] Storing: "${filename}" (${content.size} bytes)`);
    setStoreFile(filename, content);
  }, []);

  const getMeshFile = useCallback(
    (filename: string): Blob | undefined => getStoreFile(filename),
    [],
  );

  const hasMeshFile = useCallback(
    (filename: string): boolean => hasStoreFile(filename),
    [],
  );

  const clearMeshFiles = useCallback(() => {
    clearStore();
  }, []);

  return (
    <MeshFilesContext.Provider
      value={{ setMeshFile, getMeshFile, hasMeshFile, clearMeshFiles }}
    >
      {children}
    </MeshFilesContext.Provider>
  );
}

export function useMeshFiles() {
  const context = useContext(MeshFilesContext);
  if (context === undefined) {
    throw new Error('useMeshFiles must be used within a MeshFilesProvider');
  }
  return context;
}
