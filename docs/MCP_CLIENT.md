# PRAXEON como cliente MCP

PRAXEON puede usar las herramientas de servidores [MCP](https://modelcontextprotocol.io) dentro de las misiones. Cada
herramienta pasa por el mismo circuito supervisado que las nativas: política → capability firmado → ejecución →
redacción de secretos. (El servidor MCP de PRAXEON, que expone su supervisión a otros agentes, es `praxeon-mcp`.)

## Cómo se añade un servidor

Todo se puede hacer desde la pestaña **Tools (MCP)** de la interfaz: «Añadir servidor», «Activar y revisar» (ves las herramientas
con su firma y sus pistas), «Aprobar», y por herramienta riesgo / solo lectura / confirmación / desactivar. Al editar un agente,
las herramientas MCP aprobadas aparecen como botones para añadirlas a sus herramientas permitidas. Por debajo es la API:

Un servidor MCP es un programa de terceros que se ejecuta con tus permisos. Por eso el alta tiene tres pasos:

1. **Alta** (queda desactivado):
   `PUT /v1/mcp/servers/{nombre}` con `command`, `args`, `env`, `cwd`, `trust_hints`, `timeout_s`.
   El nombre es minúsculas, dígitos y `_` (sin `__`).
2. **Activar y revisar**: `POST /v1/mcp/servers/{nombre}/enable` arranca el servidor y lista sus herramientas
   (`tools`, con su `signature`, anotaciones y `tools_hash`). Aún **no** se exponen al agente (`pending_approval`).
3. **Aprobar**: `POST /v1/mcp/servers/{nombre}/approve` con el `tools_hash` que revisaste. Si el servidor cambió sus
   herramientas entre tanto, responde 409.

Los servidores se guardan en `<caché>/mcp.json`. Si cambias qué se ejecuta (comando, argumentos, entorno, carpeta), el
servidor se desactiva y hay que volver a aprobarlo.

```json
{ "command": "uvx", "args": ["mcp-server-time"], "env": { "API_KEY": "${MI_API_KEY}" } }
```

## Servidores remotos (URL)

Además de programas locales (`command` + `args`, por stdio), se pueden usar servidores remotos con una `url`:

```json
{ "url": "https://servidor.ejemplo.com/mcp/sse?token=${MI_TOKEN}", "headers": { "X-Api-Key": "${OTRA_CLAVE}" } }
```

- **Transportes**: SSE clásico y HTTP «streamable». Con `transport: "auto"` (por defecto) una URL acabada en `/sse` es SSE; en otro caso
  se prueba streamable y, si el servidor no lo entiende, se retrocede a SSE.
- **El token, en una variable**: escribe `${VARIABLE}` en la URL o las cabeceras y define la variable en el entorno de PRAXEON o en su
  `.env`. Así `mcp.json` no guarda el secreto. Si falta la variable, el error la nombra sin mostrar ningún valor.
- **La URL no se muestra**: la API y la interfaz devuelven la URL con los valores de la query y los tramos largos del path ocultos
  (`…/sse?token=•••`); los errores y los registros también quitan los secretos. Al editar, dejar la URL vacía conserva la guardada.
- **Avisos**: se avisa de `http://` hacia hosts que no son locales (el token viaja sin cifrar) y de valores secretos escritos
  literalmente en vez de como `${VARIABLE}`.
- **Seguridad del transporte**: en SSE, el endpoint de envío que anuncia el servidor debe ser del mismo origen que la URL; si no, se rechaza.
- Todo lo demás (aprobación por huella, riesgo, confirmación, resultados no confiables) es igual que con un servidor local.

## Qué ve el agente

Las herramientas se llaman `mcp__<servidor>__<herramienta>` y aparecen en su prompt con la firma de sus argumentos.
Se respetan `allowed_tools` / `forbidden_tools` de cada agente: puedes dar una herramienta MCP a un solo agente
(`mcp__github__create_issue`).

## Seguridad

- **Entorno mínimo**: el servidor solo recibe `PATH`, carpetas del usuario y lo que declares en `env`
  (`${VAR}` toma el valor del entorno de PRAXEON). No hereda tus claves de API.
- **Confirmación por defecto**: lo que anuncia el servidor sobre sí mismo (`readOnlyHint`…) no se cree salvo que pongas
  `trust_hints: true`. Sin eso, toda herramienta MCP pide confirmación humana.
- **Nunca se auto-aprueban**: en Full Access autónomo, las herramientas MCP con efectos siguen esperando al operador.
  Solo se auto-aprueban las marcadas como solo lectura.
- **Ajuste por herramienta**: `PUT /v1/mcp/servers/{nombre}/tools/{herramienta}` con `risk`, `read_only`, `confirm` o
  `disabled`. Prevalece sobre lo que diga el servidor.
- **Resultados no confiables**: lo que devuelve una herramienta llega marcado como dato externo, no como instrucciones.
- **Cambios silenciosos**: si el servidor modifica sus herramientas tras aprobarlas, dejan de exponerse (`changed`)
  hasta que vuelvas a aprobarlas.
- **No hay sandbox**: el servidor es un proceso con tus permisos. Aprueba solo servidores en los que confíes.

## Límites actuales

- Solo herramientas (no recursos ni prompts MCP) y sin OAuth: la autenticación remota es por token en la URL o en cabeceras.
- Los resultados de las herramientas MCP no cuentan como evidencia para el supervisor.
- `allowed_tools: ["*"]` (el valor por defecto de un agente) permite también todas las herramientas MCP aprobadas; para limitarlo,
  quita el `*` y elige las herramientas una a una.
- Al editar un servidor, la API nunca devuelve los valores de `env`: reenvía `null` en una clave para conservar el valor guardado.
