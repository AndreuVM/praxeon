// Estado de las pestañas del visor de archivos (FE-010, nota N-002). Funciones puras: cada una recibe un estado
// `{ tabs, active }` y devuelve el nuevo (o el MISMO objeto si nada cambia, para que React no repinte).
//
// Una pestaña es `{ path, kind, content, original, etag, encoding, error, conflict, mode, mime, size }`; `content !== original`
// significa «con cambios sin guardar». Lo que se recuerda entre recargas son solo las rutas (`persistable`), nunca el texto.

import { MAX_VIEWER_TABS, emptyViewer, isInside, isRelPath, moveRel } from './explorerState.js';

export { emptyViewer };

const extOf = (p) => (p.split('.').pop() || '').toLowerCase();
export const isSvg = (p) => extOf(p) === 'svg';
export const baseName = (p) => p.split('/').pop();

export const loadingTab = (path) => ({
  path, kind: 'loading', content: '', original: '', etag: null, encoding: 'utf-8', error: null, conflict: false, mode: isSvg(path) ? 'preview' : 'edit',
});

export const isDirty = (tab) => tab.kind === 'text' && tab.content !== tab.original;
export const dirtyPaths = (state) => state.tabs.filter(isDirty).map((t) => t.path);

const lastPath = (tabs) => (tabs.length ? tabs[tabs.length - 1].path : null);

/** Abre una pestaña (en carga) o, si ya existe, solo la activa. */
export function openTab(state, path) {
  if (state.tabs.some((t) => t.path === path)) return state.active === path ? state : { ...state, active: path };
  return { tabs: [...state.tabs, loadingTab(path)], active: path };
}

export function setActive(state, path) {
  if (state.active === path || !state.tabs.some((t) => t.path === path)) return state;
  return { ...state, active: path };
}

/** Cambia campos de una pestaña. Si ya no existe (se cerró mientras cargaba) no hace nada: no la resucita. */
export function patchTab(state, path, changes) {
  const tab = state.tabs.find((t) => t.path === path);
  if (!tab || Object.keys(changes).every((k) => Object.is(tab[k], changes[k]))) return state;
  return { ...state, tabs: state.tabs.map((t) => (t === tab ? { ...t, ...changes } : t)) };
}

export function closeTab(state, path) {
  if (!state.tabs.some((t) => t.path === path)) return state;
  const tabs = state.tabs.filter((t) => t.path !== path);
  return { tabs, active: state.active === path ? lastPath(tabs) : state.active };
}

/** Un archivo o carpeta se ha eliminado: se cierran las pestañas que cuelgan de ella. */
export function removePath(state, path) {
  const tabs = state.tabs.filter((t) => !isInside(t.path, path));
  if (tabs.length === state.tabs.length) return state;
  return { tabs, active: state.active && isInside(state.active, path) ? lastPath(tabs) : state.active };
}

/** Un archivo o carpeta ha cambiado de ruta: sus pestañas la siguen (con su texto, esté guardado o no). */
export function renamePath(state, from, to) {
  if (from === to || !state.tabs.some((t) => isInside(t.path, from))) return state;
  return {
    tabs: state.tabs.map((t) => (isInside(t.path, from) ? { ...t, path: moveRel(t.path, from, to) } : t)),
    active: state.active ? moveRel(state.active, from, to) : null,
  };
}

/** Lo que se guarda en `sessionStorage`: rutas y la pestaña activa. Ni una letra del contenido. */
export function persistable(state) {
  const tabs = state.tabs.filter((t) => t.kind !== 'error').map((t) => t.path).slice(-MAX_VIEWER_TABS);
  return { tabs, active: tabs.includes(state.active) ? state.active : null };
}

/** Estado inicial a partir de lo recordado: pestañas «en carga» que el almacén rellena leyendo cada archivo. */
export function restore(saved) {
  const seen = new Set();
  const paths = [];
  for (const p of Array.isArray(saved?.tabs) ? saved.tabs : []) {
    if (isRelPath(p) && !seen.has(p)) {
      seen.add(p);
      paths.push(p);
    }
  }
  const tabs = paths.slice(-MAX_VIEWER_TABS).map(loadingTab);
  return { tabs, active: tabs.some((t) => t.path === saved?.active) ? saved.active : lastPath(tabs) };
}
