import { createViewerStore } from '../../utils/viewerStore';
import { explorerStore } from '../../utils/explorerState';
import { readWorkspaceFile, saveWorkspaceFile } from '../../services/workspaceFiles';

/**
 * El almacén real del visor (FE-010): lee y guarda por la API del workspace, recuerda las RUTAS de las pestañas en el mismo
 * almacén por workspace que el explorador (FE-004) y avisa antes de recargar si hay texto sin guardar.
 */
export const viewerStore = createViewerStore({
  read: readWorkspaceFile,
  write: saveWorkspaceFile,
  load: (ws) => explorerStore.load(ws).viewer,
  persist: (ws, viewer) => explorerStore.save(ws, (s) => ({ ...s, viewer })),
  guardUnload: true,
});
