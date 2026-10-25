import React, { useState } from 'react';
import { addCustomModel, hasModels, modelsFor } from '../utils/modelCatalog';

const CUSTOM = '__custom__';
const fieldStyle = {
  width: '100%', backgroundColor: 'var(--bg)', border: '1px solid var(--line)', borderRadius: '9px',
  padding: '7px 10px', fontSize: '12.5px', color: 'var(--text)',
};

// Modelo del agente: desplegable con los modelos conocidos del proveedor; «Otro…» permite escribir uno que no esté en la lista.
export default function AgentModelSelect({ provider, value, onChange }) {
  const [typing, setTyping] = useState(false);
  const [draft, setDraft] = useState('');

  if (!hasModels(provider)) {
    return <input type="text" value={value} onChange={(e) => onChange(e.target.value)} placeholder="nombre del modelo" aria-label="Modelo" style={fieldStyle} />;
  }

  const options = modelsFor(provider, [value]);
  const commit = () => {
    const name = draft.trim();
    setTyping(false);
    setDraft('');
    if (!name) return;
    addCustomModel(provider, name);   // queda en la lista para la próxima vez
    onChange(name);
  };

  if (typing) {
    return (
      <input
        autoFocus
        type="text"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') { e.preventDefault(); commit(); }
          if (e.key === 'Escape') { setTyping(false); setDraft(''); }
        }}
        placeholder="Nombre del modelo y Enter"
        aria-label="Nombre del modelo"
        style={fieldStyle}
      />
    );
  }

  return (
    <select
      value={value}
      aria-label="Modelo"
      onChange={(e) => (e.target.value === CUSTOM ? setTyping(true) : onChange(e.target.value))}
      style={fieldStyle}
    >
      {options.map((m) => <option key={m} value={m}>{m}</option>)}
      <option value={CUSTOM}>Otro modelo…</option>
    </select>
  );
}
