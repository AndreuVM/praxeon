import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { ChevronRight, ChevronDown, Folder, FolderOpen, File, RefreshCw, FilePlus, FolderPlus, Pencil, Trash2 } from 'lucide-react';
import {
  fetchWorkspaceTree,
  createWorkspaceEntry,
  renameWorkspaceEntry,
  deleteWorkspaceEntry,
  uploadWorkspaceFile,
} from '../../services/workspaceFiles';
import { confirmDialog, notify } from '../Dialogs';
import { explorerStore, isInside, moveRel, removed, renamed, reveal, setOpen as rememberOpen } from '../../utils/explorerState';

const ROW = { display: 'flex', alignItems: 'center', gap: 6, padding: '3px 8px', cursor: 'pointer', fontSize: 13, whiteSpace: 'nowrap', userSelect: 'none' };
const iconBtn = { background: 'none', border: 0, color: 'inherit', cursor: 'pointer', padding: 2, display: 'inline-flex', opacity: 0.75 };

const parentOf = (p) => (p.includes('/') ? p.slice(0, p.lastIndexOf('/')) : '');
const joinPath = (dir, name) => (dir ? `${dir}/${name}` : name);

const hasFiles = (e) => Array.from(e.dataTransfer?.types || []).includes('Files');
// Arrastre interno: una entrada del propio árbol que se mueve a otra carpeta (distinto de soltar archivos de Windows)
const ENTRY_MIME = 'application/x-praxeon-entry';
const isInternal = (e) => Array.from(e.dataTransfer?.types || []).includes(ENTRY_MIME);
const accepts = (e) => hasFiles(e) || isInternal(e);
const baseNameOf = (p) => p.split('/').pop();

const readFile = (entry) => new Promise((resolve, reject) => entry.file(resolve, reject));
const readAll = async (reader) => {
  const all = [];
  for (;;) {
    const batch = await new Promise((resolve, reject) => reader.readEntries(resolve, reject));
    if (!batch.length) return all;
    all.push(...batch);
  }
};

/** Recorre lo soltado (archivos y carpetas, con su contenido) y lo devuelve como lista plana con rutas relativas. */
async function walk(entry, prefix, out) {
  const rel = prefix ? `${prefix}/${entry.name}` : entry.name;
  if (entry.isFile) {
    out.push({ kind: 'file', rel, file: await readFile(entry) });
    return;
  }
  const children = await readAll(entry.createReader());
  if (!children.length) out.push({ kind: 'dir', rel });          // las carpetas vacías también se crean
  for (const child of children) await walk(child, rel, out);
}

/** Debe llamarse dentro del evento `drop`: pasado el evento el navegador vacía `dataTransfer.items`. */
function grabDropped(dt) {
  const entries = Array.from(dt.items || []).filter((i) => i.kind === 'file').map((i) => (i.webkitGetAsEntry ? i.webkitGetAsEntry() : null));
  if (entries.length && entries.every(Boolean)) return { entries, files: null };
  return { entries: null, files: Array.from(dt.files || []) };   // navegadores sin webkitGetAsEntry: solo archivos sueltos
}

async function listDropped({ entries, files }) {
  if (files) return files.map((file) => ({ kind: 'file', rel: file.name, file }));
  const out = [];
  for (const entry of entries) await walk(entry, '', out);
  return out;
}

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
  const [open, setOpen] = useState(() => entry.type === 'dir' && ctx.isOpen(entry.rel_path));   // restaurado al volver al explorador
  const [hover, setHover] = useState(false);
  const [state, setState] = useState({ loading: false, error: null, entries: null, truncated: false });
  const isDir = entry.type === 'dir';
  const { creating, renaming, startCreate, startRename, commitCreate, commitRename, cancel, remove, focusDir, dragDir, setDragDir, drop } = ctx;
  const dropDir = isDir ? entry.rel_path : parentOf(entry.rel_path);

  const { loadStart, loadEnd, gone, forgetMissing } = ctx;
  const load = useCallback(async () => {
    setState((s) => ({ ...s, loading: true, error: null }));
    loadStart();
    try {
      const data = await fetchWorkspaceTree(entry.rel_path);
      setState({ loading: false, error: null, entries: data.entries, truncated: data.truncated });
    } catch (err) {
      setState({ loading: false, error: err.message, entries: null, truncated: false });
      if (err.status === 404) gone(entry.rel_path);    // la carpeta ya no existe: se olvida (no se reabrirá si se recrea)
    } finally {
      loadEnd();
    }
  }, [entry.rel_path, loadStart, loadEnd, gone]);

  useEffect(() => {
    if (open) load();
  }, [reloadKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // Una ruta recordada (carpeta abierta, selección o carpeta actual) que ya no aparece en el listado de su carpeta se olvida sin error.
  // Solo al cambiar el listado: un archivo recién creado se selecciona antes de que el listado se recargue.
  useEffect(() => {
    if (isDir && state.entries && !state.truncated) forgetMissing(entry.rel_path, state.entries);
  }, [state.entries]); // eslint-disable-line react-hooks/exhaustive-deps

  // Crear dentro de esta carpeta: se abre para mostrar el campo de nombre
  const creatingHere = isDir && creating && creating.dir === entry.rel_path;
  useEffect(() => {
    if (creatingHere && !open) {
      setOpen(true);
      ctx.rememberOpen(entry.rel_path, true);
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
    ctx.rememberOpen(entry.rel_path, next);
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
          style={{ ...ROW, paddingLeft: 8 + depth * 14, background: dragDir === dropDir && isDir ? 'rgba(110,160,255,.22)' : selected ? 'var(--hover, rgba(127,127,127,.18))' : 'transparent' }}
          onClick={toggle}
          draggable
          onDragStart={(e) => {
            e.dataTransfer.setData(ENTRY_MIME, entry.rel_path);
            e.dataTransfer.effectAllowed = 'move';
          }}
          onDragEnd={() => setDragDir(null)}
          onDragOver={(e) => { if (accepts(e)) { e.preventDefault(); e.stopPropagation(); e.dataTransfer.dropEffect = isInternal(e) ? 'move' : 'copy'; if (dragDir !== dropDir) setDragDir(dropDir); } }}
          onDrop={(e) => { if (accepts(e)) { e.preventDefault(); e.stopPropagation(); drop(e, dropDir); } }}
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
export default function FileTree({ selectedPath = null, onOpenFile, workspaceName = '', workspacePath = '', onSelectedMissing }) {
  const [root, setRoot] = useState({ loading: true, error: null, entries: [], truncated: false });
  const [reloadKey, setReloadKey] = useState(0);
  const [creating, setCreating] = useState(null);   // { dir, kind }
  const [renaming, setRenaming] = useState(null);   // rel_path
  // Estado recordado de este workspace (carpetas abiertas, carpeta actual, desplazamiento). El árbol se monta con la clave del
  // workspace, así que este estado inicial es siempre el del workspace que se muestra. Sin ruta no se guarda nada.
  const [initial] = useState(() => explorerStore.save(workspacePath, (s) => reveal(s, selectedPath)));
  const openSet = useRef(new Set(initial.open));
  const [currentDir, setCurrentDir] = useState(initial.currentDir); // carpeta donde crean los botones de la cabecera
  const treeRef = useRef(null);
  const pending = useRef(0);                                 // cargas de carpetas en curso (para saber cuándo restaurar el desplazamiento)
  const restoring = useRef(initial.scroll > 0);
  const lastScroll = useRef(initial.scroll);
  const settle = useRef(null);
  const live = useRef({ selectedPath, currentDir });         // lo último visto, para los efectos de los nodos
  useLayoutEffect(() => { live.current = { selectedPath, currentDir }; });   // antes que los efectos de los nodos
  const [dragDir, setDragDir] = useState(null);     // carpeta que se resalta al arrastrar archivos encima ('' = raíz)
  const [uploading, setUploading] = useState(null); // { done, total } mientras se suben archivos

  const persist = useCallback((update) => {
    const next = explorerStore.save(workspacePath, update);
    openSet.current = new Set(next.open);
    return next;
  }, [workspacePath]);

  const loadStart = useCallback(() => { pending.current += 1; }, []);
  const loadEnd = useCallback(() => {
    pending.current -= 1;
    settle.current?.();
  }, []);

  /** Una ruta ya no existe: se olvida lo recordado dentro de ella y se limpian la carpeta actual y la selección si estaban ahí. */
  const alive = useRef(true);                                // tras desmontar (p. ej. cambio de workspace) una carga tardía ya no toca lo guardado
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);
  const gone = useCallback((rel) => {
    if (!alive.current) return;
    persist((s) => removed(s, rel));
    setCurrentDir((c) => (c && isInside(c, rel) ? '' : c));
    if (live.current.selectedPath && isInside(live.current.selectedPath, rel)) onSelectedMissing?.();
  }, [persist, onSelectedMissing]);

  /** `entries` es el listado completo de la carpeta `parent`: lo recordado que cuelga directamente de ella y no está se olvida. */
  const forgetMissing = useCallback((parent, entries) => {
    const names = new Set(entries.map((c) => c.rel_path));
    const { selectedPath: sel, currentDir: cur } = live.current;
    for (const p of [...openSet.current, sel, cur]) if (p && parentOf(p) === parent && !names.has(p)) gone(p);
  }, [gone]);

  const loadRoot = useCallback(async () => {
    setRoot((r) => ({ ...r, loading: true, error: null }));
    loadStart();
    try {
      const data = await fetchWorkspaceTree('');
      setRoot({ loading: false, error: null, entries: data.entries, truncated: data.truncated });
      if (!data.truncated) forgetMissing('', data.entries);
    } catch (err) {
      setRoot({ loading: false, error: err.message, entries: [], truncated: false });
    } finally {
      loadEnd();
    }
  }, [loadStart, loadEnd, forgetMissing]);

  const refresh = useCallback(() => {
    setReloadKey((k) => k + 1);
    loadRoot();
  }, [loadRoot]);

  useEffect(() => {
    loadRoot();
    const onChange = (e) => {
      // Otro workspace pasa a ser el activo: este árbol se desmonta (la clave cambia). No se toca su estado guardado, que sigue siendo
      // el de su workspace, ni se recarga contra el workspace nuevo. Alias, fijar u olvidar del mismo workspace sí recargan.
      const target = e.detail?.path;
      if (target && workspacePath && target !== workspacePath) return;
      setCreating(null);
      setRenaming(null);
      refresh();
    };
    window.addEventListener('praxeon:workspace-changed', onChange);
    return () => window.removeEventListener('praxeon:workspace-changed', onChange);
  }, [loadRoot, refresh, workspacePath]);

  // Desplazamiento: se guarda al moverse y se restaura cuando las carpetas abiertas han terminado de cargar (si no, el contenido
  // aún no tiene la altura necesaria). Hasta restaurar no se guarda nada: el navegador recortaría el valor mientras el árbol crece.
  useEffect(() => {
    const el = treeRef.current;
    if (!el || !workspacePath) return undefined;
    const target = initial.scroll;
    let dirty = false;
    let saveTimer = 0;
    let settleTimer = 0;
    let giveUp = 0;
    let observer = null;
    const flush = () => {
      clearTimeout(saveTimer);
      if (!dirty) return;
      dirty = false;
      persist((s) => ({ ...s, scroll: lastScroll.current }));
    };
    const onScroll = () => {
      if (restoring.current) return;
      lastScroll.current = el.scrollTop;
      dirty = true;
      clearTimeout(saveTimer);
      saveTimer = setTimeout(flush, 200);
    };
    const stopWatching = () => {
      clearTimeout(settleTimer);
      clearTimeout(giveUp);
      observer?.disconnect();
      settle.current = null;
      for (const ev of ['wheel', 'pointerdown', 'keydown']) el.removeEventListener(ev, userTookOver);
    };
    const finish = () => {
      restoring.current = false;
      stopWatching();
    };
    function userTookOver() { finish(); }                  // si la persona se mueve antes, no se le quita el control
    const apply = () => {
      el.scrollTop = target;
      lastScroll.current = el.scrollTop;
      finish();
    };
    const schedule = () => {
      clearTimeout(settleTimer);
      settleTimer = setTimeout(() => { if (restoring.current && pending.current === 0) apply(); }, 150);
    };
    el.addEventListener('scroll', onScroll, { passive: true });
    if (restoring.current) {
      settle.current = schedule;
      observer = new MutationObserver(schedule);
      observer.observe(el, { childList: true, subtree: true });
      for (const ev of ['wheel', 'pointerdown', 'keydown']) el.addEventListener(ev, userTookOver, { passive: true });
      giveUp = setTimeout(apply, 4000);
      schedule();
    }
    return () => {
      el.removeEventListener('scroll', onScroll);
      if (!restoring.current) flush();
      stopWatching();
    };
  }, [workspacePath, persist, initial.scroll]);

  const cancel = useCallback(() => {
    setCreating(null);
    setRenaming(null);
  }, []);

  const fail = (err) => notify(err.message || 'No se pudo completar la operación.');

  // Una entrada cambió de ruta: lo recordado dentro de ella (carpetas abiertas, carpeta actual) la sigue
  const afterRename = (from, to) => {
    persist((s) => renamed(s, from, to));
    setCurrentDir((c) => (c ? moveRel(c, from, to) : c));
  };

  // Mueve una entrada del árbol a otra carpeta (arrastrar dentro del explorador)
  const move = async (source, dir) => {
    if (!source || parentOf(source) === dir) return;                       // ya está ahí
    if (dir === source || dir.startsWith(`${source}/`)) {
      fail(new Error('No se puede mover una carpeta dentro de sí misma.'));
      return;
    }
    try {
      const res = await renameWorkspaceEntry(source, joinPath(dir, baseNameOf(source)));
      announce({ action: 'rename', path: res.old_rel_path, newPath: res.rel_path });
      afterRename(res.old_rel_path, res.rel_path);
      refresh();
    } catch (err) {
      fail(err);
    }
  };

  const upload = async (grabbed, dir) => {
    let items;
    try {
      items = await listDropped(grabbed);
    } catch (err) {
      fail(err);
      return;
    }
    if (!items.length) return;
    const total = items.length;
    const failures = [];
    const conflicts = [];
    let done = 0;
    setUploading({ done, total });
    for (const item of items) {
      const path = joinPath(dir, item.rel);
      try {
        if (item.kind === 'dir') await createWorkspaceEntry(path, 'dir');
        else await uploadWorkspaceFile(path, item.file, false);
      } catch (err) {
        if (err.status === 409 && item.kind === 'file') conflicts.push(item);
        else if (!(err.status === 409 && item.kind === 'dir')) failures.push(`${item.rel}: ${err.message}`);
      }
      done += 1;
      setUploading({ done, total });
    }
    if (conflicts.length) {
      const names = conflicts.slice(0, 3).map((c) => `«${c.rel}»`).join(', ') + (conflicts.length > 3 ? ` y ${conflicts.length - 3} más` : '');
      const replace = await confirmDialog(`${conflicts.length === 1 ? 'Ya existe' : 'Ya existen'} ${names} en el destino. ¿Reemplazar${conflicts.length === 1 ? 'lo' : 'los'}?`, { confirmLabel: 'Reemplazar', danger: true });
      if (replace) {
        for (const item of conflicts) {
          try {
            await uploadWorkspaceFile(joinPath(dir, item.rel), item.file, true);
          } catch (err) {
            failures.push(`${item.rel}: ${err.message}`);
          }
        }
      }
    }
    setUploading(null);
    refresh();
    if (failures.length) fail(new Error(`No se pudo subir: ${failures.slice(0, 3).join(' · ')}${failures.length > 3 ? ` (y ${failures.length - 3} más)` : ''}`));
  };

  const ctx = {
    dragDir,
    setDragDir,
    drop: (e, dir) => {
      if (isInternal(e)) {
        const source = e.dataTransfer.getData(ENTRY_MIME);
        setDragDir(null);
        move(source, dir);
        return;
      }
      const grabbed = grabDropped(e.dataTransfer);     // síncrono: dentro del evento
      setDragDir(null);
      upload(grabbed, dir);
    },
    creating,
    renaming,
    isOpen: (rel) => openSet.current.has(rel),
    rememberOpen: (rel, open) => persist((s) => rememberOpen(s, rel, open)),
    forgetMissing,
    loadStart,
    loadEnd,
    gone,
    focusDir: (dir) => {
      setCurrentDir(dir);
      persist((s) => ({ ...s, currentDir: dir }));
    },
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
        afterRename(res.old_rel_path, res.rel_path);
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
        persist((s) => removed(s, entry.rel_path));
        setCurrentDir((c) => (c && isInside(c, entry.rel_path) ? '' : c));
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
      {uploading && <div role="status" style={{ padding: '2px 10px 6px', fontSize: 12, color: 'var(--muted)' }}>Subiendo {uploading.done}/{uploading.total}…</div>}
      <div
        role="tree"
        ref={treeRef}
        style={{ flex: 1, overflowY: 'auto', outline: dragDir === '' ? '2px dashed rgba(110,160,255,.7)' : 'none', outlineOffset: -2 }}
        onDragOver={(e) => { if (accepts(e)) { e.preventDefault(); e.dataTransfer.dropEffect = isInternal(e) ? 'move' : 'copy'; if (dragDir !== '') setDragDir(''); } }}
        onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget)) setDragDir(null); }}
        onDrop={(e) => { if (accepts(e)) { e.preventDefault(); ctx.drop(e, ''); } }}
      >
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
