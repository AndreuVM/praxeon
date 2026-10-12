const AGENT_COLORS = ['#38bdf8', '#f472b6', '#a3e635', '#fb923c', '#c084fc', '#2dd4bf', '#facc15', '#f87171'];

// Color estable por agente: el mismo agente siempre se ve igual en el árbol, los bloques y la leyenda
export function agentColor(name) {
  if (!name) return '#64748b';
  let h = 0;
  for (let i = 0; i < name.length; i += 1) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return AGENT_COLORS[h % AGENT_COLORS.length];
}
