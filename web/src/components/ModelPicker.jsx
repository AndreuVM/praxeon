import React, { useState } from 'react';
import { Plus, Trash2 } from 'lucide-react';
import { addCustomModel, BUILTIN_MODELS, hasModels, isBuiltin, modelsFor, removeCustomModel } from '../utils/modelCatalog';

const field = {
  width: '100%',
  backgroundColor: '#121722',
  border: '1px solid #202737',
  borderRadius: '4px',
  padding: '4px 6px',
  color: '#f0f6fc',
  fontSize: '10.5px',
  outline: 'none',
};

/**
 * Lista de modelos del proveedor elegido (distinta para cada uno). Permite añadir modelos propios y quitarlos.
 * value '' = el modelo predeterminado del proveedor.
 */
export default function ModelPicker({ provider, value, onChange, disabled }) {
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState('');
  const [, force] = useState(0);

  if (!hasModels(provider)) {
    return (
      <div>
        <label style={{ fontSize: '10px', color: '#8b949e', display: 'block', marginBottom: '2px' }}>Modelo:</label>
        <div style={{ ...field, color: '#64748b' }}>No aplica (modo simulado)</div>
      </div>
    );
  }

  const models = modelsFor(provider, [value]);
  const defaultModel = (BUILTIN_MODELS[provider] || [])[0];
  const removable = value && !isBuiltin(provider, value);

  const confirmAdd = () => {
    const name = draft.trim();
    if (!name) return;
    addCustomModel(provider, name);
    onChange(name);
    setDraft('');
    setAdding(false);
    force((n) => n + 1);
  };

  return (
    <div>
      <label style={{ fontSize: '10px', color: '#8b949e', display: 'flex', justifyContent: 'space-between', marginBottom: '2px' }}>
        <span>Modelo:</span>
        {!disabled && (
          <button
            type="button"
            onClick={() => setAdding((v) => !v)}
            style={{ background: 'none', border: 'none', color: '#58a6ff', fontSize: '10px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '2px', padding: 0 }}
          >
            <Plus size={10} /> Añadir
          </button>
        )}
      </label>
      <div style={{ display: 'flex', gap: '4px' }}>
        <select value={value || ''} onChange={(e) => onChange(e.target.value)} disabled={disabled} style={{ ...field, flex: 1, minWidth: 0 }}>
          <option value="">Predeterminado ({defaultModel})</option>
          {models.map((m) => (
            <option key={m} value={m}>
              {m}
              {isBuiltin(provider, m) ? '' : ' (añadido)'}
            </option>
          ))}
        </select>
        {removable && !disabled && (
          <button
            type="button"
            title="Quitar este modelo de la lista"
            onClick={() => {
              removeCustomModel(provider, value);
              onChange('');
              force((n) => n + 1);
            }}
            style={{ background: '#161c28', border: '1px solid #202737', borderRadius: '4px', color: '#f87171', cursor: 'pointer', padding: '0 6px' }}
          >
            <Trash2 size={11} />
          </button>
        )}
      </div>
      {adding && !disabled && (
        <div style={{ display: 'flex', gap: '4px', marginTop: '4px' }}>
          <input
            autoFocus
            type="text"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                confirmAdd();
              }
              if (e.key === 'Escape') setAdding(false);
            }}
            placeholder="nombre exacto del modelo"
            style={{ ...field, flex: 1, minWidth: 0 }}
          />
          <button
            type="button"
            onClick={confirmAdd}
            disabled={!draft.trim()}
            style={{ background: draft.trim() ? '#238636' : '#18202d', border: '1px solid #2ea043', borderRadius: '4px', color: '#fff', fontSize: '10.5px', padding: '0 8px', cursor: draft.trim() ? 'pointer' : 'not-allowed' }}
          >
            Añadir
          </button>
        </div>
      )}
    </div>
  );
}
