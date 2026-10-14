# Política de Seguridad y Modelo de Amenazas: PRAXEON

**Runtime supervision for autonomous AI agents**

## 1. Versiones Soportadas

| Versión | Soportada | Estado de Mantenimiento |
| :--- | :---: | :--- |
| **1.1.x (v1.1.0 y rama principal)** | ✅ Sí | Versión activa. Incluye Web API FastAPI con autenticación forzada, WebSocket streaming seguro, Decision Tree durable, Execution Modes (Container, Local Restricted, Full Access) con capability binding HMAC-SHA256 y fail-closed por defecto, workflows multiagente supervisados, aplicación de escritorio autenticada en loopback y **cliente MCP** (herramientas externas supervisadas, ver §2.12). Los cambios posteriores a la etiqueta v1.1.0 están en la rama principal y constan en la sección *Unreleased* del `CHANGELOG.md`. |
| < 1.1.0 | ❌ No | Sustituida por 1.1.x. Se recomienda actualizar. |

---

## 2. Modelo de Amenazas y Filosofía de Defensa en Profundidad

PRAXEON asume un entorno de adversarios hostiles donde el modelo de lenguaje (LLM) está sujeto a:
- **Inyección indirecta de prompts** a través de observaciones del entorno (archivos, páginas web, APIs externas).
- **Alucinación de herramientas y parámetros** (invocaciones no fundamentadas empíricamente).
- **Tentativas de manipulación de estado o replay attacks** (reutilización de autorizaciones pasadas y carreras concurrentes).
- **Evasión de límites del filesystem y ejecución arbitraria en el host**.
- **Exfiltración o llamadas de red no autorizadas (Network Egress y SSRF a metadatos cloud)**.
- **Evasión sintáctica y ofuscación de subshell** (`${IFS}`, base64 pipelines, pipes a intérpretes).
- **Servidores MCP externos no confiables** (cliente MCP): herramientas con descripciones envenenadas, resultados con instrucciones ocultas, cambio silencioso de sus herramientas tras haberlas aprobado, abuso de herencia de entorno o exfiltración de secretos hacia el servidor.
- **Fuga de credenciales de proveedores LLM** (claves de API en la interfaz, en `mcp.json`, en registros o en mensajes de error) y gasto involuntario en proveedores de pago.

Para mitigar estas amenazas, el runtime establece una **cadena formal de custodia de autorización**:

$$\text{LLM Proposal} \to \text{Evidence Grounding} \to \text{Risk Assessment} \to \text{PolicyEngine} \to \text{Bound DecisionReceipt (HMAC)} \to \text{SecureExecutor} \to \text{Process/Container Sandbox} \to \text{OS}$$

### Principios Fundamentales:
1. **Separación de Responsabilidades y Delimitación de Host:**
   $$\text{Semantic Judgment (JEV/LAYA)} \neq \text{Operational Policy (PolicyEngine)} \neq \text{Physical Execution (SecureExecutor)}$$
   $$\text{Policy Enforcement} \neq \text{Host Isolation}$$
   El runtime impone verificación determinista y denegación por defecto (fail-safe) a nivel de aplicación. Para contención a nivel de sistema operativo y kernel, el framework soporta:
   - `ContainerSandboxAdapter`: Ejecución contenida en contenedores OCI (Docker/Podman) con `--read-only`, aislamiento de red (`--network=none`), límites estrictos de CPU/memoria y descarte de privilegios (`--cap-drop=ALL`). **Fail-Closed por Defecto:** `fallback_to_local: bool = False`. Si el motor de contenedores falla o no está disponible, el runtime bloquea la acción físicamente en lugar de degradar silenciosamente a ejecución local desprotegida.
   - `LocalProcessSandbox`: Confinamiento local de procesos hijos con desreferenciación real de symlinks (`os.path.realpath`), purga de entorno y fallback ordenado.
2. **Capabilities Ligados Criptográficamente (DecisionReceipt con HMAC-SHA256) y NonceStore Atómico Concurrente:**
   `SecureExecutor` no ejecuta ninguna herramienta física sin recibir un capability emitido por la `PolicyEngine` con:
   - `decision_status == ALLOW`
   - `action_hash == SHA256(action)`
   - `state_hash == SHA256(state)`
   - `session_id == active_session_id`
   - `signature == HMAC-SHA256(secret_key, payload)` verificado mediante comparación en tiempo constante (`hmac.compare_digest`) para mitigar ataques de temporización.
   - `is_expired() == False` validado contra la ventana de validez temporal (`expires_at` / TTL).
   - `nonce` no consumido previamente verificado mediante `SqliteNonceStore` persistente en disco o `InMemoryNonceStore`. **Resistencia a Condiciones de Carrera y Replay:** Probado y validado formalmente bajo contienda masiva con **20, 50 y 100 workers concurrentes simultáneos** (sincronizados mediante barrera temporal en el microsegundo, ver `tests/security/test_concurrent_replay_race.py`). La verificación y consumo del nonce ocurre en una transacción atómica serializada, garantizando el invariante estricto: ante 1 capability legítimo y N intentos concurrentes, exactamente 1 worker consume el nonce con éxito y ejecuta, mientras los N-1 restantes son rechazados atómicamente con excepción `PolicyViolation`.
3. **Persistencia Durable de Estados y Auditoría de Permisos:**
   - `SqliteStateStore`: Almacén transaccional en SQLite con modo WAL para sesiones y checkpoints versionados cronológicamente.
   - `PermissionManager`: Registro transaccional en disco (`audit_log_path`) en formato JSONL inmutable para todas las solicitudes, aprobaciones y rechazos de intervención humana (HITL).
4. **Política de Egress de Red y Defensa contra SSRF (`EgressPolicy`):**
   - Modos operativos: `BLOCK_ALL` (por defecto), `ALLOWLIST` (dominios explícitos) y `AUDITED`.
   - Bloqueo preventivo incondicional de direcciones privadas RFC 1918 (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), interfaces de loopback (`127.0.0.1`, `localhost`) y endpoints de metadatos de proveedores cloud (`169.254.169.254`, `metadata.google.internal`), neutralizando vectores de SSRF y robo de credenciales de instancia.
5. **Contención de Procesos en Sandbox:**
   - **Depuración de Entorno:** Las variables sensibles (`TYPESAFE_API_KEY`, `GEMINI_API_KEY`, tokens y contraseñas) son purgadas del entorno del proceso hijo antes de cualquier ejecución.
   - **Neutralización de Red y Proxies:** Si `allow_network=False` (por defecto), se bloquean comandos de egress (`curl`, `wget`, `nc`, `ssh`, etc.) y se redirigen las variables proxy (`HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`) a `http://127.0.0.1:0`.
   - **Contención de Directorio y Anti-Symlink (Jail Path):** Las rutas de archivos y comandos son forzadas a resolverse dentro del workspace delimitado utilizando `os.path.realpath` para desreferenciar symlinks físicos y prevenir fugas por enlaces simbólicos o traversals (`../`).
   - **Prevención de Inyección Shell:** Ejecución tokenizada sin `shell=True` arbitrario y con timeouts forzados.
   - **Límites conocidos (`LOCAL_RESTRICTED`/`WASM` sin `wasmtime`):** la contención de rutas se aplica al `cwd` y a las operaciones de fichero, **no** al contenido del comando, y el bloqueo de red es por lista de utilidades y variables proxy; no es una barrera frente a un adversario determinado. Para código no confiable use `CONTAINER`.
6. **Sanitización de Salidas (`DataSanitizer`):**
   Las observaciones retornadas por las herramientas son analizadas y enmascaradas (eliminando credenciales, tokens JWT y claves privadas) y envueltas en delimitadores de confianza antes de ser inyectadas en la memoria del agente.
7. **Suite de Seguridad Dedicada (`tests/security/`):**
   Suite formal de pruebas de seguridad que evalúan activamente vectores de ataque adversariales:
   - Forja y alteración de firmas de recibos HMAC.
   - Ataques de replay intra-proceso, tras reinicio con SQLite y bajo carreras concurrentes de alta carga (contienda masiva con 20, 50 y 100 workers simultáneos).
   - Path traversal y escape por enlaces simbólicos.
   - Inyección de comandos shell, evasión por `${IFS}`, base64 y subprocesos.
   - Exfiltración de red y evasión de políticas de egress.
   - Fuga de secretos y sanitización de credenciales.
   - Inyección indirecta de prompts en observaciones y respuestas de herramientas.
   - Aislamiento de límites de seguridad en el servidor MCP de PRAXEON.
   - Cliente MCP (`tests/test_mcp_client.py`, `test_mcp_runtime.py`, `test_mcp_remote.py`, `test_mcp_api.py`): aprobación por huella, retirada de herramientas que cambian, entorno aislado, secretos fuera de la API y de los errores, endpoint SSE de otro origen, y herramientas con efectos que esperan al operador incluso en Full Access autónomo.
   - Autenticación Web API y WebSocket en perfiles de producción.
8. **Modos de Ejecución Formales y Límites de Full Access (PRAXEON 1.0):**
   PRAXEON define tres modos explícitos de ejecución (`ExecutionMode`):
   - `CONTAINER`: Aislamiento estricto de contenedor (cgroups, `--network=none`, raíz de solo lectura, `--pids-limit`).
   - `LOCAL_RESTRICTED`: Sandbox de proceso local con depuración de variables de entorno y contención de rutas (jail path).
   - `FULL_ACCESS`: Ejecución directa sobre el host sin aislamiento de sistema operativo.

   **Invariante Central de Full Access:**
   > **Full Access no es un bypass del runtime ni una relajación oculta de seguridad.**
   > Cambiar el backend de ejecución no cambia quién tiene autoridad sobre la ejecución. Los modos de ejecución son estrictamente disjuntos:
   > $$\text{LOCAL\_RESTRICTED} \neq \text{CONTAINER} \neq \text{FULL\_ACCESS}$$
   > Incluso en modo `FULL_ACCESS`, cada acción debe pasar obligatoriamente por la cadena de custodia completa:
   > $$\text{Proposal} \to \text{Evidence} \to \text{Risk} \to \text{Provider} \to \text{Policy} \to \text{Capability} \to \text{SecureExecutor}$$
   >
   > **Límites no negociables:**
   > - **No fallback implícito:** Si `CONTAINER` o `LOCAL_RESTRICTED` fallan, jamás se degrada automáticamente a `FULL_ACCESS`.
   > - **Firma criptográfica vinculante:** El `execution_mode` forma parte del material firmado por HMAC del `CapabilityPayload`. Cualquier intento de ejecutar un capability firmado para sandbox en host (o viceversa) es rechazado como violación de política.
   > - **Invarianza de BLOCK y REPLAN:** Un veredicto `BLOCK` o `REPLAN` emitido por política permanece absolutamente vinculante en `FULL_ACCESS`; jamás se transforma en `ALLOW`.
   > - **Sin confirmación por paso, con doble confirmación al activar:** una sesión `FULL_ACCESS` autorizada por el operador (selección del modo + aceptación del aviso) no solicita confirmación por acción; el riesgo `CRITICAL`, `BLOCK` y `REPLAN` siguen siendo vinculantes y cada autorización automática queda auditada (`FULL_ACCESS_OPERATOR_PREAUTHORIZED`). `require_step_confirmation` permite volver a exigirla (opt-in).
   > - **Excepción: herramientas externas con efectos (MCP).** La preautorización de `FULL_ACCESS` solo omite la confirmación de acciones cuya herramienta lo permite (`ToolSpec.auto_confirm`). Una herramienta de un servidor MCP con efectos nunca se auto-aprueba, ni en la política del pipeline de decisiones ni en el bucle del agente: espera siempre al operador (ver `docs/FULL_ACCESS_POLICY.md` §3.4).
   > - **Advertencia operacional:** `FULL_ACCESS` opera directamente sobre el host sin aislamiento de sistema operativo. Debe ser explícitamente habilitado, auditado y registrado.

9. **Precedencia de Seguridad Determinista y Detección de Evasión (Rule 0):**
   PRAXEON implementa una jerarquía estricta e inmutable de precedencia decisional:
   $$\text{Rule 0 (Adversarial Evasion)} > \text{Static Critical Barriers (PRIVILEGE / DESTRUCTIVE)} > \text{Session Restrictions} > \text{Contextual Risk} > \text{Semantic Signal} > \text{ALLOW}$$

   - **Regla 0 de Evasión Adversarial:** Se interceptan y bloquean de forma incondicional patrones de camuflaje tales como:
     - Pipes directos a shells (`| sh`, `| bash`, `| python`, `| perl`).
     - Evasión de separadores de espacios mediante variables shell (`${IFS}`, `$IFS`).
     - Decodificación y ejecución de cargas útiles en Base64 (`base64 -d | sh`).
     - Exfiltración a sockets crudos de red (`/dev/tcp/`, `/dev/udp/`).
     - Acceso o alteración de ficheros de autenticación del sistema (`/etc/shadow`, `/etc/sudoers`).
     - Inyección de cadenas hexadecimales arbitrarias hacia intérpretes (`python -c "...exec(bytes.fromhex...)"`).
   - **Invariante de Cero Ejecución Física (Sección 10.1):** Ninguna acción clasificada como `BLOCK` llega jamás a tocar el handler de ejecución física (`SecureExecutor.execute` ni `_execute_builtin_tool_in_sandbox`). El pipeline aborta en la capa decisional con código HTTP 403 Forbidden y cero impacto en el sistema.

10. **Seguridad de la Web API y Streaming WebSocket:**
   - **Perfiles de Seguridad (`development`, `secure`, `production`):** En `production` y `secure`, el acceso a la API REST (`/api/v1/sessions`, `/decide`, `/events`) exige autenticación obligatoria mediante clave API (`X-API-Key` o `Authorization: Bearer <key>`), configurada a través de `PRAXEON_API_KEY`.
   - **Autenticación WebSocket:** Los sockets en `/ws/events` y `/ws/{session_id}` exigen validación de token (parámetro query `?token=` o cabecera). Los intentos de conexión sin autenticación válida son rechazados inmediatamente con código de cierre WS 1008 (Policy Violation).
   - **Aplicación de escritorio (`praxeon app`):** el servidor escucha solo en `127.0.0.1`, con un token aleatorio por ejecución que únicamente conoce la ventana; `validate_network_binding` rechaza cualquier otra interfaz si no hay autenticación configurada. Las rutas de administración (`/v1/mcp`, que pueden lanzar programas) exigen el permiso `ADMIN_SETTINGS`.
   - **Datos locales de la interfaz:** el último modelo y configuración usados, los modelos añadidos y el historial de chat de cada sesión se guardan **sin cifrar** en la caché local (`.praxeon_cache/webview`). Nunca se guardan la API key ni la confirmación de Full Access (lista blanca de campos). Protege esa carpeta como protegerías tu historial de trabajo.

11. **El Caché de Contexto como Capa de Optimización No Vinculante (praxeon.context):**
   El subsistema de Context Caching y Context Management optimiza el volumen y latencia de los datos presentados al razonador antes de la inferencia:
   - **Invariante de Separación de Autoridad:**
     $$\text{Cache Hit} \neq \text{ALLOW} \neq \text{DecisionReceipt} \neq \text{Capability} \neq \text{Execution}$$
     Un *cache hit* nunca equivale a un permiso, nunca emite capabilities HMAC y nunca ejecuta herramientas en el host. Los caminos con y sin caché convergen en exactamente las mismas compuertas de autorización determinista.
   - **Almacenamiento Efímero y Reconstruible:** El almacenamiento de fragmentos (L1) y snapshots (L2) es process-local y en memoria. Puede desaparecer tras el reinicio del proceso sin comprometer la seguridad ni el estado canónico; el almacenamiento durable (`SessionState`, `EventStore`, `Evidence`) es la única fuente de verdad y basta para reconstruir cualquier contexto bajo demanda. Un *cache miss* es un evento operacional normal, nunca un fallo de seguridad.
   - **Invalidación Estricta:** Cualquier mutación, revocación o descalificación de una evidencia empírica invalida quirúrgicamente los snapshots dependientes; el *rollback* del DAG en la sesión descarta de inmediato cualquier contexto del linaje podado.

12. **Cliente MCP: herramientas externas bajo la misma custodia (`praxeon.integrations.mcp_client`):**
    Una herramienta de un servidor MCP no es una herramienta nativa «de confianza»: se registra como `ToolSpec` con un manejador del `SecureExecutor` y recorre **el mismo circuito** (política → capability HMAC → ejecución → redacción de secretos).
    - **Alta en tres pasos y sin exposición previa.** Un servidor nace desactivado y no expone nada hasta que el operador revisa sus herramientas y las aprueba por **huella** (hash de nombre, descripción, esquema y pistas). Si el servidor cambia sus herramientas después, se retiran hasta reaprobar (defensa frente a *rug pull*); cambiar lo que se ejecuta o a qué se conecta (comando, argumentos, entorno, carpeta, URL, cabeceras, transporte) anula la aprobación y desactiva el servidor.
    - **Confirmación por defecto.** Toda herramienta MCP exige confirmación humana. Las pistas que anuncia el servidor (`readOnlyHint`…) no se creen salvo `trust_hints` explícito del operador, y los ajustes por herramienta (riesgo, solo lectura, confirmación, desactivar) prevalecen. Una herramienta que el clasificador no reconoce solo se trata como de solo lectura si su `ToolSpec` lo declara; cualquier veto del clasificador (destructivo, privilegios) se respeta.
    - **Nunca se auto-aprueban las herramientas con efectos**, tampoco en `FULL_ACCESS` autónomo (§2.8).
    - **Entorno mínimo (servidores locales).** El proceso solo recibe `PATH`, las carpetas del usuario y lo que se declare; no hereda claves de API ni otros secretos de PRAXEON. `${VARIABLE}` resuelve valores del entorno o del `.env` en el momento de lanzar.
    - **Secretos en servidores remotos.** El token puede ir en la URL o en cabeceras como `${VARIABLE}`, de modo que `mcp.json` no guarde el secreto. La API, la interfaz, los errores y los registros nunca muestran la URL completa ni valores de cabeceras o de entorno (se ocultan los valores de la query, los tramos largos de la ruta y se filtran los secretos conocidos de los mensajes). Un valor secreto escrito literalmente en `mcp.json` queda en texto plano en disco: la interfaz lo avisa.
    - **Seguridad del transporte.** Solo `http://` y `https://`; se avisa de `http` hacia hosts no locales (el token viaja sin cifrar). En SSE, el endpoint de envío que anuncia el servidor debe ser del **mismo origen** que la URL configurada; si no, se rechaza para que un servidor no redirija los mensajes del agente a otro destino.
    - **Resultados y argumentos.** Lo que devuelve una herramienta llega marcado como dato externo no confiable (no como instrucciones); es una mitigación, **no una garantía**: la barrera real es la confirmación humana y la política. Los argumentos se validan contra el JSON Schema de la herramienta antes de enviarlos.
    - **Permisos por agente.** `allowed_tools` / `forbidden_tools` de cada agente se aplican también a las herramientas MCP (`mcp__<servidor>__<herramienta>`); el valor `*` (por defecto) las permite todas, así que para limitar un agente hay que enumerar sus herramientas.
    - **Límites conocidos:** el servidor MCP **no se ejecuta en sandbox** (un servidor local es un proceso con los permisos del usuario; uno remoto es un servicio ajeno): PRAXEON supervisa la llamada y sus argumentos, no lo que el servidor hace por dentro. Aprueba únicamente servidores en los que confíes. No hay OAuth y los resultados MCP no cuentan como evidencia para el supervisor.

13. **Proveedores LLM, credenciales y gasto:**
    - **Los proveedores que facturan o consumen cupo no se eligen solos.** El modo `auto` nunca selecciona Claude API ni Claude CLI, aunque exista `ANTHROPIC_API_KEY`; hay que pedirlos explícitamente.
    - **Claude CLI no usa claves de API.** El proceso `claude` se lanza sin herramientas y sin `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` en su entorno, de modo que usa la sesión de la suscripción y no factura por tokens. Cada conversación usa su propio proceso, que se cierra al terminar el nodo o la misión.
    - **Endpoint personalizado.** Con un `base_url` personalizado para Anthropic, la clave del entorno no se hereda: debe darse explícitamente, para no enviar credenciales del servidor a un destino arbitrario.
    - **Claves en la interfaz.** El campo de API key del chat no se persiste; los errores de los proveedores indican qué variable falta sin mostrar su valor.

---

## 3. Reporte Responsable de Vulnerabilidades

Agradecemos y valoramos el trabajo de los investigadores de seguridad. Si descubres una vulnerabilidad potencial en PRAXEON:

1. **NO abras una issue pública** en GitHub.
2. Envía un reporte detallado con los pasos para reproducir la vulnerabilidad a través de la pestaña **Security Advisories** de GitHub:
   [https://github.com/AndreuVM/praxeon/security/advisories/new](https://github.com/AndreuVM/praxeon/security/advisories/new)
3. Proporciona:
   - Descripción del vector de ataque y componente afectado (`SecureExecutor`, `PolicyEngine`, `SandboxAdapter`, servidor `MCP`, cliente MCP, proveedor LLM, etc.).
   - Prueba de concepto (PoC) ejecutable o traza reproducible.
   - Impacto estimado y posibles mitigaciones.

### Compromiso de Respuesta:
- **Acuse de recibo inicial:** En menos de 48 horas laborables.
- **Evaluación y parche de seguridad:** En un plazo máximo de 14 días.
