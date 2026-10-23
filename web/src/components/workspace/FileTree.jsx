import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ChevronRight, ChevronDown, Folder, FolderOpen, File, RefreshCw, FilePlus, FolderPlus, Pencil, Trash2 } from 'lucide-react';
import {
  fetchWorkspaceTree,
  createWorkspaceEntry,
  renameWorkspaceEntry,
  deleteWorkspaceEntry,
} from '../../services/workspaceFiles';
import { confirmDialog, notify } from '../Dialogs';

const ROW = { display: 'flex', alignItems: 'center', gap: 6, padding: '3px 8px', cursor: 'pointer', fontSize: 13, whiteSpace: 'nowrap', userSelect: 'none' };
const iconBtn = { background: 'none', border: 0, color: 'inherit', cursor: 'pointer', padding: 2, display: 'inline-flex', opacity: 0.75 };

const parentOf = (p) => (p.includes('/') ? p.slice(0, p.lastIndexOf('/')) : '');
const joinPath = (dir, name) => (dir ? `${dir}/${name}` : name);

/** Avisa a otros componentes (el editor) de que una entrada cambió de sitio o ya no existe. */
function announce(detail) {
  window.dispatchEvent(new CustomEvent('praxeon:workspace-entry-changed', { detail }));
}

/** Campo de nombre en línea: Enter confirma, Esc o perder el foco cancelan. */
function NameInput({ initial = '', depth, icon: Icon, onSubmit, onCancel }) {
  const ref = useRef(null);
  const done = useRef(false);
  useEffect(() => {
    ref.current?.focus();
    if (initial) {
      const dot = initial.lastIndexOf('.');
      ref.current?.setSelectionRange(0, dot > 0 ? dot : initial.length);   // al renombrar se selecciona solo el nombre, sin la extensión
    }
  }, [initial]);
  const finish = (value) => {
    if (done.current) return;
    done.current = true;
    const name = value.trim();
    if (!name || name === initial) onCancel();
    else onSubmit(name);
  };
  return (
    <div style={{ ...ROW, paddingLeft: 8 + depth * 14 + 19, cursor: 'text' }}>
      <Icon size={14} strokeWidth={1.6} />
      <input
        ref={ref}
        defaultValue={initial}
        aria-label="Nombre"
        spellCheck={false}
        onKeyDown={(e) => {
          e.stopPropagation();
          if (e.key === 'Enter') finish(e.currentTarget.value);
          else if (e.key === 'Escape') {
            done.current = true;
            onCancel();
          }
        }}
        onBlur={(e) => finish(e.currentTarget.value)}
        style={{ flex: 1, minWidth: 0, font: 'inherit', background: 'var(--bg)', color: 'var(--text)', border: '1px solid var(--accent, #6ea0ff)', borderRadius: 4, padding: '1px 4px' }}
      />
    </div>
  );
}

function Node({ entry, depth, selectedPath, onOpenFile, reloadKey, ctx }) {
  const [open, setOpen] = useState(false);
  const [hover, setHover] = useState(false);
  const [state, setState] = useState({ loading: false, error: null, entries: null, truncated: false });
  const isDir = entry.type === 'dir';
  const { creating, renaming, startCreate, startRename, commitCreate, commitRename, cancel, remove, focusDir } = ctx;

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

  // Crear dentro de esta carpeta: se abre para mostrar el campo de nombre
  const creatingHere = isDir && creating && creating.dir === entry.rel_path;
  useEffect(() => {
    if (creatingHere && !open) {
      setOpen(true);
      if (state.entries === null) load();
    }
  }, [creatingHere]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggle = () => {
    if (!isDir) {
      onOpenFile?.(entry.rel_path);
      return;
    }
    focusDir(entry.rel_path);
    const next = !open;
    setOpen(next);
    if (next && state.entries === null) load();
  };

  const isRenaming = renaming === entry.rel_path;
  const selected = !isDir && selectedPath === entry.rel_path;
  const Icon = isDir ? (open ? FolderOpen : Folder) : File;

  return (
    <div>
      {isRenaming ? (
        <NameInput
          initial={entry.name}
          depth={depth}
          icon={Icon}
          onSubmit={(name) => commitRename(entry, name)}
          onCancel={cancel}
        />
      ) : (
        <div
          role="treeitem"
          aria-expanded={isDir ? open : undefined}
          aria-selected={selected}
          tabIndex={0}
          style={{ ...ROW, paddingLeft: 8 + depth * 14, background: selected ? 'var(--hover, rgba(127,127,127,.18))' : 'transparent' }}
          onClick={toggle}
          onMouseEnter={() => setHover(true)}
          onMouseLeave={() => setHover(false)}
          onFocus={() => setHover(true)}
          onBlur={() => setHover(false)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              toggle();
            } else if (e.key === 'F2') {
              e.preventDefault();
              startRename(entry);
            } else if (e.key === 'Delete') {
              e.preventDefault();
              remove(entry);
            }
          }}
          title={entry.rel_path}
        >
          {isDir ? (open ? <ChevronDown size={13} /> : <ChevronRight size={13} />) : <span style={{ width: 13 }} />}
          <Icon size={14} strokeWidth={1.6} />
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', flex: 1 }}>{entry.name}</span>
          {hover && (
            <span style={{ display: 'inline-flex', gap: 2, flexShrink: 0 }} onClick={(e) => e.stopPropagation()}>
              {isDir && (
                <>
                  <button type="button" style={iconBtn} aria-label={`Nuevo archivo en ${entry.name}`} title="Nuevo archivo" onClick={() => startCreate(entry.rel_path, 'file')}><FilePlus size={13} /></button>
                  <button type="button" style={iconBtn} aria-label={`Nueva carpeta en ${entry.name}`} title="Nueva carpeta" onClick={() => startCreate(entry.rel_path, 'dir')}><FolderPlus size={13} /></button>
                </>
              )}
              <button type="button" style={iconBtn} aria-label={`Renombrar ${entry.name}`} title="Renombrar (F2)" onClick={() => startRename(entry)}><Pencil size={13} /></button>
              <button type="button" style={iconBtn} aria-label={`Eliminar ${entry.name}`} title="Eliminar (Supr)" onClick={() => remove(entry)}><Trash2 size={13} /></button>
            </span>
          )}
        </div>
      )}
      {isDir && open && (
        <div role="group">
          {creatingHere && (
            <NameInput
              depth={depth + 1}
              icon={creating.kind === 'dir' ? Folder : File}
              onSubmit={(name) => commitCreate(name)}
              onCancel={cancel}
            />
          )}
          {state.loading && <div style={{ ...ROW, paddingLeft: 22 + depth * 14, color: 'var(--muted)' }}>Cargando…</div>}
          {state.error && <div style={{ ...ROW, paddingLeft: 22 + depth * 14, color: 'var(--bad, #e5484d)' }}>{state.error}</div>}
          {state.entries && state.entries.length === 0 && !creatingHere && (
            <div style={{ ...ROW, paddingLeft: 22 + depth * 14, color: 'var(--muted)' }}>Carpeta vacía</div>
          )}
          {state.entries?.map((child) => (
            <Node key={child.rel_path} entry={child} depth={depth + 1} selectedPath={selectedPath} onOpenFile={onOpenFile} reloadKey={reloadKey} ctx={ctx} />
          ))}
          {state.truncated && <div style={{ ...ROW, paddingLeft: 22 + depth * 14, color: 'var(--muted)' }}>… hay más entradas (lista truncada)</div>}
        </div>
      )}
    </div>
  );
}

/** Árbol del workspace activo con gestión de archivos (FE-001 + crear, renombrar y eliminar). Se recarga al cambiar de workspace. */
export default function FileTree({ selectedPath = null, onOpenFile, workspaceName = '' }) {
  const [root, setRoot] = useState({ loading: true, error: null, entries: [], truncated: false });
  const [reloadKey, setReloadKey] = useState(0);
  const [creating, setCreating] = useState(null);   // { dir, kind }
  const [renaming, setRenaming] = useState(null);   // rel_path
  const [currentDir, setCurrentDir] = useState(''); // carpeta donde crean los botones de la cabecera

  const loadRoot = useCallback(async () => {
    setRoot((r) => ({ ...r, loading: true, error: null }));
    try {
      const data = await fetchWorkspaceTree('');
      setRoot({ loading: false, error: null, entries: data.entries, truncated: data.truncated });
    } catch (err) {
      setRoot({ loading: false, error: err.message, entries: [], truncated: false });
    }
  }, []);

  const refresh = useCallback(() => {
    setReloadKey((k) => k + 1);
    loadRoot();
  }, [loadRoot]);

  useEffect(() => {
    loadRoot();
    const onChange = () => {
      setCreating(null);
      setRenaming(null);
      setCurrentDir('');
      refresh();
    };
    window.addEventListener('praxeon:workspace-changed', onChange);
    return () => window.removeEventListener('praxeon:workspace-changed', onChange);
  }, [loadRoot, refresh]);

  const cancel = useCallback(() => {
    setCreating(null);
    setRenaming(null);
  }, []);

  const fail = (err) => notify(err.message || 'No se pudo completar la operación.');

  const ctx = {
    creating,
    renaming,
    focusDir: setCurrentDir,
    cancel,
    startCreate: (dir, kind) => {
      setRenaming(null);
      setCreating({ dir, kind });
    },
    startRename: (entry) => {
      setCreating(null);
      setRenaming(entry.rel_path);
    },
    commitCreate: async (name) => {
      const target = creating;
      setCreating(null);
      try {
        const res = await createWorkspaceEntry(joinPath(target.dir, name), target.kind);
        refresh();
        if (target.kind === 'file') onOpenFile?.(res.rel_path);
      } catch (err) {
        fail(err);
      }
    },
    commitRename: async (entry, name) => {
      setRenaming(null);
      try {
        const res = await renameWorkspaceEntry(entry.rel_path, joinPath(parentOf(entry.rel_path), name));
        announce({ action: 'rename', path: res.old_rel_path, newPath: res.rel_path });
        refresh();
      } catch (err) {
        fail(err);
      }
    },
    remove: async (entry) => {
      const isDir = entry.type === 'dir';
      const ok = await confirmDialog(
        isDir
          ? `¿Eliminar la carpeta «${entry.name}» y todo su contenido? No se puede deshacer.`
          : `¿Eliminar el archivo «${entry.name}»? No se puede deshacer.`,
        { confirmLabel: 'Eliminar', danger: true },
      );
      if (!ok) return;
      try {
        await deleteWorkspaceEntry(entry.rel_path);
        announce({ action: 'delete', path: entry.rel_path });
        refresh();
      } catch (err) {
        fail(err);
      }
    },
  };

  const creatingAtRoot = creating && creating.dir === '';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0, height: '100%' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '8px 10px', fontSize: 11, letterSpacing: '.06em', textTransform: 'uppercase', color: 'var(--muted)' }}>
        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{workspaceName || 'Explorador'}</span>
        <span style={{ display: 'inline-flex', gap: 4 }}>
          <button type="button" aria-label="Nuevo archivo" title="Nuevo archivo" onClick={() => ctx.startCreate(currentDir, 'file')} style={iconBtn}><FilePlus size={14} /></button>
          <button type="button" aria-label="Nueva carpeta" title="Nueva carpeta" onClick={() => ctx.startCreate(currentDir, 'dir')} style={iconBtn}><FolderPlus size={14} /></button>
          <button type="button" aria-label="Actualizar árbol" title="Actualizar" onClick={refresh} style={iconBtn}><RefreshCw size={13} /></button>
        </span>
      </div>
      <div role="tree" style={{ flex: 1, overflowY: 'auto' }}>
        {creatingAtRoot && (
          <NameInput depth={-1} icon={creating.kind === 'dir' ? Folder : File} onSubmit={(name) => ctx.commitCreate(name)} onCancel={cancel} />
        )}
        {root.loading && <div style={{ ...ROW, color: 'var(--muted)' }}>Cargando…</div>}
        {root.error && <div style={{ ...ROW, color: 'var(--bad, #e5484d)' }}>{root.error}</div>}
        {!root.loading && !root.error && root.entries.length === 0 && !creatingAtRoot && <div style={{ ...ROW, color: 'var(--muted)' }}>El workspace está vacío</div>}
        {root.entries.map((e) => (
          <Node key={e.rel_path} entry={e} depth={0} selectedPath={selectedPath} onOpenFile={onOpenFile} reloadKey={reloadKey} ctx={ctx} />
        ))}
        {root.truncated && <div style={{ ...ROW, color: 'var(--muted)' }}>… hay más entradas (lista truncada)</div>}
      </div>
    </div>
  );
}
