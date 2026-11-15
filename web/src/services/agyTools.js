/** Catálogo de herramientas de agy que se pueden activar a mano (FE-015). El servidor es la única fuente: grupos, riesgo y aviso. */
import { authFetch } from './api';

let cached = null;

/** `{ groups: [{ id, title, risk, summary, warning, tools, caps }], images_available }`, o `null` si el servidor no responde. */
export async function fetchAgyTools() {
  if (cached) return cached;
  try {
    const res = await authFetch('/v1/providers/agy/tools');
    if (!res.ok) return null;
    const data = (await res.json()).data;
    if (!data || !Array.isArray(data.groups)) return null;
    cached = data;
    return data;
  } catch {
    return null;
  }
}

export const resetAgyToolsCache = () => { cached = null; };
