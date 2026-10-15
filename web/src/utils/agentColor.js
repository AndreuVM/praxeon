export const AGENT_COLORS = ['var(--text)', 'var(--muted)'];

// Los agentes se distinguen por nombre (y alternancia de tono), nunca por colores semánticos
export function agentColor(name) {
  if (!name) return 'var(--muted)';
  let h = 0;
  for (let i = 0; i < name.length; i += 1) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return AGENT_COLORS[h % AGENT_COLORS.length];
}
