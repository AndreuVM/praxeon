# Claves de PRAXEON

Cada función usa su propia clave (auditoría S-03): conocer una no permite forjar la otra.

| Variable | Para qué sirve | Quién la presenta |
|---|---|---|
| `PRAXEON_API_KEY` | Credencial de los clientes (cabecera `X-API-Key`, UI, SDK). Obligatoria para exponer el servidor en red. | Clientes |
| `PRAXEON_JWT_SECRET` | Firma de los tokens JWT. | Nadie (solo el servidor) |
| `PRAXEON_RECEIPT_KEY` | Firma HMAC de los recibos de capability. | Nadie (solo el servidor) |
| `PRAXEON_OPERATOR_KEY` | Token de operador para aprobar decisiones y autorizar `full_access` por API (si no existe, vale `PRAXEON_API_KEY`). | Operador |
| `PRAXEON_SECRET_KEY` | Secreto **maestro** opcional: de él se derivan (HKDF-SHA256) las claves de JWT y de recibos que no se hayan definido. **No es una credencial de cliente.** | Nadie |

Sin ninguna clave de firma, el servidor genera claves aleatorias por proceso (los tokens dejan de valer al reiniciar).
Perfil `production`: hace falta `PRAXEON_SECRET_KEY` (≥ 32 caracteres) o bien `PRAXEON_JWT_SECRET` y `PRAXEON_RECEIPT_KEY`.

## Migración desde versiones anteriores

Antes `PRAXEON_SECRET_KEY` también se aceptaba como API key y como token de operador. Ya no:

- Si usabas `PRAXEON_SECRET_KEY` como clave de acceso, define `PRAXEON_API_KEY` y úsala en su lugar.
- Si aprobabas decisiones con ese valor como `operator_token`, define `PRAXEON_OPERATOR_KEY` (o usa la API key).
- Los JWT emitidos antes del cambio dejan de ser válidos (la clave de firma es ahora una subclave): hay que volver a iniciar sesión.
- La app de escritorio no necesita cambios: ya genera una API key y un secreto efímeros en cada arranque.
