// Almacén en memoria de las pestañas del visor, una por workspace (FE-010, nota N-002).
//
// Vive FUERA de los componentes: al cambiar de vista (el visor se desmonta) o de workspace, las pestañas y el texto sin guardar
// siguen aquí, y las cargas y guardados que estaban en vuelo terminan igualmente. Solo las RUTAS se recuerdan en `sessionStorage`
// (vía `explorerStore`); el contenido de los archivos jamás se escribe en el navegador. Tras una recarga se reabren las pestañas
// leyendo los archivos de nuevo, y si había texto sin guardar el navegador avisa antes de perderlo (`beforeunload`).
//
// Las dependencias (leer, guardar, persistir) se inyectan para poder probarlo sin servidor ni navegador.

import { closeTab, dirtyPaths, emptyViewer, isDirty, openTab, patchTab, persistable, removePath, renamePath, restore, setActive } from './viewerState.js';

const NOT_FOUND = 404;

export function createViewerStore({ read, write, load = () => emptyViewer(), persist = () => {}, guardUnload = false }) {
  const states = new Map();              // workspace -> estado del visor
  const lastSaved = new Map();           // workspace -> JSON de lo último persistido (evita escribir sin cambios)
  const inflight = new Set();            // cargas en curso («workspace\0ruta»)
  const listeners = new Set();
  const EMPTY = emptyViewer();

  const emit = () => listeners.forEach((l) => l());
  const get = (ws) => states.get(ws) ?? EMPTY;

  const update = (ws, fn) => {
    const prev = get(ws);
    const next = fn(prev);
    if (next === prev) return;
    states.set(ws, next);
    const saved = JSON.stringify(persistable(next));
    if (ws && lastSaved.get(ws) !== saved) {
      lastSaved.set(ws, saved);
      persist(ws, persistable(next));
    }
    emit();
  };

  /** Crea el estado del workspace a partir de lo recordado, sin avisar a nadie (se puede llamar al renderizar). */
  const ensure = (ws) => {
    if (!states.has(ws)) {
      const initial = restore(ws ? load(ws) : null);
      states.set(ws, initial);
      lastSaved.set(ws, JSON.stringify(persistable(initial)));
    }
    return get(ws);
  };

  const info = (data) => ({
    kind: data.kind, content: data.content ?? '', original: data.content ?? '', etag: data.etag, encoding: data.encoding || 'utf-8',
    mime: data.mime, size: data.size, error: null, conflict: false,
  });

  /** Lee el archivo de una pestaña. `quiet`: si ya no existe, la pestaña se descarta sin error (reapertura tras recargar). */
  const loadTab = async (ws, path, quiet) => {
    const key = `${ws}\0${path}`;
    if (inflight.has(key)) return;
    inflight.add(key);
    try {
      const data = await read(path);
      update(ws, (s) => patchTab(s, path, info(data)));
    } catch (err) {
      if (quiet && err?.status === NOT_FOUND) update(ws, (s) => closeTab(s, path));
      else update(ws, (s) => patchTab(s, path, { kind: 'error', error: err?.message || String(err) }));
    } finally {
      inflight.delete(key);
    }
  };

  /** Una pestaña sin cambios pudo cambiar en disco mientras no se miraba (un agente, otro programa): se relee en silencio. */
  const refreshTab = async (ws, path) => {
    const key = `${ws}\0${path}`;
    if (inflight.has(key)) return;
    inflight.add(key);
    try {
      const data = await read(path);
      update(ws, (s) => {
        const tab = s.tabs.find((t) => t.path === path);
        if (!tab || isDirty(tab) || tab.etag === data.etag) return s;     // lo editado nunca se pisa
        return patchTab(s, path, info(data));
      });
    } catch (err) {
      if (err?.status !== NOT_FOUND) return;                              // sin conexión: se conserva lo que había
      update(ws, (s) => {
        const tab = s.tabs.find((t) => t.path === path);
        if (!tab) return s;
        return isDirty(tab) ? patchTab(s, path, { error: 'El archivo ya no existe en disco. Tus cambios siguen aquí.' }) : closeTab(s, path);
      });
    } finally {
      inflight.delete(key);
    }
  };

  const store = {
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    snapshot: get,
    ensure,

    /** Al mostrar el visor: reabre lo que faltaba por cargar y comprueba el resto (lo modificado nunca se pisa). */
    activate(ws) {
      ensure(ws);
      for (const tab of get(ws).tabs) {
        if (tab.kind === 'loading' || tab.kind === 'error') loadTab(ws, tab.path, tab.kind === 'loading');
        else if (tab.kind === 'text') refreshTab(ws, tab.path);
      }
    },

    open(ws, path) {
      ensure(ws);
      if (get(ws).tabs.some((t) => t.path === path)) {
        update(ws, (s) => setActive(s, path));
        return;
      }
      update(ws, (s) => openTab(s, path));
      loadTab(ws, path, false);
    },

    select: (ws, path) => update(ws, (s) => setActive(s, path)),
    close: (ws, path) => update(ws, (s) => closeTab(s, path)),
    patch: (ws, path, changes) => update(ws, (s) => patchTab(s, path, changes)),

    async reload(ws, path) {
      try {
        const data = await read(path);
        update(ws, (s) => patchTab(s, path, info(data)));
      } catch (err) {
        update(ws, (s) => patchTab(s, path, { error: err?.message || String(err) }));
      }
    },

    /** Guarda la pestaña activa. `force` ignora el etag (sobrescribir tras un conflicto). */
    async save(ws, force = false) {
      const s = get(ws);
      const tab = s.tabs.find((t) => t.path === s.active);
      if (!tab || !isDirty(tab)) return;
      const sent = tab.content;
      try {
        const res = await write(tab.path, sent, force ? null : tab.etag, tab.encoding);
        // Si mientras se guardaba se siguió escribiendo, lo nuevo sigue pendiente: solo se marca guardado lo que se envió.
        update(ws, (cur) => patchTab(cur, tab.path, { original: sent, etag: res.etag, encoding: res.encoding || tab.encoding, conflict: false, error: null }));
      } catch (err) {
        if (err?.status === 409) update(ws, (cur) => patchTab(cur, tab.path, { conflict: true, error: null }));
        else update(ws, (cur) => patchTab(cur, tab.path, { error: err?.message || String(err) }));
      }
    },

    /** Una entrada del explorador se eliminó o cambió de nombre: las pestañas siguen a la ruta nueva o se cierran. */
    entryChanged(ws, detail) {
      const { action, path, newPath } = detail || {};
      if (!path) return;
      if (action === 'delete') update(ws, (s) => removePath(s, path));
      else if (action === 'rename' && newPath) update(ws, (s) => renamePath(s, path, newPath));
    },

    /** Archivos con cambios sin guardar en cualquier workspace: `[{ workspace, path }]`. */
    unsaved() {
      return [...states.entries()].flatMap(([workspace, s]) => dirtyPaths(s).map((path) => ({ workspace, path })));
    },
  };

  if (guardUnload && typeof window !== 'undefined') {
    // Recargar o cerrar la ventana con texto sin guardar: el navegador pide confirmación en lugar de perderlo en silencio.
    window.addEventListener('beforeunload', (e) => {
      if (store.unsaved().length === 0) return;
      e.preventDefault();
      e.returnValue = '';
    });
  }
  return store;
}
