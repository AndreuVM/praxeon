// Prepara el texto de una respuesta para mostrarlo: los nodos de veredicto devuelven un objeto JSON
// ({"approved": true, "feedback": "..."}) y a veces el texto llega con «\n» literales. Aquí se extrae lo legible.

const TEXT_KEYS = ['feedback', 'summary', 'result', 'answer', 'response', 'report', 'message', 'text'];

function unescapeLiterals(s) {
  return s.replace(/\\n/g, '\n').replace(/\\t/g, '  ').replace(/\\"/g, '"').replace(/\\\\/g, '\\');
}

function fromObject(obj) {
  const key = TEXT_KEYS.find((k) => typeof obj[k] === 'string' && obj[k].trim());
  const verdict = typeof obj.approved === 'boolean' ? (obj.approved ? 'approved' : 'rejected') : null;
  if (key) return { text: obj[key], verdict };
  const lines = Object.entries(obj)
    .filter(([k]) => k !== 'approved')
    .map(([k, v]) => `- **${k}**: ${typeof v === 'string' ? v : JSON.stringify(v)}`);
  return { text: lines.join('\n'), verdict };
}

/** @returns {{text: string, verdict: 'approved'|'rejected'|null}} */
export function presentAnswer(raw) {
  const src = String(raw || '').trim();
  if (src.startsWith('{')) {
    try {
      const obj = JSON.parse(src);
      if (obj && typeof obj === 'object' && !Array.isArray(obj)) return fromObject(obj);
    } catch {
      // JSON truncado o mal escapado: se rescata el campo de texto con una expresión regular
      const approved = src.match(/"approved"\s*:\s*(true|false)/i);
      const fb = src.match(/"(?:feedback|summary|result|answer)"\s*:\s*"([\s\S]*?)"?\s*\}?\s*$/);
      if (fb) {
        return { text: unescapeLiterals(fb[1]), verdict: approved ? (approved[1].toLowerCase() === 'true' ? 'approved' : 'rejected') : null };
      }
    }
  }
  // «\n» literales sin saltos de línea reales: es texto escapado dos veces
  const text = !src.includes('\n') && src.includes('\\n') ? unescapeLiterals(src) : src;
  return { text, verdict: null };
}
