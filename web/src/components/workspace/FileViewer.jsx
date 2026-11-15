import React, { Suspense, useCallback, useEffect, useMemo, useState, useSyncExternalStore } from 'react';
import { X, Save, Eye, Code2, Download } from 'lucide-react';
import Markdown from '../Markdown';
import { fetchWorkspaceBlob } from '../../services/workspaceFiles';
import { baseName, isDirty, isSvg } from '../../utils/viewerState';
import { viewerStore } from './viewerInstance';

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

/**
 * Visor/editor de archivos del workspace (FE-003). Editor tipo VS Code (Monaco) con pestañas; muestra imágenes, SVG, PDF,
 * Markdown y HTML. Las pestañas y el texto sin guardar viven en `viewerStore` (por workspace), no en este componente: al cambiar
 * de vista o de workspace siguen ahí (FE-010). Abrir un archivo desde el árbol es `viewerStore.open(workspacePath, ruta)`.
 */
export default function FileViewer({ workspacePath = '' }) {
  const ws = workspacePath;
  useState(() => viewerStore.ensure(ws));            // estado inicial (rutas recordadas) ya en el primer pintado
  const { tabs, active: activePath } = useSyncExternalStore(viewerStore.subscribe, () => viewerStore.snapshot(ws));

  // Al mostrar el visor: reabrir lo que falte y refrescar lo que no esté modificado.
  useEffect(() => { viewerStore.activate(ws); }, [ws]);

  // Una entrada se eliminó o cambió de nombre desde el explorador: las pestañas siguen a la ruta nueva o se cierran.
  useEffect(() => {
    const onEntry = (e) => viewerStore.entryChanged(ws, e.detail);
    window.addEventListener('praxeon:workspace-entry-changed', onEntry);
    return () => window.removeEventListener('praxeon:workspace-entry-changed', onEntry);
  }, [ws]);

  const patch = useCallback((path, changes) => viewerStore.patch(ws, path, changes), [ws]);
  const stableSave = useCallback(() => viewerStore.save(ws), [ws]);
  const setActivePath = useCallback((path) => viewerStore.select(ws, path), [ws]);

  const close = (path) => {
    const t = tabs.find((x) => x.path === path);
    if (t && isDirty(t) && !window.confirm(`«${baseName(path)}» tiene cambios sin guardar. ¿Cerrar y descartarlos?`)) return;
    viewerStore.close(ws, path);
  };

  const dirty = isDirty;
  const tab = tabs.find((t) => t.path === activePath) || null;
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
              <button type="button" onClick={() => viewerStore.reload(ws, tab.path)}>Recargar (descarta tus cambios)</button>
              <button type="button" onClick={() => viewerStore.save(ws, true)}>Sobrescribir</button>
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
