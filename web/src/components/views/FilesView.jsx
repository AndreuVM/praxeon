import React, { useState } from 'react';
import FileTree from '../workspace/FileTree';
import FileViewer from '../workspace/FileViewer';

/** Vista «Archivos»: explorador del workspace activo + visor/editor (FE-001 + FE-003). */
export default function FilesView({ workspace = null }) {
  const [openRequest, setOpenRequest] = useState(null);
  const [selected, setSelected] = useState(null);

  return (
    <div style={{ display: 'flex', flex: 1, minWidth: 0, minHeight: 0 }}>
      <aside style={{ width: 260, minWidth: 180, borderRight: '1px solid var(--line)', display: 'flex', flexDirection: 'column' }}>
        <FileTree
          workspaceName={workspace?.name || ''}
          selectedPath={selected}
          onOpenFile={(path) => {
            setSelected(path);
            setOpenRequest({ path, nonce: Date.now() });
          }}
        />
      </aside>
      <section style={{ flex: 1, minWidth: 0 }}>
        <FileViewer openRequest={openRequest} />
      </section>
    </div>
  );
}
