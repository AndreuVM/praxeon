import React, { useEffect, useRef, useState } from 'react';
import { AlertTriangle, ArrowLeftRight, FolderOpen, FolderSearch, Pencil, Star, X } from 'lucide-react';

const wsName = (p) => (p || '').split(/[\\/]/).filter(Boolean).pop() || p;

const smallBtn = { width: '26px', height: '26px' };

/** Campo para poner o quitar el alias de un workspace. Enter guarda, Esc cancela. */
function AliasInput({ initial, onSave, onCancel }) {
  const ref = useRef(null);
  const done = useRef(false);
  useEffect(() => {
    ref.current?.focus();
    ref.current?.select();
  }, []);
  const finish = (save) => {
    if (done.current) return;
    done.current = true;
    if (save) onSave(ref.current.value);
    else onCancel();
  };
  return (
    <input
      ref={ref}
      defaultValue={initial}
      maxLength={40}
      aria-label="Alias del workspace"
      placeholder="Alias (vacío = nombre de la carpeta)"
      spellCheck={false}
      onKeyDown={(e) => {
        e.stopPropagation();
        if (e.key === 'Enter') finish(true);
        else if (e.key === 'Escape') finish(false);
      }}
      onBlur={() => finish(true)}
      style={{ flex: 1, minWidth: 0, font: 'inherit', background: 'var(--bg)', color: 'var(--text)', border: '1px solid var(--accent, #6ea0ff)', borderRadius: 6, padding: '5px 8px' }}
    />
  );
}

/**
 * Panel de workspaces anclado al riel (RD-6.1): el actual, los favoritos con su atajo Alt+1…9, los recientes, alias y olvidar.
 * Cambiar entre ellos es inmediato; Alt+0 alterna con el anterior.
 */
export default function WorkspaceFlyout({ open, workspace, sessionWorkspace, onSelect, onForget, onPin, onAlias, onBrowse, onClose }) {
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(null);   // ruta del workspace cuyo alias se está editando

  useEffect(() => {
    if (!open) return undefined;
    setError('');
    setEditing(null);
    const onKey = (e) => { if (e.key === 'Escape') onClose?.(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  const current = workspace?.path || '';
  const known = workspace?.recent || [];
  const favorites = known.filter((r) => r.pinned);
  const recent = known.filter((r) => !r.pinned);
  const previous = known.find((r) => r.path === workspace?.previous && r.exists) || null;
  const mismatch = Boolean(sessionWorkspace && current && sessionWorkspace !== current);

  const run = async (action) => {
    setBusy(true);
    setError('');
    try {
      await action();
    } catch (err) {
      setError(err.message || 'No se pudo completar la operación.');
    } finally {
      setBusy(false);
    }
  };

  const choose = (path) => run(async () => {
    await onSelect(path);
    onClose?.();
  });

  const row = (r) => (
    <div key={r.path} style={{ display: 'flex', alignItems: 'center', gap: '4px', opacity: r.exists ? 1 : 0.55 }}>
      {editing === r.path ? (
        <AliasInput
          initial={r.alias || ''}
          onCancel={() => setEditing(null)}
          onSave={(value) => { setEditing(null); if (value.trim() !== (r.alias || '')) run(() => onAlias(r.path, value)); }}
        />
      ) : (
        <button
          type="button"
          disabled={!r.exists || busy}
          onClick={() => choose(r.path)}
          title={r.exists ? r.path : `${r.path} (ya no existe)`}
          style={{
            flex: 1,
            minWidth: 0,
            display: 'flex',
            alignItems: 'center',
            gap: '10px',
            textAlign: 'left',
            background: r.path === current ? 'var(--hover)' : 'transparent',
            border: 0,
            borderRadius: 'var(--r-ui)',
            padding: '7px 9px',
            color: r.path === current ? 'var(--text)' : 'var(--muted)',
          }}
          className="ws-row"
        >
          <FolderOpen size={15} strokeWidth={1.6} style={{ flexShrink: 0 }} />
          <span style={{ minWidth: 0, flex: 1 }}>
            <span style={{ display: 'block' }}>
              {r.name}{r.alias && r.folder !== r.name ? <span style={{ color: 'var(--muted)' }}> · {r.folder}</span> : null}{r.path === current ? ' · actual' : ''}
            </span>
            <span style={{ display: 'block', fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-label)', color: 'var(--muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {r.path}
            </span>
          </span>
          {r.pinned && <kbd style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-label)', color: 'var(--muted)', border: '1px solid var(--line)', borderRadius: 4, padding: '0 5px' }}>Alt+{r.slot}</kbd>}
        </button>
      )}
      {onPin && (
        <button
          type="button"
          className="btn-icon"
          style={{ ...smallBtn, color: r.pinned ? 'var(--accent, #6ea0ff)' : undefined }}
          disabled={busy}
          onClick={() => run(() => onPin(r.path, !r.pinned))}
          title={r.pinned ? 'Quitar de favoritos' : 'Fijar como favorito (acceso rápido Alt+1…9)'}
          aria-label={r.pinned ? `Quitar ${r.name} de favoritos` : `Fijar ${r.name} como favorito`}
          aria-pressed={r.pinned}
        >
          <Star size={13} fill={r.pinned ? 'currentColor' : 'none'} />
        </button>
      )}
      {onAlias && (
        <button type="button" className="btn-icon" style={smallBtn} onClick={() => setEditing(r.path)} title="Ponerle un nombre corto" aria-label={`Cambiar el alias de ${r.name}`}>
          <Pencil size={13} />
        </button>
      )}
      {onForget && (
        <button type="button" className="btn-icon" style={smallBtn} onClick={() => run(async () => onForget(r.path))} title="Quitar de la lista" aria-label={`Quitar ${r.name} de la lista`}>
          <X size={13} />
        </button>
      )}
    </div>
  );

  const heading = (text) => <div style={{ fontSize: 'var(--fs-label)', color: 'var(--muted)', marginBottom: '2px' }}>{text}</div>;

  return (
    <>
      <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 54 }} />
      <div
        role="dialog"
        aria-label="Workspaces"
        className="rise"
        style={{
          position: 'fixed',
          left: '60px',
          top: '10px',
          width: '360px',
          maxHeight: 'calc(100vh - 20px)',
          overflowY: 'auto',
          zIndex: 55,
          backgroundColor: 'var(--surface)',
          border: '1px solid var(--line)',
          borderRadius: 'var(--r-panel)',
          boxShadow: 'var(--shadow-overlay)',
          padding: '14px',
          display: 'flex',
          flexDirection: 'column',
          gap: '10px',
        }}
      >
        <div style={{ fontSize: 'var(--fs-label)', letterSpacing: '0.07em', textTransform: 'uppercase', color: 'var(--muted)' }}>
          Workspaces
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
          <span style={{ fontWeight: 600 }}>{current ? workspace?.name || wsName(current) : 'Sin workspace elegido'}</span>
          {current && (
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-label)', color: 'var(--muted)', overflowWrap: 'anywhere' }}>
              {current}
            </span>
          )}
        </div>

        {mismatch && (
          <div style={{ display: 'flex', gap: '8px', color: 'var(--bad)', fontSize: 'var(--fs-secondary)' }}>
            <AlertTriangle size={14} style={{ flexShrink: 0, marginTop: '2px' }} />
            <span>
              La sesión activa trabaja en otra carpeta: <span style={{ fontFamily: 'var(--font-mono)' }}>{sessionWorkspace}</span>
            </span>
          </div>
        )}

        {previous && (
          <button type="button" className="btn btn-secondary" disabled={busy} onClick={() => choose(previous.path)} style={{ justifyContent: 'flex-start' }} title={previous.path}>
            <ArrowLeftRight size={14} />
            Volver a «{previous.name}»
            <kbd style={{ marginLeft: 'auto', fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-label)', color: 'var(--muted)' }}>Alt+0</kbd>
          </button>
        )}

        {favorites.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
            {heading('Favoritos')}
            {favorites.map(row)}
          </div>
        )}

        {recent.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
            {heading(favorites.length ? 'Recientes' : 'Recientes · pulsa la estrella para fijar un favorito')}
            {recent.map(row)}
          </div>
        )}

        {error && <div style={{ color: 'var(--bad)', fontSize: 'var(--fs-secondary)' }}>{error}</div>}

        <button type="button" className="btn btn-secondary" onClick={onBrowse} style={{ justifyContent: 'flex-start' }}>
          <FolderSearch size={14} />
          Añadir workspace…
        </button>
      </div>
    </>
  );
}
