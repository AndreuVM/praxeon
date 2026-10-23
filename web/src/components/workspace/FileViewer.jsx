import React, { Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { X, Save, Eye, Code2 } from 'lucide-react';
import Markdown from '../Markdown';
import { fetchWorkspace } from '../../services/api';
import { notify } from '../Dialogs';
import { readWorkspaceFile, saveWorkspaceFile, fetchWorkspaceBlob } from '../../services/workspaceFiles';

// Monaco va en su propio chunk (code splitting, AUDIT-P2): solo se descarga al abrir un archivo.
const MonacoEditor = React.lazy(() => import('./MonacoEditor'));

const LANG = {
  js: 'javascript', jsx: 'javascript', mjs: 'javascript', ts: 'typescript', tsx: 'typescript', json: 'json',
  py: 'python', md: 'markdown', html: 'html', htm: 'html', css: 'css', scss: 'scss', yml: 'yaml', yaml: 'yaml',
  toml: 'ini', ini: 'ini', sh: 'shell', ps1: 'powershell', sql: 'sql', xml: 'xml', rs: 'rust', go: 'go',
  java: 'java', c: 'c', h: 'c', cpp: 'cpp', cs: 'csharp', rb: 'ruby', php: 'php', txt: 'plaintext',
};
const langOf = (p) => LANG[(p.split('.').pop() || '').toLowerCase()] || 'plaintext';
const baseName = (p) => p.split('/').pop();

const bar = { display: 'flex', alignItems: 'center', gap: 8, padding: '4px 10px', borderBottom: '1px solid var(--line)', fontSize: 12 };

function PdfPreview({ path }) {
  const [url, setUrl] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    let revoked = false;
    let objectUrl = null;
    fetchWorkspaceBlob(path)
      .then((blob) => {
        if (revoked) return;
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
      })
      .catch((e) => !revoked && setError(e.message));
    return () => {
      revoked = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [path]);
  if (error) return <div style={{ padding: 16, color: 'var(--bad, #e5484d)' }}>{error}</div>;
  if (!url) return <div style={{ padding: 16, color: 'var(--muted)' }}>Cargando PDF…</div>;
  return <iframe title={path} src={url} style={{ border: 0, width: '100%', height: '100%' }} />;
}

/**
 * Visor/editor de archivos del workspace (FE-003). Editor tipo VS Code (Monaco) con pestañas.
 * `openRequest` = { path, nonce } pide abrir un archivo desde el árbol.
 */
export default function FileViewer({ openRequest = null }) {
  const [tabs, setTabs] = useState([]); // {path, kind, content, original, etag, error, conflict, mode}
  const [activePath, setActivePath] = useState(null);
  const tabsRef = useRef(tabs);
  tabsRef.current = tabs;
  const activeRef = useRef(activePath);
  activeRef.current = activePath;

  const patch = useCallback((path, changes) => {
    setTabs((ts) => ts.map((t) => (t.path === path ? { ...t, ...changes } : t)));
  }, []);

  const open = useCallback(async (path) => {
    setActivePath(path);
    if (tabsRef.current.some((t) => t.path === path)) return;
    setTabs((ts) => [...ts, { path, kind: 'loading', content: '', original: '', etag: null, error: null, conflict: false, mode: 'edit' }]);
    try {
      const info = await readWorkspaceFile(path);
      patch(path, { kind: info.kind, content: info.content ?? '', original: info.content ?? '', etag: info.etag, mime: info.mime, size: info.size });
    } catch (err) {
      patch(path, { kind: 'error', error: err.message });
    }
  }, [patch]);

  useEffect(() => {
    if (openRequest?.path) open(openRequest.path);
  }, [openRequest, open]);

  // Al cambiar de workspace las pestañas abiertas dejan de corresponder a él: guardar una de ellas escribiría la misma ruta
  // relativa en el workspace nuevo. Se cierran todas y se avisa de lo que no estaba guardado (FE-002 ↔ FE-003).
  const workspacePathRef = useRef(null);
  useEffect(() => {
    let alive = true;
    fetchWorkspace().then((w) => { if (alive && workspacePathRef.current === null) workspacePathRef.current = w?.path ?? ''; }).catch(() => {});
    const onChange = (e) => {
      const next = e.detail?.path;
      if (next === undefined || workspacePathRef.current === null || next === workspacePathRef.current) return;
      workspacePathRef.current = next;
      const unsaved = tabsRef.current.filter((t) => t.kind === 'text' && t.content !== t.original).length;
      setTabs([]);
      setActivePath(null);
      if (unsaved) notify(`Se cerraron ${unsaved} archivo${unsaved === 1 ? '' : 's'} con cambios sin guardar al cambiar de workspace.`);
    };
    window.addEventListener('praxeon:workspace-changed', onChange);
    return () => { alive = false; window.removeEventListener('praxeon:workspace-changed', onChange); };
  }, []);

  const save = useCallback(async (force = false) => {
    const tab = tabsRef.current.find((t) => t.path === activeRef.current);
    if (!tab || tab.kind !== 'text' || tab.content === tab.original) return;
    try {
      const res = await saveWorkspaceFile(tab.path, tab.content, force ? null : tab.etag);
      patch(tab.path, { original: tab.content, etag: res.etag, conflict: false, error: null });
    } catch (err) {
      if (err.status === 409) patch(tab.path, { conflict: true, error: null });
      else patch(tab.path, { error: err.message });
    }
  }, [patch]);
  const saveRef = useRef(save);
  saveRef.current = save;
  const stableSave = useCallback(() => saveRef.current(), []);

  const reload = async (path) => {
    try {
      const info = await readWorkspaceFile(path);
      patch(path, { content: info.content ?? '', original: info.content ?? '', etag: info.etag, conflict: false, error: null });
    } catch (err) {
      patch(path, { error: err.message });
    }
  };

  const close = (path) => {
    const tab = tabsRef.current.find((t) => t.path === path);
    if (tab && tab.content !== tab.original && !window.confirm(`«${baseName(path)}» tiene cambios sin guardar. ¿Cerrar y descartarlos?`)) return;
    setTabs((ts) => ts.filter((t) => t.path !== path));
    if (activeRef.current === path) {
      const rest = tabsRef.current.filter((t) => t.path !== path);
      setActivePath(rest.length ? rest[rest.length - 1].path : null);
    }
  };

  const tab = tabs.find((t) => t.path === activePath) || null;
  const dirty = (t) => t.kind === 'text' && t.content !== t.original;
  const lang = tab ? langOf(tab.path) : 'plaintext';
  const previewable = tab && tab.kind === 'text' && (lang === 'markdown' || lang === 'html');

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minWidth: 0 }}>
      <div role="tablist" style={{ display: 'flex', overflowX: 'auto', borderBottom: '1px solid var(--line)' }}>
        {tabs.map((t) => (
          <div key={t.path} role="tab" aria-selected={t.path === activePath} onClick={() => setActivePath(t.path)}
            style={{ ...bar, borderBottom: 0, cursor: 'pointer', background: t.path === activePath ? 'var(--surface)' : 'transparent', whiteSpace: 'nowrap' }} title={t.path}>
            <span>{baseName(t.path)}{dirty(t) ? ' ●' : ''}</span>
            <button type="button" aria-label={`Cerrar ${baseName(t.path)}`} onClick={(e) => { e.stopPropagation(); close(t.path); }} style={{ background: 'none', border: 0, color: 'inherit', cursor: 'pointer' }}>
              <X size={12} />
            </button>
          </div>
        ))}
      </div>

      {!tab && <div style={{ flex: 1, display: 'grid', placeItems: 'center', color: 'var(--muted)' }}>Elige un archivo del explorador</div>}

      {tab && (
        <>
          <div style={bar}>
            <span style={{ color: 'var(--muted)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis' }}>{tab.path}</span>
            {previewable && (
              <button type="button" onClick={() => patch(tab.path, { mode: tab.mode === 'edit' ? 'preview' : 'edit' })} style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                {tab.mode === 'edit' ? <><Eye size={13} /> Vista previa</> : <><Code2 size={13} /> Editar</>}
              </button>
            )}
            {tab.kind === 'text' && (
              <button type="button" disabled={!dirty(tab)} onClick={stableSave} style={{ display: 'flex', gap: 4, alignItems: 'center' }} title="Guardar (Ctrl+S)">
                <Save size={13} /> Guardar
              </button>
            )}
          </div>
          {tab.conflict && (
            <div role="alert" style={{ ...bar, background: 'rgba(229,72,77,.15)' }}>
              <span style={{ flex: 1 }}>El archivo cambió en disco desde que lo abriste.</span>
              <button type="button" onClick={() => reload(tab.path)}>Recargar (descarta tus cambios)</button>
              <button type="button" onClick={() => save(true)}>Sobrescribir</button>
            </div>
          )}
          {tab.error && <div role="alert" style={{ ...bar, color: 'var(--bad, #e5484d)' }}>{tab.error}</div>}

          <div style={{ flex: 1, minHeight: 0 }}>
            {tab.kind === 'loading' && <div style={{ padding: 16, color: 'var(--muted)' }}>Cargando…</div>}
            {tab.kind === 'binary' && <div style={{ padding: 16, color: 'var(--muted)' }}>Archivo binario ({tab.mime || 'desconocido'}): no se puede mostrar como texto.</div>}
            {tab.kind === 'pdf' && <PdfPreview path={tab.path} />}
            {tab.kind === 'text' && tab.mode === 'edit' && (
              <Suspense fallback={<div style={{ padding: 16, color: 'var(--muted)' }}>Cargando editor…</div>}>
                <MonacoEditor path={tab.path} language={lang} value={tab.content} onChange={(v) => patch(tab.path, { content: v })} onSave={stableSave} />
              </Suspense>
            )}
            {tab.kind === 'text' && tab.mode === 'preview' && lang === 'markdown' && (
              <div style={{ padding: 16, overflowY: 'auto', height: '100%' }}><Markdown text={tab.content} /></div>
            )}
            {tab.kind === 'text' && tab.mode === 'preview' && lang === 'html' && (
              // Sin allow-same-origin: el HTML se ejecuta en un origen opaco y no puede llamar a la API de la app.
              <iframe title={tab.path} sandbox="allow-scripts" srcDoc={tab.content} style={{ border: 0, width: '100%', height: '100%', background: '#fff' }} />
            )}
          </div>
        </>
      )}
    </div>
  );
}
