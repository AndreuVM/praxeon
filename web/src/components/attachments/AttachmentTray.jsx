import React from 'react';
import { AlertTriangle, FileText, LoaderCircle, X } from 'lucide-react';
import { formatSize } from '../../utils/attachmentRules';

/** Fichas de los archivos adjuntos: nombre, tamaño y estado; con `onRemove` se pueden quitar. */
export default function AttachmentTray({ items, onRemove, label = 'Archivos adjuntos' }) {
  if (!items || items.length === 0) return null;
  return (
    <div role="list" aria-label={label} style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
      {items.map((it) => {
        const failed = it.status === 'error';
        return (
          <span
            key={it.id || it.rel_path || it.name}
            role="listitem"
            title={failed ? it.error : it.rel_path || it.name}
            style={{
              display: 'inline-flex', alignItems: 'center', gap: 6, maxWidth: '100%', padding: '3px 8px', fontSize: 12, borderRadius: 8,
              border: `1px solid ${failed ? 'var(--bad, #e5484d)' : 'var(--line)'}`, background: failed ? 'rgba(229,72,77,.10)' : 'var(--bg)',
            }}
          >
            {it.preview && !failed ? <img src={it.preview} alt="" width={22} height={22} style={{ objectFit: 'cover', borderRadius: 4 }} /> : failed ? <AlertTriangle size={12} /> : it.status === 'uploading' ? <LoaderCircle size={12} className="spin" /> : <FileText size={12} />}
            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 220 }}>{it.name}</span>
            {failed ? (
              <span style={{ color: 'var(--bad, #e5484d)' }}>{it.error}</span>
            ) : (
              <span style={{ color: 'var(--muted)' }} title={it.image?.stripped ? 'Se han eliminado los metadatos de la imagen (ubicación, fecha, dispositivo)' : undefined}>
                {it.status === 'uploading' ? 'Subiendo…' : it.image ? `${it.image.width}×${it.image.height}${it.image.stripped ? ' · sin metadatos' : ''}` : formatSize(it.size)}
              </span>
            )}
            {onRemove && (
              <button type="button" className="btn-icon" aria-label={`Quitar ${it.name}`} title="Quitar" onClick={() => onRemove(it.id)} style={{ padding: 0, width: 16, height: 16 }}>
                <X size={11} />
              </button>
            )}
          </span>
        );
      })}
    </div>
  );
}
