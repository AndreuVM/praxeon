import { useCallback, useEffect, useState } from 'react';
import * as api from '../../services/api';

// Workspace por defecto del proyecto, sincronizado entre todos los componentes que lo muestran.
export default function useWorkspace() {
  const [workspace, setWorkspace] = useState(null);

  const refresh = useCallback(async () => {
    try {
      setWorkspace(await api.fetchWorkspace());
    } catch {
      /* sin conexión: se conserva el último valor conocido */
    }
  }, []);

  useEffect(() => {
    refresh();
    const onChange = (e) => (e.detail ? setWorkspace(e.detail) : refresh());
    window.addEventListener('praxeon:workspace-changed', onChange);
    return () => window.removeEventListener('praxeon:workspace-changed', onChange);
  }, [refresh]);

  const choose = useCallback((path) => api.selectWorkspace(path), []);
  const forget = useCallback((path) => api.forgetWorkspace(path), []);

  return { workspace, refresh, choose, forget };
}

export const SOURCE_LABELS = {
  selected: 'Elegido por ti',
  env: 'Variable de entorno',
  auto: 'Detectado automáticamente',
};
