import { useEffect, useSyncExternalStore } from 'react';

/**
 * Conecta un `AgentSession` a un componente: devuelve su estado y re-renderiza al cambiar. Al montarse abre su WebSocket y al
 * desmontarse lo cierra (con StrictMode el efecto se ejecuta dos veces: `activate` deja el almacén listo de nuevo).
 */
export default function useAgentSession(store) {
  useEffect(() => {
    store.activate();
    return () => store.dispose();
  }, [store]);
  return useSyncExternalStore(store.subscribe, store.getState);
}
