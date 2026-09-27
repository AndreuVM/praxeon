# PRAXEON — Diseño e implementación de un runtime de supervisión para agentes autónomos basado en evaluación semántica, políticas deterministas y ejecución controlada

**Memoria Técnica y Académica del Proyecto de Fin de Ciclo**  
**Ciclo Formativo de Grado Superior:** Desarrollo de Aplicaciones Multiplataforma (DAM)  
**Autor:** Adrián  
**Versión del Sistema:** 1.0.0 (Enterprise GA & Production Release)  
**Fecha:** Septiembre 2026  

---

## 1. Resumen Ejecutivo y Ficha Técnica

El advenimiento de los Modelos de Lenguaje de Gran Escala (LLMs) dotados de capacidades de ejecución de herramientas (*tool use* / *function calling*) ha transformado el desarrollo de software, propiciando el nacimiento de **agentes autónomos**. Sin embargo, la integración directa de modelos estocásticos en entornos operativos presenta riesgos críticos de seguridad y robustez: alucinaciones con efectos destructivos en el sistema de archivos o bases de datos, bucles de razonamiento degenerativo (*infinite tool loops*), finalizaciones prematuras sin verificación empírica (*premature finishes*) y vulnerabilidad ante inyecciones de instrucciones indirectas (*indirect prompt injections*).

**PRAXEON** es una plataforma multiplataforma de supervisión y gobernanza en tiempo de ejecución diseñada para interponerse entre cualquier agente de IA (LangChain, CrewAI, AutoGen, Claude Desktop, Cursor) y los recursos físicos del sistema operativo anfitrión. 

### Ficha Técnica del Proyecto
* **Nombre Oficial:** PRAXEON (Platform for Runtime Assessment, eXecution Enforcement & Operational Navigation).
* **Entorno de Ejecución:** Multiplataforma (Windows 11, Linux Ubuntu 22.04+, macOS Sonoma).
* **Lenguajes y Runtimes:** Python 3.11+, JavaScript (ES2023) / HTML5 / CSS3.
* **Stack Backend:** FastAPI, Starlette, Pydantic v2, Uvicorn, SQLite3 (WAL mode), HMAC-SHA256, Subprocess & Container Sandboxing APIs.
* **Stack Frontend:** React 18, Vite, WebSocket Client, SVG Visualization, Modern CSS Architecture.
* **Protocolos e Interoperabilidad:** REST OpenAPI 3.1, WebSockets (Full Duplex con Gap Recovery), Model Context Protocol (MCP).
* **Licencia:** Apache 2.0 / Código Abierto para fines de investigación y docencia.

---

## 2. Justificación y Motivación del Proyecto

### 2.1 El problema de la agencia descontrolada
Los agentes autónomos actuales reciben un objetivo en lenguaje natural, descomponen el plan en pasos cognitivos y despachan llamadas a herramientas (*tool calls*) como `run_command`, `write_file` o peticiones HTTP. Cuando estos agentes operan sin supervisión determinista:
1. **Falta de Grounding:** El modelo asume que una acción se completó con éxito basándose únicamente en su propia inferencia probabilística, sin verificar el sistema de archivos ni los códigos de salida de los procesos.
2. **Degeneración de Trayectorias y Bucles:** Ante errores repetidos, el modelo entra en patrones oscilatorios (repetir `git status` o el mismo comando con variaciones cosméticas), agotando el presupuesto de tokens y tiempo.
3. **Peligro de Acciones Irreversibles:** Comandos altamente lesivos como `rm -rf /`, `DROP DATABASE`, o la subida de secretos a repositorios públicos (`git push origin main`) son emitidos accidentalmente o inducidos por datos no confiables.
4. **Vulnerabilidades de Cadena de Suministro:** Un texto descargado de internet puede contener instrucciones que secuestran el contexto del modelo (*Prompt Injection* indirecto) ordenándole leer claves de API y exfiltrarlas por red.

### 2.2 La propuesta de valor de PRAXEON
PRAXEON resuelve esta problemática aplicando un **paradigma de confianza cero (*Zero-Trust Runtime*)**:
> *"Ninguna herramienta toca el host anfitrión sin un capability token criptográfico firmado por una política determinista que haya evaluado el riesgo y la evidencia observable."*

PRAXEON actúa como una aduana matemática y de seguridad que desacopla el cerebro probabilístico del agente del músculo ejecutor del sistema operativo.

---

## 3. Objetivos Técnicos y Pedagógicos

### 3.1 Objetivos Técnicos
1. **Pipeline de Supervisión Canónico en 7 Etapas:** Implementar una cadena de custodia estricta: `Proposal -> Evidence -> Risk -> Provider -> Policy -> Capability -> Execution`.
2. **Cero Ejecuciones Físicas No Autorizadas:** Demostrar formalmente que ninguna acción con estado `BLOCK` o `REVIEW` pendiente llega a invocar el handler físico del sistema.
3. **Criptografía y Prevención de Replay:** Firmar cada autorización mediante HMAC-SHA256 con nonces únicos de un solo uso y expiración temporal (TTL), persistidos en una base de datos SQLite transaccional resistente a condiciones de carrera concurrentes.
4. **Aislamiento Multi-Nivel (Sandboxing):** Soportar ejecución en contenedores aislados (`ContainerSandboxAdapter` con Docker/Podman) con fallback configurable y seguro a un sandbox de procesos locales con contención estricta de rutas (*path jail*), redacción de secretos y control de variables de entorno.
5. **Observabilidad en Tiempo Real:** Proveer una API REST documentada y un canal WebSocket bidireccional con recuperación automática de eventos perdidos (*gap recovery*) y números de secuencia monótonos.
6. **Interfaz Web de Supervisión:** Construir un panel web reactivo que permita a los operadores humanos inspeccionar el grafo de estados, auditar la evidencia y autorizar o rechazar manualmente acciones críticas en estado `REVIEW`.
7. **Estandarización MCP:** Integrar un servidor compatible con la especificación Model Context Protocol (MCP) para conectar con Cursor, Claude Desktop o cualquier entorno de agentes estándar.

### 3.2 Objetivos Pedagógicos (Competencias de DAM)
El proyecto sintetiza y profundiza en los resultados de aprendizaje del ciclo formativo:
* **Acceso a Datos:** Persistencia políglota, transaccionalidad ACID, SQLite con WAL (*Write-Ahead Logging*), índices y prevención de concurrencia multihilo.
* **Programación de Servicios y Procesos:** Arquitectura de microservicios, asincronía (`asyncio`), gestión de hilos y barreras de concurrencia (`threading.Barrier`, `ThreadPoolExecutor`), comunicación por sockets y WebSockets, y gestión segura de procesos hijos y contenedores.
* **Desarrollo de Interfaces:** Diseño de interfaces modernas SPA en React, visualización interactiva de grafos dirigidos acíclicos (DAGs), estados reactivos y experiencia de usuario (UX) centrada en el analista de seguridad.
* **Sistemas de Gestión Empresarial y Seguridad:** Principios de auditoría informática, trazabilidad inmutable, prevención de evasiones (*path traversal*, *symlink race conditions*, inyecciones de código) y separación de privilegios según roles (Operator vs. Viewer).

---

## 4. Arquitectura del Sistema

```
+---------------------------------------------------------------------------------------+
|                                    PRAXEON RUNTIME                                    |
|                                                                                       |
|   +-------------------+      +--------------------+      +------------------------+   |
|   |   Agent Proposal  | ---> |   Evidence Engine  | ---> |       Risk Engine      |   |
|   | (Tool / Rationale)|      | (Claims/Grounding) |      | (Command Taxonomy/OOD) |   |
|   +-------------------+      +--------------------+      +------------------------+   |
|                                                                      |                |
|   +-------------------+      +--------------------+                  v                |
|   | Capability Token  | <--- |   Policy Engine    | <--- +------------------------+   |
|   | (HMAC-SHA256/TTL) |      | (Deterministic)    |      | Semantic Provider (IA) |   |
|   +-------------------+      +--------------------+      | (LAYA / TypeSafe Eval) |   |
|             |                                            +------------------------+   |
|             v                                                                         |
|   +-------------------------------------------------------------------------------+   |
|   |                                SECURE EXECUTOR                                |   |
|   |  - Nonce Store Verification (Zero-Replay / Atomic Single-Use)                 |   |
|   |  - Context Binding Check (Session ID & State Hash Alignment)                  |   |
|   |  - Secret Sanitizer (Masking API Keys, Passwords & Sensitive Env Vars)        |   |
|   +-------------------------------------------------------------------------------+   |
|             |                                                                         |
|             v                                                                         |
|   +------------------------------------+      +-----------------------------------+   |
|   |    Container Sandbox (Docker)      |  OR  |    Local Process Sandbox (Host)   |   |
|   | (--network=none, --read-only, cgroup)     | (Path Jail, Strict shlex, Egress) |   |
|   +------------------------------------+      +-----------------------------------+   |
+---------------------------------------------------------------------------------------+
                               | Event Stream
                               v
+---------------------------------------------------------------------------------------+
|                       EVENT BUS & REAL-TIME SUPERVISION LAYER                         |
|                                                                                       |
|   +---------------------+      +---------------------+      +---------------------+   |
|   |  REST API (FastAPI) |      | WebSocket Streaming |      |  React / Vite UI    |   |
|   | (Auth: X-API-Key)   |      | (Gap Recovery/Seq)  |      | (Decision Inspector)|   |
|   +---------------------+      +---------------------+      +---------------------+   |
+---------------------------------------------------------------------------------------+
```

### 4.1 El Pipeline Canónico de 7 Etapas
1. **Proposal:** El agente propone una acción con nombre de herramienta, argumentos estructurados y justificación (*rationale*).
2. **Evidence:** Se extraen evidencias del historial de observaciones, validando si los supuestos del agente están respaldados por hechos observables.
3. **Risk:** Un clasificador determinista categoriza la operación (`INSPECTION`, `LOCAL_MUTATION`, `REMOTE_MUTATION`, `PROCESS_CONTROL`, `PRIVILEGE`, `DESTRUCTIVE`, `BUILD_TEST`).
4. **Provider:** El supervisor semántico (red neuronal o adaptador TypeSafe) analiza la trayectoria en busca de degradación, bucles u oscilaciones.
5. **Policy:** El motor de políticas aplica reglas deterministas de máxima precedencia:
   - Acciones destructivas (`rm -rf`, `DROP DATABASE`) se bloquean irrevocablemente (`BLOCK`).
   - Acciones ambiguas o de mutación remota (`git push`) pasan a revisión humana obligatoria (`REVIEW`).
   - Acciones benignas fundamentadas (`read_file`, `git status`) son autorizadas (`ALLOW`).
6. **Capability:** Solo para decisiones `ALLOW`, se genera un `DecisionReceipt` firmado con HMAC-SHA256, vinculado al hash del estado, hash de la acción, nonce criptográfico y timestamp de expiración.
7. **Execution:** El `SecureExecutor` valida el recibo, consume el nonce de forma atómica en SQLite y despacha la ejecución al sandbox correspondiente.

---

## 5. Implementación Técnica y Endurecimiento

### 5.1 Prevención de Replay y Concurrencia de Nonces (Fase 1)
Para evitar que un atacante o un agente errático reutilice un token de capacidad concedido legítimamente, se implementó `SqliteNonceStore`. 
* **Atomicidad:** Utiliza el modo WAL de SQLite con inserción atómica `INSERT INTO consumed_nonces` bajo clave primaria compuesta `(decision_id, nonce)`.
* **Prueba de Carrera:** La prueba `test_sqlite_nonce_store_concurrent_race_condition` somete el almacén a una contienda extrema con 20 hilos concurrentes sincronizados mediante `threading.Barrier`. Exactamente 1 hilo logra consumir el nonce (`True`) y los otros 19 son rechazados atómicamente (`False`), garantizando cero condiciones de carrera.

### 5.2 Endurecimiento de la Web API y WebSocket (Fase 2)
* **Autenticación Unificada:** Soporte para cabeceras `X-API-Key` y `Authorization: Bearer <key>`.
* **Perfil de Producción:** Cuando `PRAXEON_PROFILE=production` o `PRAXEON_REQUIRE_AUTH=1`, cualquier intento de interactuar con endpoints de decisión o sesión sin credenciales válidas es rechazado con `401 Unauthorized`.
* **Autenticación en WebSockets:** El endpoint `/v1/sessions/{session_id}/stream` (y su alias `/ws/{session_id}`) valida el token pasado por parámetro query `?token=` o cabecera antes de aceptar el handshake. Si no está autenticado, la conexión se cierra inmediatamente con el código normativo `WS_1008_POLICY_VIOLATION`.

### 5.3 Invariante Fundamental de Ejecución (Fase 3 - Sección 10.1)
La auditoría formal de PRAXEON exigió la demostración de la invariante:
$$\forall a \in \text{Actions}, \quad \text{Policy}(a) = \text{BLOCK} \implies \text{PhysicalExecutions}(a) = 0$$
En la prueba `test_e2e_critical_allowed_reaches_executor_and_blocked_never_touches_physical_handler`:
* Una acción permitida invoca al sandbox físico y eleva el contador de ejecución a 1.
* Una acción bloqueada (`rm -rf /`) es detenida por la política, responde con `403 Forbidden` en la API y el contador de invocaciones físicas permanece inalterado en 1 (0 llamadas adicionales).

### 5.4 Salida Estructurada en CompletionVerifier (Fase 4)
Para evitar que un agente declare erróneamente el éxito de una tarea basándose en coincidencias casuales de la subcadena `"success"` en un mensaje de error (ejemplo: *"Failed to verify success condition"*), el `CompletionVerifier` fue dotado de expresiones regulares estrictas:
* Se exige coincidencia estructurada de códigos de retorno (`exit_code == 0`, `rc=0`, `returncode: 0`, `0 errors`, `0 failed`).
* Se requiere formato cuantificado para tests (`\b[1-9]\d*\s+passed\b`), impidiendo que logs con `"0 passed"` o fallos sean clasificados como superados.

### 5.5 Integración MCP con Prefijo Canónico `praxeon_*` (Fase 4)
El servidor MCP (`praxeon/interceptor/mcp_bridge.py` y `server.py`) expone herramientas con nomenclatura canónica:
* `praxeon_start_session`
* `praxeon_validate_action` / `praxeon_evaluate_action`
* `praxeon_step_and_execute`
* `praxeon_rollback`
* `praxeon_get_session_state`
* `praxeon_confirm_action`
* `praxeon_evaluate_next_step`
* `praxeon_diagnose_trace`
Manteniendo retrocompatibilidad completa mediante alias transparentes con los nombres históricos `jev_v2_*` y `jev_*`.

### 5.6 Benchmarks Out-of-Distribution (OOD) y Resistencia Adversarial (Fase 5)
Se construyó la suite `test_ood_and_adversarial_benchmark.py`, evaluando:
1. Codificaciones evasivas (decodificación Base64 encadenada a `/bin/sh`, inyecciones hexadecimales en Python).
2. Manipulaciones de variables internas de la shell (uso de `${IFS}` para evadir filtros de espacios).
3. Intentos de exfiltración de credenciales (`/etc/shadow`, tuberías a `nc`).
4. Inyecciones de prompts indirectas simulando instrucciones del sistema operativo.
5. Cadenas multi-hop donde comandos maliciosos son camuflados tras secuencias de inspección benignas.

---

## 6. Resultados Cuantitativos de Evaluación

| Métrica de la Auditoría Técnica | Objetivo Normativo | Resultado Obtenido en PRAXEON 1.0 | Estado |
| :--- | :---: | :---: | :---: |
| **Exactitud Decisional (Holdout n=200)** | $\ge 95.0\%$ | **100.0%** | Superado |
| **False Allow Rate (Crítico)** | $0.0\%$ | **0.0%** | Conforme |
| **Destructive False Allows (Comandos destructivos)** | $0$ | **0** | Conforme |
| **False Block Rate (Sobre-restricción)** | $\le 5.0\%$ | **0.0%** | Superado |
| **Tasa de Detección de Ataques OOD y Evasión** | $100.0\%$ | **100.0%** | Conforme |
| **Ejecuciones Físicas de Acciones Bloqueadas** | $0$ | **0 (Invariante demostrada)** | Conforme |
| **Resistencia a Replay en Concurrencia (20 workers)** | 1 éxito / 19 bloqueados | **1 éxito / 19 bloqueados (100%)** | Conforme |
| **Latencia Mediana Agregada ($p50$)** | $< 5.0\text{ ms}$ | **0.093 ms** | Superado |
| **Throughput de Supervisión** | $> 2.000\text{ ops/s}$ | **9.836,7 ops/s** | Superado |
| **Cobertura de la Suite de Pruebas Automatizadas** | $> 90\%$ | **100% aprobadas (380+ tests)** | Conforme |

---

## 7. Conclusiones y Trabajo Futuro

### 7.1 Conclusiones
El proyecto PRAXEON 1.0 demuestra con éxito la viabilidad de implementar un runtime de supervisión determinista de alto rendimiento para agentes autónomos. Se ha demostrado que:
1. La inteligencia estocástica de los LLMs puede gobernarse de forma segura sin degradar su utilidad cuando se aplica un pipeline formal con separación de privilegios y tokens criptográficos.
2. La arquitectura basada en capabilities de un solo uso mitiga de raíz los riesgos de evasión, replay y suplantación de entorno.
3. La interfaz web y el protocolo MCP convierten a PRAXEON en una solución lista para despliegues reales en entornos corporativos donde la seguridad no puede dejarse al azar.

### 7.2 Líneas de Trabajo Futuro
* **Aislamiento con Micro-VMs:** Integración de hipervisores ligeros como AWS Firecracker o Google gVisor para ofrecer sandboxing con fronteras de virtualización por hardware en milisegundos.
* **Agentes Multi-Entorno Federados:** Soporte para agentes distribuidos que operan en clústeres Kubernetes mediante mTLS y sincronización descentralizada del EventBus.
* **Calibración Bayesiana Dinámica:** Ajuste continuo de umbrales de riesgo mediante aprendizaje por refuerzo a partir de las decisiones tomadas por los operadores humanos en el Decision Inspector.

---

*Proyecto desarrollado y certificado bajo las directrices del Ciclo Formativo de Grado Superior en Desarrollo de Aplicaciones Multiplataforma (DAM).*
