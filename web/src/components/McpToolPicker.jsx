import React, { useEffect, useState } from 'react';
import { fetchMcpServers } from '../services/api';

const splitCsv = (text) => String(text || '').split(',').map((t) => t.trim()).filter(Boolean);

// Herramientas MCP aprobadas y expuestas, para añadirlas o quitarlas de la lista de herramientas permitidas de un agente.
export default function McpToolPicker({ value, onChange }) {
  const [tools, setTools] = useState([]);

  useEffect(() => {
    let alive = true;
    fetchMcpServers()
      .then((servers) => {
        if (!alive) return;
        setTools(servers.flatMap((s) => (s.tools || []).filter((t) => t.exposed).map((t) => ({ name: t.exposed_as, server: s.name, description: t.description }))));
      })
      .catch(() => alive && setTools([])); // sin acceso a la API de MCP simplemente no se ofrece
    return () => { alive = false; };
  }, []);

  if (tools.length === 0) return null;
  const current = splitCsv(value);
  const everything = current.includes('*');

  const toggle = (name) => {
    const next = current.includes(name) ? current.filter((t) => t !== name) : [...current, name];
    onChange(next.join(', '));
  };

  return (
    <div style={{ marginTop: '6px' }}>
      <span style={{ fontSize: '10.5px', color: '#73849c', display: 'block', marginBottom: '4px' }}>
        {everything
          ? 'Con «*» este agente puede usar todas las herramientas MCP aprobadas. Quita «*» y elige abajo para limitarlo.'
          : 'Herramientas MCP aprobadas (pulsa para permitirlas o quitarlas):'}
      </span>
      <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
        {tools.map((t) => {
          const on = everything || current.includes(t.name);
          return (
            <button
              key={t.name}
              type="button"
              disabled={everything}
              onClick={() => toggle(t.name)}
              title={t.description || t.name}
              aria-pressed={on}
              style={{
                fontSize: '11px', fontFamily: 'var(--font-mono)', padding: '2px 8px', borderRadius: '4px', cursor: everything ? 'default' : 'pointer',
                backgroundColor: on ? 'rgba(56,189,248,0.12)' : 'transparent',
                border: `1px solid ${on ? 'rgba(56,189,248,0.45)' : '#243044'}`,
                color: on ? '#38bdf8' : '#8b949e',
              }}
            >
              {t.name}
            </button>
          );
        })}
      </div>
    </div>
  );
}
