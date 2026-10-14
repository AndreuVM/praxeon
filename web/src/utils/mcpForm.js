// Variables de entorno del formulario de servidores MCP: «CLAVE=valor» por línea.
// La API nunca devuelve los valores guardados; al editar, una clave existente con valor vacío significa «conservar» (null).
export function parseEnv(text, existingKeys = []) {
  const env = {};
  for (const raw of String(text || '').split('\n')) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    const eq = line.indexOf('=');
    const key = (eq === -1 ? line : line.slice(0, eq)).trim();
    const value = eq === -1 ? '' : line.slice(eq + 1);
    if (!key) continue;
    env[key] = value === '' && existingKeys.includes(key) ? null : value;
  }
  return env;
}
