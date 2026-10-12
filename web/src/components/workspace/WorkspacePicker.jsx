import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowUp, Check, Folder, FolderOpen, History, X, Eye, EyeOff } from 'lucide-react';
import * as api from '../../services/api';

const box = { backgroundColor: '#0a0e16', border: '1px solid #1e293b', borderRadius: '8px' };
const mono = { fontFamily: 'var(--font-mono)' };

function hintLabel(hints = []) {
  if (!hints.length) return null;
  return (
    <span
      title={`Contiene: ${hints.join(', ')}`}
      style={{ fontSize: '9.5px', color: '#34d399', border: '1px solid rgba(52,211,153,0.35)', borderRadius: '4px', padding: '0 4px' }}
    >
      proyecto
    </span>
  );
}

/**
 * Selector de workspace: carpeta nativa (escritorio), recientes, explorador de carpetas
 * y ruta escrita a mano con validación en vivo. Llama a onSelect(ruta) con una carpeta válida.
 */
export default function WorkspacePicker({ open, title = 'Elegir workspace', initialPath = '', recent = [], onSelect, onClose, onForget }) {
  const [typed, setTyped] = useState('');
  const [validation, setValidation] = useState(null);
  const [browse, setBrowse] = useState(null);
  const [showHidden, setShowHidden] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const nativeAvailable = api.canPickFolderNatively();
  const validateSeq = useRef(0);

  const load = useCallback(async (path, hidden) => {
    try {
      setBrowse(await api.browseWorkspace(path, hidden));
      setError('');
    } catch (err) {
      setError(err.message);
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    setTyped(initialPath || '');
    setValidation(null);
    load(initialPath || '', showHidden);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    if (!open || !typed.trim()) {
      setValidation(null);
      return undefined;
    }
    const seq = ++validateSeq.current;
    const timer = setTimeout(async () => {
      try {
        const result = await api.validateWorkspacePath(typed.trim());
        if (seq === validateSeq.current) setValidation(result);
      } catch {
        if (seq === validateSeq.current) setValidation(null);
      }
    }, 250);
    return () => clearTimeout(timer);
  }, [typed, open]);

  if (!open) return null;

  const choose = async (path) => {
    setBusy(true);
    setError('');
    try {
      await onSelect(path);
      onClose();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const pickNative = async () => {
    const path = await api.pickFolderNatively(browse?.path || initialPath || '');
    if (path) await choose(path);
  };

  const toggleHidden = () => {
    const next = !showHidden;
    setShowHidden(next);
    if (browse) load(browse.path, next);
  };

  const valid = validation?.valid === true;

  return (
    <div
      onClick={onClose}
      style={{ position: 'fixed', inset: 0, backgroundColor: 'rgba(0,0,0,0.65)', backdropFilter: 'blur(4px)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 120 }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{ width: 'min(680px, 94vw)', maxHeight: '88vh', display: 'flex', flexDirection: 'column', gap: '12px', backgroundColor: '#121824', border: '1px solid #233146', borderRadius: '10px', padding: '18px', boxShadow: 'var(--shadow-lg)' }}
      >
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <h2 style={{ fontSize: '15px', fontWeight: 700, color: '#f8fafc', display: 'flex', alignItems: 'center', gap: '8px' }}>
            <FolderOpen size={16} style={{ color: '#58a6ff' }} /> {title}
          </h2>
          <button type="button" onClick={onClose} aria-label="Cerrar" style={{ background: 'none', border: 'none', color: '#8b949e', cursor: 'pointer' }}>
            <X size={16} />
          </button>
        </div>
        <p style={{ fontSize: '11.5px', color: '#8b949e', lineHeight: 1.4 }}>
          Es la carpeta del proyecto sobre la que trabaja el agente: las rutas relativas parten de aquí y no puede salir de ella.
        </p>

        {/* Ruta escrita o pegada */}
        <div>
          <div style={{ display: 'flex', gap: '8px' }}>
            <input
              type="text"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && valid) choose(validation.path);
              }}
              placeholder="Pega o escribe la ruta de la carpeta"
              style={{ ...mono, flex: 1, padding: '8px 10px', fontSize: '12px', color: '#f8fafc', outline: 'none', ...box }}
            />
            <button
              type="button"
              disabled={!valid || busy}
              onClick={() => choose(validation.path)}
              style={{ padding: '0 14px', borderRadius: '6px', border: 'none', fontSize: '12px', fontWeight: 600, cursor: valid ? 'pointer' : 'not-allowed', backgroundColor: valid ? '#2563eb' : '#1e293b', color: valid ? '#fff' : '#64748b' }}
            >
              Usar
            </button>
          </div>
          {validation && (
            <div style={{ fontSize: '11px', marginTop: '4px', color: valid ? '#34d399' : '#f87171', display: 'flex', alignItems: 'center', gap: '6px' }}>
              {valid ? <Check size={12} /> : <X size={12} />}
              {valid ? 'Carpeta válida' : validation.reason}
              {valid && hintLabel(validation.hints)}
            </div>
          )}
        </div>

        {nativeAvailable && (
          <button
            type="button"
            onClick={pickNative}
            disabled={busy}
            style={{ alignSelf: 'flex-start', display: 'flex', alignItems: 'center', gap: '6px', padding: '7px 12px', borderRadius: '6px', border: '1px solid #2b6cb0', backgroundColor: 'rgba(56,139,253,0.12)', color: '#58a6ff', fontSize: '12px', fontWeight: 600, cursor: 'pointer' }}
          >
            <FolderOpen size={14} /> Elegir con el explorador del sistema…
          </button>
        )}

        {recent.length > 0 && (
          <div>
            <div style={{ fontSize: '10.5px', color: '#8b949e', display: 'flex', alignItems: 'center', gap: '5px', marginBottom: '4px' }}>
              <History size={11} /> Usados recientemente
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '3px', maxHeight: '120px', overflowY: 'auto' }}>
              {recent.map((r) => (
                <div key={r.path} style={{ display: 'flex', alignItems: 'center', gap: '6px', ...box, padding: '4px 8px', opacity: r.exists ? 1 : 0.55 }}>
                  <button
                    type="button"
                    disabled={!r.exists || busy}
                    onClick={() => choose(r.path)}
                    title={r.exists ? r.path : `${r.path} (ya no existe)`}
                    style={{ flex: 1, minWidth: 0, textAlign: 'left', background: 'none', border: 'none', cursor: r.exists ? 'pointer' : 'not-allowed', color: '#c9d1d9', fontSize: '12px' }}
                  >
                    <strong style={{ color: '#f0f6fc' }}>{r.name}</strong>
                    <span style={{ ...mono, color: '#64748b', fontSize: '10.5px', marginLeft: '8px', overflowWrap: 'anywhere' }}>{r.path}</span>
                  </button>
                  {onForget && (
                    <button type="button" onClick={() => onForget(r.path)} title="Quitar de la lista" aria-label="Quitar de la lista" style={{ background: 'none', border: 'none', color: '#64748b', cursor: 'pointer' }}>
                      <X size={12} />
                    </button>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Explorador de carpetas */}
        <div style={{ ...box, display: 'flex', flexDirection: 'column', minHeight: 0, flex: 1 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px', padding: '6px 8px', borderBottom: '1px solid #1e293b' }}>
            <button
              type="button"
              disabled={!browse?.parent}
              onClick={() => load(browse.parent, showHidden)}
              title="Subir a la carpeta superior"
              style={{ background: 'none', border: '1px solid #243044', borderRadius: '4px', color: '#c9d1d9', cursor: browse?.parent ? 'pointer' : 'not-allowed', padding: '2px 5px', opacity: browse?.parent ? 1 : 0.4 }}
            >
              <ArrowUp size={13} />
            </button>
            <select
              value=""
              onChange={(e) => e.target.value && load(e.target.value, showHidden)}
              style={{ backgroundColor: '#161c28', border: '1px solid #243044', borderRadius: '4px', color: '#c9d1d9', fontSize: '11px', padding: '2px 4px' }}
            >
              <option value="">Ir a…</option>
              {(browse?.roots || []).map((r) => (
                <option key={r.path} value={r.path}>{r.name}</option>
              ))}
            </select>
            <span style={{ ...mono, fontSize: '11px', color: '#94a3b8', flex: 1, minWidth: 0, overflowWrap: 'anywhere' }}>{browse?.path || '…'}</span>
            <button type="button" onClick={toggleHidden} title={showHidden ? 'Ocultar carpetas ocultas' : 'Mostrar carpetas ocultas'} style={{ background: 'none', border: 'none', color: '#8b949e', cursor: 'pointer' }}>
              {showHidden ? <EyeOff size={13} /> : <Eye size={13} />}
            </button>
            <button
              type="button"
              disabled={!browse || busy}
              onClick={() => choose(browse.path)}
              style={{ padding: '4px 10px', borderRadius: '5px', border: 'none', fontSize: '11px', fontWeight: 600, cursor: 'pointer', backgroundColor: '#2563eb', color: '#fff' }}
            >
              Usar esta carpeta
            </button>
          </div>
          <div style={{ overflowY: 'auto', maxHeight: '240px', padding: '4px' }}>
            {(browse?.entries || []).length === 0 && !error && (
              <div style={{ fontSize: '11.5px', color: '#64748b', padding: '10px' }}>Esta carpeta no tiene subcarpetas.</div>
            )}
            {(browse?.entries || []).map((e) => (
              <button
                key={e.path}
                type="button"
                onClick={() => load(e.path, showHidden)}
                style={{ width: '100%', display: 'flex', alignItems: 'center', gap: '8px', textAlign: 'left', padding: '5px 8px', background: 'none', border: 'none', borderRadius: '4px', color: '#c9d1d9', fontSize: '12px', cursor: 'pointer' }}
                onMouseEnter={(ev) => (ev.currentTarget.style.backgroundColor = '#161c28')}
                onMouseLeave={(ev) => (ev.currentTarget.style.backgroundColor = 'transparent')}
              >
                <Folder size={13} style={{ color: '#58a6ff', flexShrink: 0 }} />
                <span style={{ flex: 1 }}>{e.name}</span>
                {hintLabel(e.hints)}
              </button>
            ))}
            {browse?.truncated && <div style={{ fontSize: '10.5px', color: '#eab308', padding: '6px 8px' }}>Lista recortada: hay más carpetas de las mostradas.</div>}
          </div>
        </div>

        {error && <div style={{ fontSize: '11.5px', color: '#f87171' }}>{error}</div>}
      </div>
    </div>
  );
}
