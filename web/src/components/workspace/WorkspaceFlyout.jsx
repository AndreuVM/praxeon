import React, { useEffect, useState } from 'react';
import { AlertTriangle, FolderOpen, FolderSearch, X } from 'lucide-react';

const wsName = (p) => (p || '').split(/[\\/]/).filter(Boolean).pop() || p;

/**
 * Panel de workspace anclado al riel (RD-6.1): workspace actual, recientes, olvidar y acceso al explorador.
 * Más adelante crecerá con guardado de proyectos y explorador de archivos.
 */
export default function WorkspaceFlyout({ open, workspace, sessionWorkspace, onSelect, onForget, onBrowse, onClose }) {
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return undefined;
    setError('');
    const onKey = (e) => { if (e.key === 'Escape') onClose?.(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  const current = workspace?.path || '';
  const recent = workspace?.recent || [];
  const mismatch = Boolean(sessionWorkspace && current && sessionWorkspace !== current);

  const choose = async (path) => {
    setBusy(true);
    setError('');
    try {
      await onSelect(path);
      onClose?.();
    } catch (err) {
      setError(err.message || 'No se pudo cambiar el workspace.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 54 }} />
      <div
        role="dialog"
        aria-label="Workspace del proyecto"
        className="rise"
        style={{
          position: 'fixed',
          left: '60px',
          top: '10px',
          width: '320px',
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
          Workspace del proyecto
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
          <span style={{ fontWeight: 600 }}>{current ? wsName(current) : 'Sin workspace elegido'}</span>
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

        {recent.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
            <div style={{ fontSize: 'var(--fs-label)', color: 'var(--muted)', marginBottom: '2px' }}>Recientes</div>
            {recent.map((r) => (
              <div key={r.path} style={{ display: 'flex', alignItems: 'center', gap: '4px', opacity: r.exists ? 1 : 0.55 }}>
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
                  <span style={{ minWidth: 0 }}>
                    <span style={{ display: 'block' }}>{r.name}{r.path === current ? ' · actual' : ''}</span>
                    <span style={{ display: 'block', fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-label)', color: 'var(--muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {r.path}
                    </span>
                  </span>
                </button>
                {onForget && (
                  <button
                    type="button"
                    className="btn-icon"
                    style={{ width: '26px', height: '26px' }}
                    onClick={() => onForget(r.path)}
                    title="Quitar de la lista"
                    aria-label={`Quitar ${r.name} de la lista`}
                  >
                    <X size={13} />
                  </button>
                )}
              </div>
            ))}
          </div>
        )}

        {error && <div style={{ color: 'var(--bad)', fontSize: 'var(--fs-secondary)' }}>{error}</div>}

        <button type="button" className="btn btn-secondary" onClick={onBrowse} style={{ justifyContent: 'flex-start' }}>
          <FolderSearch size={14} />
          Explorar carpetas…
        </button>

        <div style={{ color: 'var(--muted)', fontSize: 'var(--fs-secondary)' }}>
          Más adelante: guardar proyectos y explorar sus archivos.
        </div>
      </div>
    </>
  );
}
