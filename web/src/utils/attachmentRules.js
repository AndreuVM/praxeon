// Reglas de los adjuntos del chat (FE-005). Funciones puras: qué archivos se aceptan y cómo se muestran.
// El servidor vuelve a comprobarlo todo (tamaño, nombre, ruta): esto solo evita subir lo que ya se sabe que se rechazará.

export const DEFAULT_LIMITS = { max_file_bytes: 100 * 1024 * 1024, max_files: 50 };   // los mismos que el servidor; él manda (fetchAttachmentLimits)

/** «1.2 KB», «3.4 MB»… */
export function formatSize(bytes) {
  const n = Number(bytes) || 0;
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * Reparte los archivos nuevos en aceptados y rechazados (con el motivo). `existing` son los adjuntos que ya tiene el mensaje:
 * los que no han fallado cuentan para el máximo de archivos.
 */
export function checkNewFiles(existing, files, limits = DEFAULT_LIMITS) {
  const accepted = [];
  const rejected = [];
  let count = existing.filter((a) => a.status !== 'error').length;
  for (const file of files) {
    if (!file || typeof file.name !== 'string') continue;
    if (file.size === 0) {
      rejected.push({ name: file.name, reason: 'El archivo está vacío.' });
    } else if (file.size > limits.max_file_bytes) {
      rejected.push({ name: file.name, reason: `Supera el límite de ${formatSize(limits.max_file_bytes)} por archivo (${formatSize(file.size)}).` });
    } else if (count >= limits.max_files) {
      rejected.push({ name: file.name, reason: `Máximo ${limits.max_files} archivos por mensaje.` });
    } else {
      accepted.push(file);
      count += 1;
    }
  }
  return { accepted, rejected };
}

/**
 * Aviso de las imágenes del mensaje según el modelo elegido (FE-006). `capability` es lo que dice el servidor
 * (`{ supported, remote, provider, reason }`) o null si aún no se sabe. Devuelve null si no hay imágenes.
 *   - modelo sin visión: bloquea el envío (nunca se manda una imagen a ciegas ni se ignora en silencio);
 *   - proveedor remoto: avisa de que la imagen sale de este equipo (sin metadatos).
 */
export function visionNotice(items, capability) {
  const images = (items || []).filter((it) => it.kind === 'image' && it.status !== 'error');
  if (images.length === 0) return null;
  if (!capability) return { blocked: false, level: 'info', message: 'Comprobando si el modelo admite imágenes…' };
  if (!capability.supported) return { blocked: true, level: 'error', message: capability.reason || 'Este modelo no admite imágenes.' };
  if (capability.remote) {
    const many = images.length > 1;
    return {
      blocked: false,
      level: 'warn',
      message: `${many ? 'Las imágenes se enviarán' : 'La imagen se enviará'} a ${capability.provider || 'el proveedor'} (servicio remoto): sale${many ? 'n' : ''} de este equipo, sin metadatos (ubicación, fecha ni dispositivo).`,
    };
  }
  return null;
}

/** Archivos de un evento de arrastrar/pegar (vacío si lo soltado no son archivos: texto, enlaces…). */
export function filesFromTransfer(dataTransfer) {
  if (!dataTransfer) return [];
  const fromItems = Array.from(dataTransfer.items || [])
    .filter((item) => item.kind === 'file')
    .map((item) => item.getAsFile())
    .filter(Boolean);
  return fromItems.length > 0 ? fromItems : Array.from(dataTransfer.files || []);
}

/** ¿Lo que se arrastra encima incluye archivos? (para resaltar la zona de soltar solo en ese caso) */
export function hasFiles(dataTransfer) {
  return Array.from(dataTransfer?.types || []).includes('Files');
}
