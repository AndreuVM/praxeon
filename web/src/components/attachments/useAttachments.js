import { useCallback, useLayoutEffect, useRef, useState } from 'react';
import { deleteWorkspaceEntry } from '../../services/workspaceFiles';
import { fetchAttachmentLimits, uploadAttachment } from '../../services/attachments';
import { DEFAULT_LIMITS, checkNewFiles } from '../../utils/attachmentRules';

let counter = 0;
const PARALLEL_UPLOADS = 3;                      // con hasta 50 archivos de 100 MB, subirlos todos a la vez agotaría la memoria

/**
 * Adjuntos del mensaje que se está escribiendo (FE-005). Cada archivo se sube nada más añadirlo, así un error de tamaño o de
 * servidor se ve enseguida en su ficha. Estados de una ficha: `uploading`, `ready` (ya está en el workspace) y `error`.
 */
export default function useAttachments(sessionId) {
  const [items, setItems] = useState([]);
  const itemsRef = useRef([]);                       // la lista vigente, para decidir sobre ella sin esperar a un nuevo render
  const limits = useRef(null);
  const session = useRef(sessionId);
  useLayoutEffect(() => { session.current = sessionId; });   // la sesión vigente en cada subida (cambia al abrir una conversación nueva)

  const update = useCallback((fn) => {
    itemsRef.current = fn(itemsRef.current);
    setItems(itemsRef.current);
  }, []);
  const patch = useCallback((id, change) => update((list) => list.map((it) => (it.id === id ? { ...it, ...change } : it))), [update]);

  const addFiles = useCallback(async (fileList) => {
    const files = Array.from(fileList || []);
    if (files.length === 0) return;
    if (!limits.current) limits.current = fetchAttachmentLimits();
    const lim = await limits.current.catch(() => DEFAULT_LIMITS);
    const { accepted, rejected } = checkNewFiles(itemsRef.current, files, lim);
    const toUpload = accepted.map((file) => ({ id: `att-${++counter}`, file }));
    update((list) => [
      ...list,
      ...rejected.map((r) => ({ id: `att-${++counter}`, name: r.name, size: 0, status: 'error', error: r.reason })),
      ...toUpload.map(({ id, file }) => ({ id, name: file.name, size: file.size, status: 'uploading' })),
    ]);
    const queue = [...toUpload];
    const worker = async () => {
      for (let next = queue.shift(); next; next = queue.shift()) {
        try {
          const saved = await uploadAttachment(session.current, next.file);
          patch(next.id, { status: 'ready', name: saved.name, size: saved.size, rel_path: saved.rel_path, mime: saved.mime });
        } catch (err) {
          patch(next.id, { status: 'error', error: err.message || 'No se pudo subir el archivo.' });
        }
      }
    };
    await Promise.all(Array.from({ length: Math.min(PARALLEL_UPLOADS, queue.length) }, worker));
  }, [patch, update]);

  /** Quita la ficha; si el archivo ya estaba subido y no se envió, también se borra del workspace (mejor esfuerzo). */
  const remove = useCallback((id) => {
    const gone = itemsRef.current.find((it) => it.id === id);
    if (gone?.status === 'ready' && gone.rel_path) deleteWorkspaceEntry(gone.rel_path).catch(() => {});
    update((list) => list.filter((it) => it.id !== id));
  }, [update]);

  /** Vacía las fichas tras enviar el mensaje (los archivos se quedan en el workspace: la misión los referencia). */
  const clear = useCallback(() => update(() => []), [update]);

  return {
    items,
    addFiles,
    remove,
    clear,
    uploading: items.some((it) => it.status === 'uploading'),
    ready: items.filter((it) => it.status === 'ready'),
  };
}
