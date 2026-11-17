// Herramientas propias de agy que el usuario activa a mano (FE-015). Lógica pura de la configuración de misión.
//
// Lo activado lo ejecuta agy por su cuenta, SIN la política, el supervisor ni el sandbox de PRAXEON, así que cada grupo exige una
// confirmación explícita. Ni las herramientas ni las confirmaciones se guardan: viven solo en la configuración en memoria del panel
// (no están en `CONFIG_FIELDS` ni en `pickConfig`) y no pasan a los paneles nuevos, de modo que se vuelven a pedir cada vez.

export const isAgy = (provider) => ['agy', 'antigravity'].includes(String(provider || '').toLowerCase());

const uniq = (list) => [...new Set((Array.isArray(list) ? list : []).filter((x) => typeof x === 'string' && x))];

/** Lo elegido en la configuración: `tools` (activadas), `ack` (confirmadas de ellas) y `pending` (activadas sin confirmar). Solo con agy. */
export function agySelection(config) {
  if (!isAgy(config?.llmProvider)) return { tools: [], ack: [], pending: [] };
  const tools = uniq(config.agyTools);
  const acked = new Set(uniq(config.agyToolsAck));
  return { tools, ack: tools.filter((t) => acked.has(t)), pending: tools.filter((t) => !acked.has(t)) };
}

/** Activar un grupo lo deja SIN confirmar; desactivarlo quita también su confirmación. */
export function toggleGroup(config, id, on) {
  const tools = uniq(config?.agyTools).filter((t) => t !== id);
  const ack = uniq(config?.agyToolsAck).filter((t) => t !== id);
  return { agyTools: on ? [...tools, id] : tools, agyToolsAck: ack };
}

/** Confirmar (o retirar la confirmación de) un grupo ya activado. */
export function confirmGroup(config, id, ok) {
  const tools = uniq(config?.agyTools);
  if (!tools.includes(id)) return { agyToolsAck: uniq(config?.agyToolsAck) };
  const ack = uniq(config?.agyToolsAck).filter((t) => t !== id);
  return { agyToolsAck: ok ? [...ack, id] : ack };
}

/** Aviso bajo el cuadro de mensaje: si hay herramientas activadas sin confirmar, no se envía. `null` si no hay nada que decir. */
export function agyNotice(selection, catalog = []) {
  if (!selection || selection.tools.length === 0) return null;
  const title = (id) => catalog.find((g) => g.id === id)?.title || id;
  if (selection.pending.length > 0) {
    return { level: 'error', blocked: true, message: `Confirma o desmarca las herramientas de agy: ${selection.pending.map(title).join(', ')}. Las ejecuta agy por su cuenta, sin la política de PRAXEON.` };
  }
  return { level: 'warn', blocked: false, message: `Herramientas de agy activadas (${selection.ack.map(title).join(', ')}): las ejecuta agy sin pasar por la política, el supervisor ni el sandbox de PRAXEON.` };
}

/** Campos de la petición de misión. Solo se mandan los grupos confirmados (el servidor lo vuelve a exigir). */
export function missionFields(config) {
  const sel = agySelection(config);
  return sel.ack.length > 0 ? { agy_tools: sel.ack, agy_tools_ack: sel.ack } : {};
}

/** La configuración que se copia a otros paneles o se recuerda: sin herramientas de agy ni confirmaciones. */
export function withoutAgyTools(config) {
  if (!config || typeof config !== 'object') return config;
  // eslint-disable-next-line no-unused-vars
  const { agyTools, agyToolsAck, ...rest } = config;
  return rest;
}

/**
 * Aviso cuando el modelo elegido de agy es de los lentos. Medido con el agy real (3 turnos con el proveedor de PRAXEON): los de esfuerzo bajo o medio de
 * Gemini 3.8 y los de Claude/GPT-OSS responden en 2-5 s por turno; los «xhigh»/«high» y las familias argon/carbon tardan 6-20 s.
 * `null` si el modelo es rápido o desconocido.
 */
export function slowModelNotice(model) {
  const m = String(model || '').trim().toLowerCase();
  if (!m) return null;
  if (!/(xhigh|-high$|argon|carbon)/.test(m)) return null;
  return `«${m}» piensa más y suele tardar 3-5 veces más por paso (6-20 s frente a 2-5 s). Para ir rápido usa gemini-3.8-flash-medium, gemini-3.8-flash-low o gemini-4-hydrogen-low.`;
}

/** Imágenes que ha generado el modelo, a partir de los eventos `agy.image_generated` (las rutas son del workspace). */
export function imageFromEvent(payload) {
  const rel = payload?.rel_path;
  if (typeof rel !== 'string' || !rel.startsWith('.praxeon/generated/') || rel.includes('..')) return null;
  return { rel_path: rel, name: String(payload.name || rel.split('/').pop()), width: Number(payload.width) || 0, height: Number(payload.height) || 0 };
}
