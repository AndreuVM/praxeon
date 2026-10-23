/**
 * Cliente de archivos del workspace (FE-001 árbol, FE-003 visor/editor).
 * Reutiliza authFetch de api.js para conservar la autenticación.
 */
import { authFetch } from './api';

const BASE = '/v1/workspace';

export class WorkspaceFileError extends Error {
  constructor(message, status) {
    super(message);
    this.name = 'WorkspaceFileError';
    this.status = status;
  }
}

async function call(path, options) {
  const res = await authFetch(`${BASE}${path}`, options);
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      detail = (await res.json()).detail || detail;
    } catch { /* cuerpo no JSON */ }
    throw new WorkspaceFileError(detail, res.status);
  }
  return (await res.json()).data;
}

/** Un nivel de la carpeta `relPath` ('' = raíz del workspace activo). */
export const fetchWorkspaceTree = (relPath = '', showHidden = false) =>
  call(`/tree?path=${encodeURIComponent(relPath)}&show_hidden=${showHidden}`);

/** Contenido de texto + etag; los binarios/PDF devuelven `kind` sin contenido. */
export const readWorkspaceFile = (relPath) => call(`/file?path=${encodeURIComponent(relPath)}`);

/** Guarda; lanza WorkspaceFileError con status 409 si el archivo cambió en disco. */
export const saveWorkspaceFile = (relPath, content, etag = null, encoding = 'utf-8') =>
  call('/file', { method: 'PUT', body: JSON.stringify({ path: relPath, content, etag, encoding }) });

/** Crea un archivo vacío (`kind` 'file') o una carpeta ('dir'). 409 si ya existe. */
export const createWorkspaceEntry = (relPath, kind) =>
  call('/entry', { method: 'POST', body: JSON.stringify({ path: relPath, kind }) });

/** Renombra o mueve dentro del workspace. 409 si el destino ya existe. */
export const renameWorkspaceEntry = (relPath, newPath) =>
  call('/entry', { method: 'PATCH', body: JSON.stringify({ path: relPath, new_path: newPath }) });

/** Elimina un archivo o una carpeta con todo su contenido. */
export const deleteWorkspaceEntry = (relPath) => call(`/entry?path=${encodeURIComponent(relPath)}`, { method: 'DELETE' });

/** Sube un archivo (File/Blob) a `relPath` creando las carpetas que falten. 409 si existe y no se pide `overwrite`. */
export const uploadWorkspaceFile = (relPath, file, overwrite = false) =>
  call(`/upload?path=${encodeURIComponent(relPath)}&overwrite=${overwrite}`, {
    method: 'POST',
    headers: { 'Content-Type': file.type || 'application/octet-stream' },
    body: file,
  });

/** Descarga los bytes (PDF, imágenes) como Blob para previsualizar sin exponer la API key en la URL. */
export async function fetchWorkspaceBlob(relPath) {
  const res = await authFetch(`${BASE}/file/raw?path=${encodeURIComponent(relPath)}`);
  if (!res.ok) throw new WorkspaceFileError(`HTTP ${res.status}`, res.status);
  return await res.blob();
}
