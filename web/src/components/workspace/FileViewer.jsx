import React, { Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { X, Save, Eye, Code2, Download } from 'lucide-react';
import Markdown from '../Markdown';
import { fetchWorkspace } from '../../services/api';
import { notify } from '../Dialogs';
import { readWorkspaceFile, saveWorkspaceFile, fetchWorkspaceBlob } from '../../services/workspaceFiles';

// Monaco va en su propio chunk (code splitting, AUDIT-P2): solo se descarga al abrir un archivo.
const MonacoEditor = React.lazy(() => import('./MonacoEditor'));

const LANG = {
  js: 'javascript', jsx: 'javascript', mjs: 'javascript', ts: 'typescript', tsx: 'typescript', json: 'json',
  py: 'python', md: 'markdown', html: 'html', htm: 'html', css: 'css', scss: 'scss', yml: 'yaml', yaml: 'yaml',
  toml: 'ini', ini: 'ini', sh: 'shell', ps1: 'powershell', sql: 'sql', xml: 'xml', svg: 'xml', rs: 'rust', go: 'go',
  java: 'java', c: 'c', h: 'c', cpp: 'cpp', cs: 'csharp', rb: 'ruby', php: 'php', txt: 'plaintext',
};
const extOf = (p) => (p.split('.').pop() || '').toLowerCase();
const langOf = (p) => LANG[extOf(p)] || 'plaintext';
const baseName = (p) => p.split('/').pop();
const isSvg = (p) => extOf(p) === 'svg';

const bar = { display: 'flex', alignItems: 'center', gap: 8, padding: '4px 10px', borderBottom: '1px solid var(--line)', fontSize: 12 };
const toolBtn = { display: 'flex', gap: 4, alignItems: 'center' };

/** Descarga los bytes autenticados y los expone como URL local (no se pone la API key en ninguna URL). */
function useBlobUrl(path) {
  const [state, setState] = useState({ url: null, error: null });
  useEffect(() => {
    let cancelled = false;
    let objectUrl = null;
    setState({ url: null, error: null });
    fetchWorkspaceBlob(path)
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setState({ url: objectUrl, error: null });
      })
      .catch((e) => !cancelled && setState({ url: null, error: e.message }));
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [path]);
  return state;
}

function DownloadLink({ url, name }) {
  if (!url) return null;
  return (
    <a href={url} download={name} style={{ ...toolBtn, color: 'inherit', textDecoration: 'none' }} title="Descargar">
      <Download size={13} /> Descargar
    </a>
  );
}

function PdfPreview({ path }) {
  const { url, error } = useBlobUrl(path);
  if (error) return <div style={{ padding: 16, color: 'var(--bad, #e5484d)' }}>{error}</div>;
  if (!url) return <div style={{ padding: 16, color: 'var(--muted)' }}>Cargando PDF…</div>;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{ ...bar, justifyContent: 'flex-end' }}><DownloadLink url={url} name={baseName(path)} /></div>
      <iframe title={path} src={url} style={{ border: 0, width: '100%', flex: 1, minHeight: 0 }} />
    </div>
  );
}

/** Imagen o SVG mostrado con <img>: así un SVG no ejecuta scripts ni accede a la página. */
function ImageView({ src, name }) {
  const [size, setSize] = useState(null);
  const [broken, setBroken] = useState(false);
  useEffect(() => { setBroken(false); setSize(null); }, [src]);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{ ...bar, justifyContent: 'space-between' }}>
        <span style={{ color: 'var(--muted)' }}>{size ? `${size.w} × ${size.h} px` : ''}</span>
        <DownloadLink url={src} name={name} />
      </div>
      <div style={{ flex: 1, minHeight: 0, overflow: 'auto', display: 'grid', placeItems: 'center', padding: 16, background: 'repeating-conic-gradient(rgba(127,127,127,.12) 0% 25%, transparent 0% 50%) 50% / 16px 16px' }}>
        {broken
          ? <span style={{ color: 'var(--bad, #e5484d)' }}>No se pudo mostrar la imagen.</span>
          : <img src={src} alt={name} onError={() => setBroken(true)} onLoad={(e) => setSize({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })} style={{ maxWidth: '100%', maxHeight: '100%', objectFit: 'contain' }} />}
      </div>
    </div>
  );
}

function ImagePreview({ path }) {
  const { url, error } = useBlobUrl(path);
  if (error) return <div style={{ padding: 16, color: 'var(--bad, #e5484d)' }}>{error}</div>;
  if (!url) return <div style={{ padding: 16, color: 'var(--muted)' }}>Cargando imagen…</div>;
  return <ImageView src={url} name={baseName(path)} />;
}

/** Vista previa del SVG que se está editando (con los cambios sin guardar). */
function SvgPreview({ path, content }) {
  const url = useMemo(() => URL.createObjectURL(new Blob([content], { type: 'image/svg+xml' })), [content]);
  useEffect(() => () => URL.revokeObjectURL(url), [url]);
  return <ImageView src={url} name={baseName(path)} />;
}

function BinaryView({ tab }) {
  const { url } = useBlobUrl(tab.path);
  return (
    <div style={{ padding: 16, color: 'var(--muted)', display: 'flex', flexDirection: 'column', gap: 8, alignItems: 'flex-start' }}>
      <span>Archivo binario ({tab.mime || 'desconocido'}): no se puede mostrar como texto.</span>
      <DownloadLink url={url} name={baseName(tab.path)} />
    </div>
  );
}

const under = (p, base) => p === base || p.startsWith(`${base}/`);

/**
 * Visor/editor de archivos del workspace (FE-003). Editor tipo VS Code (Monaco) con pestañas; muestra imágenes, SVG, PDF,
 * Markdown y HTML. `openRequest` = { path, nonce } pide abrir un archivo desde el árbol.
 */
export default function FileViewer({ openRequest = null }) {
  const [tabs, setTabs] = useState([]); // {path, kind, content, original, etag, encoding, error, conflict, mode}
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
    setTabs((ts) => [...ts, { path, kind: 'loading', content: '', original: '', etag: null, encoding: 'utf-8', error: null, conflict: false, mode: isSvg(path) ? 'preview' : 'edit' }]);
    try {
      const info = await readWorkspaceFile(path);
      patch(path, { kind: info.kind, content: info.content ?? '', original: info.content ?? '', etag: info.etag, encoding: info.encoding || 'utf-8', mime: info.mime, size: info.size });
    } catch (err) {
      patch(path, { kind: 'error', error: err.message });
    }
  }, [patch]);

  useEffect(() => {
    if (openRequest?.path) open(openRequest.path);
  }, [openRequest, open]);

  // Una entrada se eliminó o cambió de nombre desde el explorador: las pestañas siguen a la ruta nueva o se cierran.
  useEffect(() => {
    const onEntry = (e) => {
      const { action, path, newPath } = e.detail || {};
      if (!path) return;
      if (action === 'delete') {
        const keep = tabsRef.current.filter((t) => !under(t.path, path));
        setTabs(keep);
        if (activeRef.current && under(activeRef.current, path)) setActivePath(keep.length ? keep[keep.length - 1].path : null);
      } else if (action === 'rename' && newPath) {
        const move = (p) => (under(p, path) ? newPath + p.slice(path.length) : p);
        setTabs((ts) => ts.map((t) => ({ ...t, path: move(t.path) })));
        if (activeRef.current) setActivePath(move(activeRef.current));
      }
    };
    window.addEventListener('praxeon:workspace-entry-changed', onEntry);
    return () => window.removeEventListener('praxeon:workspace-entry-changed', onEntry);
  }, []);

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
      const res = await saveWorkspaceFile(tab.path, tab.content, force ? null : tab.etag, tab.encoding);
      patch(tab.path, { original: tab.content, etag: res.etag, encoding: res.encoding || tab.encoding, conflict: false, error: null });
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
      patch(path, { content: info.content ?? '', original: info.content ?? '', etag: info.etag, encoding: info.encoding || 'utf-8', conflict: false, error: null });
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
  const svg = tab ? isSvg(tab.path) : false;
  const previewable = tab && tab.kind === 'text' && (lang === 'markdown' || lang === 'html' || svg);

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
            {tab.kind === 'text' && tab.encoding && tab.encoding !== 'utf-8' && (
              <span style={{ color: 'var(--muted)' }} title="Codificación del archivo (se conserva al guardar)">{tab.encoding}</span>
            )}
            {previewable && (
              <button type="button" onClick={() => patch(tab.path, { mode: tab.mode === 'edit' ? 'preview' : 'edit' })} style={toolBtn}>
                {tab.mode === 'edit' ? <><Eye size={13} /> Vista previa</> : <><Code2 size={13} /> Editar</>}
              </button>
            )}
            {tab.kind === 'text' && (
              <button type="button" disabled={!dirty(tab)} onClick={stableSave} style={toolBtn} title="Guardar (Ctrl+S)">
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
            {tab.kind === 'binary' && <BinaryView tab={tab} />}
            {tab.kind === 'pdf' && <PdfPreview path={tab.path} />}
            {tab.kind === 'image' && <ImagePreview path={tab.path} />}
            {tab.kind === 'text' && tab.mode === 'edit' && (
              <Suspense fallback={<div style={{ padding: 16, color: 'var(--muted)' }}>Cargando editor…</div>}>
                <MonacoEditor path={tab.path} language={lang} value={tab.content} onChange={(v) => patch(tab.path, { content: v })} onSave={stableSave} />
              </Suspense>
            )}
            {tab.kind === 'text' && tab.mode === 'preview' && svg && <SvgPreview path={tab.path} content={tab.content} />}
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
