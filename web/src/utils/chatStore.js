// Persistencia local del chat de cada sesión y de la última configuración usada.
// Nunca se guardan secretos (API key) ni la confirmación de Full Access: esa se vuelve a dar en cada ventana.

const CHATS_KEY = 'praxeon_chats_v1';
const CONFIG_KEY = 'praxeon_chat_config_v1';
const MAX_SESSIONS = 40;
const MAX_TEXT = 20000;

const CONFIG_FIELDS = ['llmProvider', 'customModel', 'supervisor', 'executionMode', 'workflowId', 'maxSteps', 'baseUrl', 'workspaceRoot'];

function read(key) {
  try {
    const value = JSON.parse(localStorage.getItem(key) || 'null');
    return value && typeof value === 'object' ? value : null;
  } catch {
    return null;
  }
}

function write(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
    return true;
  } catch {
    return false;
  }
}

const clip = (text, max = MAX_TEXT) => (typeof text === 'string' && text.length > max ? `${text.slice(0, max)}\n[… recortado …]` : text);

// Recorta lo más voluminoso (salidas de comandos, resúmenes) para no agotar el almacenamiento
function compactMessage(msg) {
  if (!msg || typeof msg !== 'object') return msg;
  const out = { ...msg };
  if (Array.isArray(out.thoughts)) {
    out.thoughts = out.thoughts.map((t) => ({ ...t, executionOutput: clip(t.executionOutput, 2000), thought: clip(t.thought, 4000) }));
  }
  if (Array.isArray(out.milestones)) out.milestones = out.milestones.map((m) => ({ ...m, summary: clip(m.summary) }));
  if (typeof out.finalAnswer === 'string') out.finalAnswer = clip(out.finalAnswer, 60000);
  return out;
}

export function loadChats() {
  const stored = read(CHATS_KEY);
  return stored && stored.chats && typeof stored.chats === 'object' ? stored.chats : {};
}

export function persistChats(chats) {
  const ids = Object.keys(chats).filter((sid) => Array.isArray(chats[sid]) && chats[sid].length > 0);
  // los más recientes son los últimos insertados
  let keep = ids.slice(-MAX_SESSIONS);
  const build = (list) => ({ chats: Object.fromEntries(list.map((sid) => [sid, chats[sid].map(compactMessage)])) });
  while (keep.length > 0 && !write(CHATS_KEY, build(keep))) {
    keep = keep.slice(Math.ceil(keep.length / 2)); // cuota llena: se descartan las sesiones más antiguas
  }
}

export function loadChatConfig() {
  const stored = read(CONFIG_KEY) || {};
  return Object.fromEntries(CONFIG_FIELDS.filter((k) => stored[k] !== undefined && stored[k] !== null).map((k) => [k, stored[k]]));
}

export function persistChatConfig(config) {
  write(CONFIG_KEY, Object.fromEntries(CONFIG_FIELDS.filter((k) => config[k] !== undefined).map((k) => [k, config[k]])));
}
