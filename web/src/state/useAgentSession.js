import { useSyncExternalStore } from 'react';

/** Estado de un `AgentSession` en un componente: re-renderiza al cambiar. El ciclo de vida (abrir y cerrar el WebSocket) NO es
 *  del componente sino del registro de sesiones: un panel oculto o en una pestaña sigue conectado aunque no se dibuje. */
export default function useAgentSession(store) {
  return useSyncExternalStore(store.subscribe, store.getState);
}

/** Cambios de cualquiera de las sesiones del registro (para la cola global de aprobaciones y los avisos de pestañas). */
export function useSessionRegistryVersion(registry) {
  return useSyncExternalStore(registry.subscribe, registry.getVersion);
}
