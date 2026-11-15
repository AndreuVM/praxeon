// Disposición de los paneles de agentes (FE-008, nota N-001). Todo son funciones puras sobre
//   { order: ['main', 'p2', …], focusedId, maximizedId }
// más un almacén por workspace en `localStorage`. Un panel aloja una sesión de agente; todos trabajan sobre el workspace actual.

export const MAIN_ID = 'main';
export const MAX_PANELS = 8;            // abiertos a la vez (el límite de misiones en marcha lo pone el servidor)
export const MAX_VISIBLE = 4;           // visibles a la vez (D9): 1, 2 en columnas o 2×2; el resto, en pestañas
export const STORAGE_KEY = 'praxeon:panels:v1';
const MAX_WORKSPACES = 20;

/** «Agente N»: el panel principal es el 1 y «pN» es el N. */
export const panelLabel = (id) => `Agente ${id === MAIN_ID ? 1 : Number(id.slice(1))}`;

export const initialLayout = () => ({ order: [MAIN_ID], focusedId: MAIN_ID, maximizedId: null });

const has = (layout, id) => layout.order.includes(id);

/** Siguiente id libre: «p2», «p3»… (el principal siempre es «main»). */
export function nextPanelId(order) {
  for (let n = 2; n <= MAX_PANELS + 1; n += 1) if (!order.includes(`p${n}`)) return `p${n}`;
  return null;
}

/** Abre un panel nuevo y lo enfoca. Devuelve `{ layout, id }`, o `null` si ya hay MAX_PANELS. */
export function addPanel(layout) {
  if (layout.order.length >= MAX_PANELS) return null;
  const id = nextPanelId(layout.order);
  return { layout: { ...layout, order: [...layout.order, id], focusedId: id, maximizedId: null }, id };
}

/** Cierra un panel. El último no se cierra. Si era el enfocado, el foco pasa al anterior (o al siguiente si era el primero). */
export function closePanel(layout, id) {
  if (!has(layout, id) || layout.order.length <= 1) return layout;
  const index = layout.order.indexOf(id);
  const order = layout.order.filter((p) => p !== id);
  const focusedId = layout.focusedId === id ? order[Math.max(0, index - 1)] : layout.focusedId;
  return { order, focusedId, maximizedId: layout.maximizedId === id ? null : layout.maximizedId };
}

export function focusPanel(layout, id) {
  return has(layout, id) && layout.focusedId !== id ? { ...layout, focusedId: id } : layout;
}

/** Maximiza el panel (y lo enfoca) o, si ya lo estaba, vuelve a la disposición normal. */
export function toggleMaximize(layout, id) {
  if (!has(layout, id)) return layout;
  return layout.maximizedId === id ? { ...layout, maximizedId: null } : { ...layout, focusedId: id, maximizedId: id };
}

/** Paneles visibles, en orden. El enfocado siempre se ve: si no cabe, ocupa el último hueco. Maximizado: solo ese. */
export function visibleIds(layout, maxVisible = MAX_VISIBLE) {
  if (layout.maximizedId && has(layout, layout.maximizedId)) return [layout.maximizedId];
  const limit = Math.max(1, maxVisible);
  const shown = layout.order.slice(0, limit);
  if (!shown.includes(layout.focusedId)) shown[shown.length - 1] = layout.focusedId;
  return shown;
}

/** Cuadrícula para N paneles visibles: 1, 2 en columnas o 2×2. */
export function gridFor(count) {
  if (count <= 1) return { columns: 1, rows: 1 };
  if (count === 2) return { columns: 2, rows: 1 };
  return { columns: 2, rows: 2 };
}

// ------------------------------------------------------------------ persistencia por workspace --------------------------------------

/** Normaliza lo guardado (puede venir manipulado o de otra versión) a `{ panels: [{ id, sessionId }], focusedId }`. */
export function sanitize(raw) {
  const out = { panels: [{ id: MAIN_ID, sessionId: null }], focusedId: MAIN_ID };
  if (!raw || typeof raw !== 'object' || !Array.isArray(raw.panels)) return out;
  const seen = new Set();
  const panels = [];
  for (const p of raw.panels) {
    const id = p && typeof p.id === 'string' ? p.id : null;
    if (!id || seen.has(id) || !/^(main|p\d{1,2})$/.test(id) || panels.length >= MAX_PANELS) continue;
    seen.add(id);
    const sid = p.sessionId;
    panels.push({ id, sessionId: typeof sid === 'string' && sid && sid !== 'ready' && sid.length < 200 ? sid : null });
  }
  if (!seen.has(MAIN_ID)) panels.unshift({ id: MAIN_ID, sessionId: null });
  out.panels = panels.slice(0, MAX_PANELS);
  out.focusedId = out.panels.some((p) => p.id === raw.focusedId) ? raw.focusedId : MAIN_ID;
  return out;
}

const defaultStorage = () => {
  try {
    return typeof window !== 'undefined' ? window.localStorage : null;
  } catch {
    return null;
  }
};

/** Almacén por workspace. Perder lo guardado (JSON corrupto, cuota) nunca debe romper la interfaz. */
export function createLayoutStore(storage = defaultStorage()) {
  const read = () => {
    try {
      const parsed = JSON.parse(storage?.getItem(STORAGE_KEY) || 'null');
      if (parsed && typeof parsed === 'object' && parsed.states && typeof parsed.states === 'object' && Array.isArray(parsed.order)) return parsed;
    } catch { /* corrupto: se empieza de cero */ }
    return { order: [], states: {} };
  };
  return {
    load(workspace) {
      if (!workspace || typeof workspace !== 'string') return sanitize(null);
      return sanitize(read().states[workspace]);
    },
    save(workspace, value) {
      if (!workspace || typeof workspace !== 'string') return;
      const data = read();
      data.states[workspace] = sanitize(value);
      data.order = [...data.order.filter((w) => w !== workspace), workspace];
      while (data.order.length > MAX_WORKSPACES) delete data.states[data.order.shift()];
      try {
        storage?.setItem(STORAGE_KEY, JSON.stringify(data));
      } catch { /* cuota llena o almacenamiento no disponible */ }
    },
  };
}

export const layoutStore = createLayoutStore();
