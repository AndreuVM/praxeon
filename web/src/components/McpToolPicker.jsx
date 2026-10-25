import React, { useEffect, useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, Search } from 'lucide-react';
import { fetchMcpServers } from '../services/api';

const splitCsv = (text) => String(text || '').split(',').map((t) => t.trim()).filter(Boolean);

// «mcp__marq__pm_list_tasks» -> «mcp__marq__*»: un solo permiso que cubre todas las herramientas de ese MCP (y las que añada después)
const serverPattern = (exposedName) => {
  const parts = String(exposedName).split('__');
  return parts.length >= 3 ? `${parts[0]}__${parts[1]}__*` : null;
};

const box = { backgroundColor: 'var(--bg)', border: '1px solid var(--line)', borderRadius: '9px' };

// Herramientas MCP aprobadas, agrupadas por servidor, para permitirlas a un agente: un interruptor por MCP entero o por herramienta suelta.
export default function McpToolPicker({ value, onChange }) {
  const [servers, setServers] = useState([]);
  const [open, setOpen] = useState({});
  const [query, setQuery] = useState('');

  useEffect(() => {
    let alive = true;
    fetchMcpServers()
      .then((list) => {
        if (!alive) return;
        const grouped = list
          .map((s) => {
            const tools = (s.tools || []).filter((t) => t.exposed).map((t) => ({ name: t.exposed_as, description: t.description || '' }));
            return { name: s.name, tools, pattern: tools.length ? serverPattern(tools[0].name) : null };
          })
          .filter((s) => s.tools.length > 0);
        setServers(grouped);
      })
      .catch(() => alive && setServers([])); // sin acceso a la API de MCP simplemente no se ofrece
    return () => { alive = false; };
  }, []);

  const current = splitCsv(value);
  const everything = current.includes('*');
  const q = query.trim().toLowerCase();

  const view = useMemo(() => servers.map((s) => {
    const wholeServer = Boolean(s.pattern) && current.includes(s.pattern);
    const isOn = (t) => everything || wholeServer || current.includes(t.name);
    const visible = q ? s.tools.filter((t) => `${t.name} ${t.description}`.toLowerCase().includes(q) || s.name.toLowerCase().includes(q)) : s.tools;
    return { ...s, wholeServer, isOn, visible, count: s.tools.filter(isOn).length };
  }), [servers, current.join('|'), everything, q]); // eslint-disable-line react-hooks/exhaustive-deps

  if (servers.length === 0) return null;

  const emit = (list) => onChange(list.join(', '));

  const toggleServer = (s) => {
    const names = new Set(s.tools.map((t) => t.name));
    const rest = current.filter((t) => t !== s.pattern && !names.has(t));
    emit(s.wholeServer || s.count === s.tools.length ? rest : [...rest, s.pattern || s.tools.map((t) => t.name)].flat());
  };

  const toggleTool = (s, tool) => {
    if (s.wholeServer) {
      // se quita una herramienta de un MCP entero: pasa a una lista explícita con todas las demás
      emit([...current.filter((t) => t !== s.pattern), ...s.tools.filter((t) => t.name !== tool.name).map((t) => t.name)]);
      return;
    }
    emit(current.includes(tool.name) ? current.filter((t) => t !== tool.name) : [...current, tool.name]);
  };

  const total = servers.reduce((n, s) => n + s.tools.length, 0);

  return (
    <div style={{ marginTop: '8px', display: 'flex', flexDirection: 'column', gap: '6px' }}>
      <span style={{ fontSize: '11px', color: 'var(--muted)' }}>
        {everything
          ? 'Con «*» este agente puede usar todas las herramientas MCP aprobadas. Quita «*» y elige abajo para limitarlo.'
          : `Herramientas MCP aprobadas (${servers.length} ${servers.length === 1 ? 'servidor' : 'servidores'}, ${total} herramientas). Marca un MCP entero o abre su lista para elegir.`}
      </span>

      {total > 8 && (
        <label style={{ ...box, display: 'flex', alignItems: 'center', gap: '6px', padding: '5px 9px' }}>
          <Search size={13} style={{ color: 'var(--muted)', flexShrink: 0 }} />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Buscar herramienta o MCP"
            aria-label="Buscar herramienta MCP"
            style={{ flex: 1, minWidth: 0, background: 'transparent', border: 0, outline: 0, color: 'var(--text)', fontSize: '12px' }}
          />
        </label>
      )}

      <div style={{ ...box, display: 'flex', flexDirection: 'column', maxHeight: '300px', overflowY: 'auto' }}>
        {view.map((s) => {
          if (q && s.visible.length === 0) return null;
          const expanded = q ? true : Boolean(open[s.name]);
          const all = s.count === s.tools.length;
          return (
            <div key={s.name} style={{ borderBottom: '1px solid var(--line)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '6px 9px' }}>
                <input
                  type="checkbox"
                  checked={all}
                  ref={(el) => { if (el) el.indeterminate = !all && s.count > 0; }}
                  disabled={everything}
                  onChange={() => toggleServer(s)}
                  aria-label={`Permitir todo el MCP ${s.name}`}
                  title={s.pattern ? `Permitir todo ${s.name} (${s.pattern})` : `Permitir todo ${s.name}`}
                />
                <button
                  type="button"
                  onClick={() => setOpen({ ...open, [s.name]: !open[s.name] })}
                  aria-expanded={expanded}
                  style={{ flex: 1, minWidth: 0, display: 'flex', alignItems: 'center', gap: '6px', background: 'transparent', border: 0, padding: 0, color: 'var(--text)', textAlign: 'left', cursor: 'pointer' }}
                >
                  {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                  <span style={{ fontSize: '12.5px', fontWeight: 600 }}>{s.name}</span>
                  <span style={{ fontSize: '11px', color: 'var(--muted)', marginLeft: 'auto' }}>
                    {everything ? 'todas' : `${s.count} de ${s.tools.length}`}
                  </span>
                </button>
              </div>
              {expanded && (
                <div style={{ display: 'flex', flexDirection: 'column', padding: '0 9px 6px 33px' }}>
                  {s.visible.map((t) => (
                    <label key={t.name} title={t.description || t.name} style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '3px 0', fontSize: '11.5px', fontFamily: 'var(--font-mono)', color: s.isOn(t) ? 'var(--text)' : 'var(--muted)' }}>
                      <input type="checkbox" checked={s.isOn(t)} disabled={everything} onChange={() => toggleTool(s, t)} />
                      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.pattern ? t.name.slice(s.pattern.length - 1) : t.name}</span>
                    </label>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
