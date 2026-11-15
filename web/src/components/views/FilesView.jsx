import React, { useCallback, useEffect, useState } from 'react';
import FileTree from '../workspace/FileTree';
import FileViewer from '../workspace/FileViewer';
import { viewerStore } from '../workspace/viewerInstance';
import { explorerStore, isInside, moveRel } from '../../utils/explorerState';

/** Vista «Archivos»: explorador del workspace activo + visor/editor (FE-001 + FE-003). */
export default function FilesView({ workspace = null }) {
  // La clave es la ruta del workspace: al cambiar de workspace el explorador se monta de nuevo con SU estado recordado
  // (carpetas abiertas, selección, desplazamiento), nunca con el del anterior.
  const path = workspace?.path || '';
  return <FilesPane key={path} workspace={workspace} workspacePath={path} />;
}

function FilesPane({ workspace, workspacePath }) {
  const [selected, setSelected] = useState(() => explorerStore.load(workspacePath).selected);

  // La selección se recuerda por workspace (FE-004): al volver a la vista, o recargar, sigue marcada
  useEffect(() => {
    explorerStore.save(workspacePath, (s) => ({ ...s, selected }));
  }, [workspacePath, selected]);

  // Si el archivo seleccionado se renombra, se mueve o se elimina desde el explorador, la selección lo sigue o se limpia
  useEffect(() => {
    const onEntry = (e) => {
      const { action, path, newPath } = e.detail || {};
      if (!path) return;
      if (action === 'rename' && newPath) setSelected((s) => (s ? moveRel(s, path, newPath) : s));
      else if (action === 'delete') setSelected((s) => (s && isInside(s, path) ? null : s));
    };
    window.addEventListener('praxeon:workspace-entry-changed', onEntry);
    return () => window.removeEventListener('praxeon:workspace-entry-changed', onEntry);
  }, []);

  const forgetSelection = useCallback(() => setSelected(null), []);

  return (
    <div style={{ display: 'flex', flex: 1, minWidth: 0, minHeight: 0 }}>
      <aside style={{ width: 260, minWidth: 180, borderRight: '1px solid var(--line)', display: 'flex', flexDirection: 'column' }}>
        <FileTree
          workspaceName={workspace?.name || ''}
          workspacePath={workspacePath}
          selectedPath={selected}
          onSelectedMissing={forgetSelection}
          onOpenFile={(p) => {
            setSelected(p);
            viewerStore.open(workspacePath, p);
          }}
        />
      </aside>
      <section style={{ flex: 1, minWidth: 0 }}>
        <FileViewer workspacePath={workspacePath} />
      </section>
    </div>
  );
}
