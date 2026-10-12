import React, { useState } from 'react';
import { FolderOpen } from 'lucide-react';
import WorkspacePicker from './WorkspacePicker';
import useWorkspace, { SOURCE_LABELS } from './useWorkspace';

/**
 * Campo de workspace para una tarea o sesión. Vacío = usa el workspace del proyecto (se muestra
 * la ruta efectiva, nunca un campo en blanco). Con valor = solo para esta tarea.
 *   value / onChange(path | '')  —  texto de la ruta propia de la tarea
 *   global={true}                —  edita el workspace del proyecto en lugar de un valor propio
 */
export default function WorkspaceField({ value = '', onChange, disabled = false, global = false, compact = false }) {
  const { workspace, choose, forget } = useWorkspace();
  const [open, setOpen] = useState(false);

  const own = !global && value ? value : '';
  const effectivePath = own || workspace?.path || '…';
  const name = effectivePath.split(/[\\/]/).filter(Boolean).pop() || effectivePath;
  const badge = global
    ? SOURCE_LABELS[workspace?.source] || ''
    : own
      ? 'Solo para esta tarea'
      : `Workspace del proyecto · ${SOURCE_LABELS[workspace?.source] || ''}`.replace(/ · $/, '');

  const onSelect = async (path) => {
    if (global) await choose(path);
    else onChange(path);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', backgroundColor: '#0a0e16', border: '1px solid #1e293b', borderRadius: '6px', padding: compact ? '5px 8px' : '7px 10px', minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px', minWidth: 0 }}>
          <FolderOpen size={14} style={{ color: own ? '#eab308' : '#58a6ff', flexShrink: 0 }} />
          <div title={effectivePath} style={{ flex: 1, minWidth: 0, fontSize: compact ? '11px' : '12px', fontWeight: 600, color: '#f0f6fc', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{name}</div>
          <button
            type="button"
            disabled={disabled}
            onClick={() => setOpen(true)}
            style={{ flexShrink: 0, padding: '3px 9px', borderRadius: '5px', border: '1px solid #2b6cb0', backgroundColor: 'rgba(56,139,253,0.12)', color: '#58a6ff', fontSize: '11px', fontWeight: 600, cursor: disabled ? 'not-allowed' : 'pointer' }}
          >
            Cambiar
          </button>
          {own && (
            <button
              type="button"
              disabled={disabled}
              onClick={() => onChange('')}
              title="Volver al workspace del proyecto"
              style={{ flexShrink: 0, padding: '3px 8px', borderRadius: '5px', border: '1px solid #243044', backgroundColor: 'transparent', color: '#94a3b8', fontSize: '11px', cursor: 'pointer' }}
            >
              Quitar
            </button>
          )}
        </div>
        <div
          title={effectivePath}
          style={{ fontFamily: 'var(--font-mono)', fontSize: '10.5px', color: '#8b949e', wordBreak: 'break-all', lineHeight: 1.35, display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}
        >
          {effectivePath}
        </div>
        {badge && <div style={{ fontSize: '9.5px', color: own ? '#eab308' : '#58a6ff' }}>{badge}</div>}
      </div>
      <WorkspacePicker
        open={open}
        title={global ? 'Workspace del proyecto' : 'Workspace de esta tarea'}
        initialPath={own || workspace?.path || ''}
        recent={workspace?.recent || []}
        onSelect={onSelect}
        onClose={() => setOpen(false)}
        onForget={forget}
      />
    </div>
  );
}
