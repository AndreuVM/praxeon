import React, { useEffect, useState } from 'react';
import { fetchWorkspaceBlob } from '../services/workspaceFiles';

/** Imágenes que generó el modelo (FE-015): se leen del workspace con la sesión autenticada y se muestran como <img> (sin metadatos, ya recodificadas). */
function GeneratedImage({ item }) {
  const [state, setState] = useState({ url: null, error: null });
  useEffect(() => {
    let cancelled = false;
    let objectUrl = null;
    fetchWorkspaceBlob(item.rel_path)
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
  }, [item.rel_path]);

  return (
    <figure style={{ margin: 0, display: 'flex', flexDirection: 'column', gap: 4, maxWidth: 320 }}>
      {state.url ? (
        <img src={state.url} alt={item.name} width={item.width || undefined} height={item.height || undefined} style={{ maxWidth: '100%', height: 'auto', borderRadius: 8, border: '1px solid var(--line)' }} />
      ) : (
        <div style={{ width: 160, height: 100, display: 'grid', placeItems: 'center', border: '1px dashed var(--line)', borderRadius: 8, color: 'var(--muted)', fontSize: 12 }}>
          {state.error ? 'No se pudo cargar' : 'Cargando…'}
        </div>
      )}
      <figcaption style={{ fontSize: 'var(--fs-label)', color: 'var(--muted)' }} title={item.rel_path}>
        {item.name}{item.width ? ` · ${item.width}×${item.height}` : ''} · sin metadatos
      </figcaption>
    </figure>
  );
}

export default function GeneratedImages({ items }) {
  if (!items || items.length === 0) return null;
  return (
    <div role="list" aria-label="Imágenes generadas por el modelo" style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
      {items.map((it) => <div role="listitem" key={it.rel_path}><GeneratedImage item={it} /></div>)}
    </div>
  );
}
