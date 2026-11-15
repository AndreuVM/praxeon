/**
 * Cliente de los adjuntos del chat (FE-005). El servidor guarda cada archivo en `.praxeon/attachments/<sesión>/` del workspace,
 * sanea el nombre y devuelve la ruta definitiva; la misión recibe después solo esas rutas.
 */
import { authFetch } from './api';
import { DEFAULT_LIMITS } from '../utils/attachmentRules';

const BASE = '/v1/workspace/attachments';

async function failure(res) {
  let detail = `HTTP ${res.status}`;
  try {
    detail = (await res.json()).detail || detail;
  } catch { /* cuerpo no JSON */ }
  return new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
}

/** Límites vigentes del servidor (tamaño por archivo y archivos por mensaje); si no responde, los de por defecto. */
export async function fetchAttachmentLimits() {
  try {
    const res = await authFetch(`${BASE}/limits`);
    if (!res.ok) return DEFAULT_LIMITS;
    const data = (await res.json()).data;
    return { max_file_bytes: data.max_file_bytes || DEFAULT_LIMITS.max_file_bytes, max_files: data.max_files || DEFAULT_LIMITS.max_files };
  } catch {
    return DEFAULT_LIMITS;
  }
}

/** Sube un archivo. Devuelve `{ rel_path, name, size, mime }` con el nombre ya saneado por el servidor. */
export async function uploadAttachment(sessionId, file) {
  const res = await authFetch(`${BASE}?session=${encodeURIComponent(sessionId || '')}&name=${encodeURIComponent(file.name)}`, {
    method: 'POST',
    headers: { 'Content-Type': file.type || 'application/octet-stream' },
    body: file,
  });
  if (!res.ok) throw await failure(res);
  return (await res.json()).data;
}
