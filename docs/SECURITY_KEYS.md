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

## Acceso local: `Host` de confianza y token por arranque

- **Validación de `Host`** (defensa frente a DNS-rebinding): el servidor solo atiende peticiones HTTP y WebSocket cuya cabecera `Host` sea
  `localhost`, `127.0.0.1` o `[::1]`. Una web ajena que resuelva su dominio a 127.0.0.1 recibe un `400`. Para atender otros nombres define
  `PRAXEON_ALLOWED_HOSTS` (separados por comas; admite `*.midominio.com`). Vincular a una interfaz de red (`--host 0.0.0.0`) exige
  `PRAXEON_API_KEY` **y** `PRAXEON_ALLOWED_HOSTS`.
- **Token por arranque**: `praxeon-server` y `praxeon-web` sin `PRAXEON_API_KEY` generan un token aleatorio en cada ejecución (como la app de
  escritorio) y lo muestran en una URL con `#token=…`; `praxeon-web` abre el navegador con ella. Para el modo antiguo (loopback sin
  autenticación) define `PRAXEON_ALLOW_INSECURE_DEV=1`.
