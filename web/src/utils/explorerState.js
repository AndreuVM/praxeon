// Estado del explorador de archivos por workspace (FE-004, nota N-002).
//
// Guarda solo rutas relativas y números, NUNCA contenido de archivos. Vive en `sessionStorage`: sobrevive a recargas y es propio de
// cada ventana del navegador. El explorador es del workspace, no del panel: no depende de qué panel tiene el foco.
//
// Todo lo que decide algo son funciones puras sobre un estado `{ open, currentDir, selected, scroll }`; el almacén solo lee y
// escribe. Así se prueban sin navegador.

export const STORAGE_KEY = 'praxeon:explorer:v1';
export const MAX_OPEN = 200;          // carpetas abiertas que se recuerdan por workspace (las más recientes)
export const MAX_WORKSPACES = 20;     // workspaces recordados (los más recientes)
const MAX_PATH = 1024;
const MAX_SCROLL = 10_000_000;

export const emptyState = () => ({ open: [], currentDir: '', selected: null, scroll: 0 });

/** Ruta relativa válida de un workspace, con «/» como separador: sin «..», sin segmentos vacíos, sin raíz ni unidad de disco. */
export function isRelPath(p) {
  if (typeof p !== 'string' || p.length === 0 || p.length > MAX_PATH) return false;
  if (p.startsWith('/') || p.includes('\\') || p.includes('\0') || /^[A-Za-z]:/.test(p)) return false;
  return p.split('/').every((seg) => seg !== '' && seg !== '.' && seg !== '..');
}

/** Normaliza lo leído del almacenamiento (puede venir manipulado o de una versión anterior) a un estado válido. */
export function sanitize(raw) {
  const out = emptyState();
  if (!raw || typeof raw !== 'object') return out;
  if (Array.isArray(raw.open)) {
    const seen = new Set();
    for (const p of raw.open) {
      if (isRelPath(p) && !seen.has(p)) {
        seen.add(p);
        out.open.push(p);
      }
    }
    out.open = out.open.slice(-MAX_OPEN);
  }
  if (isRelPath(raw.currentDir)) out.currentDir = raw.currentDir;
  if (isRelPath(raw.selected)) out.selected = raw.selected;
  if (typeof raw.scroll === 'number' && Number.isFinite(raw.scroll) && raw.scroll > 0) out.scroll = Math.min(Math.round(raw.scroll), MAX_SCROLL);
  return out;
}

/** `path` es `dir` o cuelga de ella. No confunde «ab» con «a»: compara por segmentos. */
export const isInside = (path, dir) => path === dir || path.startsWith(`${dir}/`);

/** Nueva ruta de `path` cuando `from` pasa a llamarse `to` (si `path` no cuelga de `from`, no cambia). */
export const moveRel = (path, from, to) => (isInside(path, from) ? to + path.slice(from.length) : path);

/** Carpetas que contienen a `rel`, de la raíz hacia dentro: «a/b/c» → ["a", "a/b"]. */
export function ancestors(rel) {
  if (!isRelPath(rel)) return [];
  const parts = rel.split('/');
  return parts.slice(0, -1).map((_, i) => parts.slice(0, i + 1).join('/'));
}

/** Abre o cierra una carpeta. Las abiertas más recientes van al final; al pasar de MAX_OPEN se olvidan las más antiguas. */
export function setOpen(state, rel, open) {
  if (!isRelPath(rel)) return state;
  const rest = state.open.filter((p) => p !== rel);
  if (!open) return rest.length === state.open.length ? state : { ...state, open: rest };
  return { ...state, open: [...rest, rel].slice(-MAX_OPEN) };
}

/** Quita `rel` (archivo o carpeta) y todo lo que cuelga de ella; si la carpeta actual o la selección estaban dentro, se limpian. */
export function removed(state, rel) {
  if (!isRelPath(rel)) return state;
  return {
    ...state,
    open: state.open.filter((p) => !isInside(p, rel)),
    currentDir: isInside(state.currentDir, rel) ? '' : state.currentDir,
    selected: state.selected && isInside(state.selected, rel) ? null : state.selected,
  };
}

/** Un archivo o carpeta cambia de ruta (renombrar o mover): lo recordado dentro de ella la sigue. */
export function renamed(state, from, to) {
  if (!isRelPath(from) || !isRelPath(to) || from === to) return state;
  const move = (p) => moveRel(p, from, to);
  return { ...state, open: state.open.map(move), currentDir: state.currentDir ? move(state.currentDir) : '', selected: state.selected ? move(state.selected) : null };
}

/** Abre las carpetas que contienen `rel`, para que sea visible (por ejemplo, el archivo seleccionado al volver). */
export function reveal(state, rel) {
  return ancestors(rel).reduce((s, dir) => (s.open.includes(dir) ? s : setOpen(s, dir, true)), state);
}

const defaultStorage = () => {
  try {
    return typeof window !== 'undefined' ? window.sessionStorage : null;
  } catch {
    return null;                       // acceso denegado (modo privado, iframe sin permisos)
  }
};

/**
 * Almacén por workspace. `storage` se inyecta para poder probarlo; sin almacenamiento todo funciona en memoria vacía.
 * Un fallo al leer (JSON corrupto) o al escribir (cuota) se ignora: perder el estado nunca debe romper el explorador.
 */
export function createExplorerStore(storage = defaultStorage()) {
  const read = () => {
    try {
      const parsed = JSON.parse(storage?.getItem(STORAGE_KEY) || 'null');
      if (parsed && typeof parsed === 'object' && parsed.states && typeof parsed.states === 'object' && Array.isArray(parsed.order)) return parsed;
    } catch { /* corrupto: se empieza de cero */ }
    return { order: [], states: {} };
  };
  const write = (data) => {
    try {
      storage?.setItem(STORAGE_KEY, JSON.stringify(data));
    } catch { /* cuota llena o almacenamiento no disponible */ }
  };
  return {
    load(workspace) {
      if (!workspace || typeof workspace !== 'string') return emptyState();
      return sanitize(read().states[workspace]);
    },
    /** `update` recibe el estado actual y devuelve el nuevo. Devuelve el estado guardado (ya normalizado). */
    save(workspace, update) {
      if (!workspace || typeof workspace !== 'string') return emptyState();
      const data = read();
      const next = sanitize(update(sanitize(data.states[workspace])));
      data.states[workspace] = next;
      data.order = [...data.order.filter((w) => w !== workspace), workspace];
      while (data.order.length > MAX_WORKSPACES) delete data.states[data.order.shift()];
      write(data);
      return next;
    },
    clear(workspace) {
      const data = read();
      delete data.states[workspace];
      data.order = data.order.filter((w) => w !== workspace);
      write(data);
    },
  };
}

export const explorerStore = createExplorerStore();
