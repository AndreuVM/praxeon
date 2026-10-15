import React, { useState } from 'react';
import { Plus, Trash2 } from 'lucide-react';
import { addCustomModel, BUILTIN_MODELS, hasModels, isBuiltin, modelsFor, removeCustomModel } from '../utils/modelCatalog';

const field = {
  width: '100%',
  backgroundColor: 'var(--surface)',
  border: '1px solid var(--line)',
  borderRadius: '6px',
  padding: '4px 6px',
  color: 'var(--text)',
  fontSize: '11px',
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
        <label style={{ fontSize: 'var(--fs-secondary)', color: 'var(--muted)', display: 'block', marginBottom: '4px' }}>Modelo</label>
        <div style={{ ...field, color: 'var(--muted)' }}>No aplica (modo simulado)</div>
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
      <label style={{ fontSize: '11px', color: 'var(--muted)', display: 'flex', justifyContent: 'space-between', marginBottom: '2px' }}>
        <span>Modelo</span>
        {!disabled && (
          <button
            type="button"
            onClick={() => setAdding((v) => !v)}
            style={{ background: 'none', border: 'none', color: 'var(--accent)', fontSize: '11px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '2px', padding: 0 }}
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
            style={{ background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: '6px', color: 'var(--bad)', cursor: 'pointer', padding: '0 6px' }}
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
            style={{ background: draft.trim() ? 'var(--ok)' : 'var(--line)', border: '1px solid var(--ok)', borderRadius: '6px', color: 'var(--on-accent)', fontSize: '11px', padding: '0 8px', cursor: draft.trim() ? 'pointer' : 'not-allowed' }}
          >
            Añadir
          </button>
        </div>
      )}
    </div>
  );
}
