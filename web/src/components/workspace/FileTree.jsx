import React, { useCallback, useEffect, useState } from 'react';
import { ChevronRight, ChevronDown, Folder, FolderOpen, File, RefreshCw } from 'lucide-react';
import { fetchWorkspaceTree } from '../../services/workspaceFiles';

const ROW = { display: 'flex', alignItems: 'center', gap: 6, padding: '3px 8px', cursor: 'pointer', fontSize: 13, whiteSpace: 'nowrap', userSelect: 'none' };

function Node({ entry, depth, selectedPath, onOpenFile, reloadKey }) {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState({ loading: false, error: null, entries: null, truncated: false });
  const isDir = entry.type === 'dir';

  const load = useCallback(async () => {
    setState((s) => ({ ...s, loading: true, error: null }));
    try {
      const data = await fetchWorkspaceTree(entry.rel_path);
      setState({ loading: false, error: null, entries: data.entries, truncated: data.truncated });
    } catch (err) {
      setState({ loading: false, error: err.message, entries: null, truncated: false });
    }
  }, [entry.rel_path]);

  useEffect(() => {
    if (open) load();
  }, [reloadKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggle = () => {
    if (!isDir) {
      onOpenFile?.(entry.rel_path);
      return;
    }
    const next = !open;
    setOpen(next);
    if (next && state.entries === null) load();
  };

  const selected = !isDir && selectedPath === entry.rel_path;
  const Icon = isDir ? (open ? FolderOpen : Folder) : File;
  return (
    <div>
      <div
        role="treeitem"
        aria-expanded={isDir ? open : undefined}
        aria-selected={selected}
        tabIndex={0}
        style={{ ...ROW, paddingLeft: 8 + depth * 14, background: selected ? 'var(--hover, rgba(127,127,127,.18))' : 'transparent' }}
        onClick={toggle}
        onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), toggle())}
        title={entry.rel_path}
      >
        {isDir ? (open ? <ChevronDown size={13} /> : <ChevronRight size={13} />) : <span style={{ width: 13 }} />}
        <Icon size={14} strokeWidth={1.6} />
        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{entry.name}</span>
      </div>
      {isDir && open && (
        <div role="group">
          {state.loading && <div style={{ ...ROW, paddingLeft: 22 + depth * 14, color: 'var(--muted)' }}>Cargando…</div>}
          {state.error && <div style={{ ...ROW, paddingLeft: 22 + depth * 14, color: 'var(--bad, #e5484d)' }}>{state.error}</div>}
          {state.entries && state.entries.length === 0 && (
            <div style={{ ...ROW, paddingLeft: 22 + depth * 14, color: 'var(--muted)' }}>Carpeta vacía</div>
          )}
          {state.entries?.map((child) => (
            <Node key={child.rel_path} entry={child} depth={depth + 1} selectedPath={selectedPath} onOpenFile={onOpenFile} reloadKey={reloadKey} />
          ))}
          {state.truncated && <div style={{ ...ROW, paddingLeft: 22 + depth * 14, color: 'var(--muted)' }}>… hay más entradas (lista truncada)</div>}
        </div>
      )}
    </div>
  );
}

/** Árbol del workspace activo. Se recarga al recibir `praxeon:workspace-changed` (FE-001). */
export default function FileTree({ selectedPath = null, onOpenFile, workspaceName = '' }) {
  const [root, setRoot] = useState({ loading: true, error: null, entries: [], truncated: false });
  const [reloadKey, setReloadKey] = useState(0);

  const loadRoot = useCallback(async () => {
    setRoot((r) => ({ ...r, loading: true, error: null }));
    try {
      const data = await fetchWorkspaceTree('');
      setRoot({ loading: false, error: null, entries: data.entries, truncated: data.truncated });
    } catch (err) {
      setRoot({ loading: false, error: err.message, entries: [], truncated: false });
    }
  }, []);

  useEffect(() => {
    loadRoot();
    const onChange = () => {
      setReloadKey((k) => k + 1);
      loadRoot();
    };
    window.addEventListener('praxeon:workspace-changed', onChange);
    return () => window.removeEventListener('praxeon:workspace-changed', onChange);
  }, [loadRoot]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0, height: '100%' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '8px 10px', fontSize: 11, letterSpacing: '.06em', textTransform: 'uppercase', color: 'var(--muted)' }}>
        <span>{workspaceName || 'Explorador'}</span>
        <button type="button" aria-label="Actualizar árbol" onClick={() => { setReloadKey((k) => k + 1); loadRoot(); }} style={{ background: 'none', border: 0, color: 'inherit', cursor: 'pointer' }}>
          <RefreshCw size={13} />
        </button>
      </div>
      <div role="tree" style={{ flex: 1, overflowY: 'auto' }}>
        {root.loading && <div style={{ ...ROW, color: 'var(--muted)' }}>Cargando…</div>}
        {root.error && <div style={{ ...ROW, color: 'var(--bad, #e5484d)' }}>{root.error}</div>}
        {!root.loading && !root.error && root.entries.length === 0 && <div style={{ ...ROW, color: 'var(--muted)' }}>El workspace está vacío</div>}
        {root.entries.map((e) => (
          <Node key={e.rel_path} entry={e} depth={0} selectedPath={selectedPath} onOpenFile={onOpenFile} reloadKey={reloadKey} />
        ))}
        {root.truncated && <div style={{ ...ROW, color: 'var(--muted)' }}>… hay más entradas (lista truncada)</div>}
      </div>
    </div>
  );
}
