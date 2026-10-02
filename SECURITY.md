# Política de Seguridad y Modelo de Amenazas: PRAXEON

**Runtime supervision for autonomous AI agents**

## 1. Versiones Soportadas

| Versión | Soportada | Estado de Mantenimiento |
| :--- | :---: | :--- |
| **1.0.x (v1.0.0)** | ✅ Sí | Versión activa, auditada y certificada para producción y defensa académica (DAM). Incluye Web API FastAPI con autenticación forzada, WebSocket streaming seguro, Decision Tree durable, Execution Modes (Container, Local Restricted, Full Access) con capability binding HMAC-SHA256 y fail-closed por defecto. |
| < 1.0.0 | ❌ No | Deprecada y discontinuada. Se recomienda actualizar a v1.0.0. |

---

## 2. Modelo de Amenazas y Filosofía de Defensa en Profundidad

PRAXEON asume un entorno de adversarios hostiles donde el modelo de lenguaje (LLM) está sujeto a:
- **Inyección indirecta de prompts** a través de observaciones del entorno (archivos, páginas web, APIs externas).
- **Alucinación de herramientas y parámetros** (invocaciones no fundamentadas empíricamente).
- **Tentativas de manipulación de estado o replay attacks** (reutilización de autorizaciones pasadas y carreras concurrentes).
- **Evasión de límites del filesystem y ejecución arbitraria en el host**.
- **Exfiltración o llamadas de red no autorizadas (Network Egress y SSRF a metadatos cloud)**.
- **Evasión sintáctica y ofuscación de subshell** (`${IFS}`, base64 pipelines, pipes a intérpretes).

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
   - Aislamiento de límites de seguridad en el servidor y cliente MCP.
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
   > - **Revisión humana obligatoria:** Toda acción con riesgo High o Critical en `FULL_ACCESS` retiene el requerimiento ineludible de aprobación humana (REVIEW).
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

11. **El Caché de Contexto como Capa de Optimización No Vinculante (praxeon.context):**
   El subsistema de Context Caching y Context Management optimiza el volumen y latencia de los datos presentados al razonador antes de la inferencia:
   - **Invariante de Separación de Autoridad:**
     $$\text{Cache Hit} \neq \text{ALLOW} \neq \text{DecisionReceipt} \neq \text{Capability} \neq \text{Execution}$$
     Un *cache hit* nunca equivale a un permiso, nunca emite capabilities HMAC y nunca ejecuta herramientas en el host. Los caminos con y sin caché convergen en exactamente las mismas compuertas de autorización determinista.
   - **Almacenamiento Efímero y Reconstruible:** El almacenamiento de fragmentos (L1) y snapshots (L2) es process-local y en memoria. Puede desaparecer tras el reinicio del proceso sin comprometer la seguridad ni el estado canónico; el almacenamiento durable (`SessionState`, `EventStore`, `Evidence`) es la única fuente de verdad y basta para reconstruir cualquier contexto bajo demanda. Un *cache miss* es un evento operacional normal, nunca un fallo de seguridad.
   - **Invalidación Estricta:** Cualquier mutación, revocación o descalificación de una evidencia empírica invalida quirúrgicamente los snapshots dependientes; el *rollback* del DAG en la sesión descarta de inmediato cualquier contexto del linaje podado.

---

## 3. Reporte Responsable de Vulnerabilidades

Agradecemos y valoramos el trabajo de los investigadores de seguridad. Si descubres una vulnerabilidad potencial en PRAXEON:

1. **NO abras una issue pública** en GitHub.
2. Envía un reporte detallado con los pasos para reproducir la vulnerabilidad a través de la pestaña **Security Advisories** de GitHub:
   [https://github.com/AndreuVM/praxeon/security/advisories/new](https://github.com/AndreuVM/praxeon/security/advisories/new)
3. Proporciona:
   - Descripción del vector de ataque y componente afectado (`SecureExecutor`, `PolicyEngine`, `SandboxAdapter`, `MCP`, etc.).
   - Prueba de concepto (PoC) ejecutable o traza reproducible.
   - Impacto estimado y posibles mitigaciones.

### Compromiso de Respuesta:
- **Acuse de recibo inicial:** En menos de 48 horas laborables.
- **Evaluación y parche de seguridad:** En un plazo máximo de 14 días.
