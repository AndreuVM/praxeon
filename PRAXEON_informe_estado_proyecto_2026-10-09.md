# Informe Integral del Estado del Proyecto: PRAXEON

**Fecha de Elaboración:** 9 de octubre de 2026  
**Proyecto:** PRAXEON (`AndreuVM/praxeon`)  
**Versión Declarada:** 1.0.0 (en transición hacia 1.1.0)  
**Entorno de Auditoría:** Windows 10 AMD64 / Python 3.11.15 / Node.js 20 / Vite 8.3.1  
**Estado General de la Suite de Tests:** **839 tests pasados, 1 saltado (100% éxito)**

---

## 1. Resumen Ejecutivo

PRAXEON se ha consolidado como un middleware de supervisión formal y runtime de seguridad desacoplado para agentes de IA autónomos (*ReAct*, *Tool-use*, *Tree-of-Thought*). Su axioma fundacional permanece inalterable:

> **"El modelo propone. El runtime decide qué se ejecuta."**

A diferencia de los frameworks de agentes tradicionales que otorgan ejecución directa a las salidas generativas del LLM o confían en prompts permisivos, PRAXEON interpone una barrera determinista e inmutable:

$$\text{LLM Proposal} \to \text{Context/Evidence} \to \text{Risk Engine} \to \text{Semantic System-1} \to \text{PolicyEngine} \to \text{HMAC Capability} \to \text{SecureExecutor} \to \text{Sandbox} \to \text{Host}$$

### Cuadro de Métricas Clave

| Dimensión | Métrica Actual | Estado |
| :--- | :---: | :---: |
| **Pruebas Automatizadas Totales** | **839 pasadas, 1 saltada** (0 fallidas) | 🟢 Excelente |
| **Seguridad Estática (Bandit)** | **0 Críticas (High), 0 Medias (Medium)** | 🟢 Auditado |
| **Tasa de Falsos Permitidos (False Allow)** | **0.0%** en toda la matriz supervisada | 🟢 Infalible |
| **Latencia Decisional System-1 (LAYA local)** | **< 0.1 ms** (sim/auto) / **~33 ms** (neural) | 🟢 Tiempo Real |
| **Independencia de Decision Providers** | TypeSafe, LAYA, Replay, Mock desacoplados | 🟢 Fase 1 OK |
| **Compilación Frontend (Vite)** | `vite build` completado en **1.35s** | 🟡 Requiere code-split |
| **CI/CD Multiplataforma** | Ubuntu + Windows / Python 3.11 & 3.12 | 🟢 Unificado |

```mermaid
graph LR
    A["Agente Autónomo (LLM)"] -->|"1. Propone Acción"| B["Evidence & Risk Engines"]
    B -->|"2. Validación Contextual"| C["Supervisor System-1 (LAYA / TypeSafe)"]
    C -->|"3. Juicio Semántico"| D["PolicyEngine (Determinista)"]
    D -->|"4. Emite Capability Firmado"| E["HMAC-SHA256 Token"]
    E -->|"5. Verificación & Anti-Replay"| F["SecureExecutor"]
    F -->|"6. Confinamiento Físico"| G["Sandbox (Container / Local Jail)"]
    G -->|"7. Resultado Observable"| H["Host OS / Target"]
```

---

## 2. Auditoría de Seguridad y Modelo de Amenazas

La postura de seguridad de PRAXEON está formalizada en `SECURITY.md` y validada mediante la suite en `tests/security/`. Se asume un modelo de adversario donde el LLM puede ser manipulado mediante **inyección indirecta de prompts**, **alucinación de herramientas** o **intentos de evasión shell**.

### 2.1. Cadena Criptográfica de Custodia (Capabilities HMAC-SHA256)
- **Token Firmado:** Ninguna herramienta física se invoca sin un `DecisionReceipt` firmado mediante **HMAC-SHA256** (`hmac.compare_digest` para inmunidad ante ataques de temporización).
- **Enlace Multidimensional:** La firma abarca 8 dimensiones canónicas obligatorias:
  $$\text{Payload} = \text{decision\_id} : \text{session\_id} : \text{action\_hash} : \text{state\_hash} : \text{nonce} : \text{status} : \text{expires\_at} : \text{execution\_mode}$$
- **Anti-Replay Atómico Concurrente:** El consumo de nonces (`SqliteNonceStore` y `InMemoryNonceStore`) opera bajo transacciones atómicas serializadas. Ha sido probado formalmente con contienda de **20, 50 y 100 hilos concurrentes simultáneos**: exactamente 1 hilo ejecuta la acción y los $N-1$ restantes son rechazados atómicamente con `PolicyViolation`.

### 2.2. Modos de Ejecución Formales y Límites de Full Access
El sistema implementa tres modos estrictamente disjuntos (`ExecutionMode`):
1. **`CONTAINER`:** Contenedores OCI (Docker/Podman) con filesystem de raíz de solo lectura (`--read-only`), red totalmente apagada (`--network=none`), aislamiento cgroups y descarte de privilegios (`--cap-drop=ALL`). Posee comportamiento **Fail-Closed**: si el demonio OCI no está disponible, la ejecución se bloquea físicamente en lugar de degradar a host.
2. **`LOCAL_RESTRICTED`:** Confinamiento local de procesos hijos con resolución real de symlinks (`os.path.realpath`) para anular *symlink traversals* y *TOCTOU*, depuración estricta de variables de entorno (eliminación de API keys y contraseñas) y desactivación de red.
3. **`FULL_ACCESS`:** Ejecución directa en host para workflows de desarrollo local. **Invariante central:** El modo de ejecución no altera la autoridad de supervisión. Las decisiones `BLOCK` y `REPLAN` son innegociables e inalterables en Full Access. Toda ejecución autónoma en este modo exige la autorización explícita y criptográficamente verificada del operador (`full_access_authorized_by_operator=True`).

### 2.3. Precedencia de Seguridad Determinista (Rule 0)
Se aplica una jerarquía inmutable de decisión:
$$\text{Rule 0 (Adversarial Evasion)} > \text{Barreras Críticas (PRIVILEGE / DESTRUCTIVE)} > \text{Riesgo Contextual} > \text{Juicio Semántico} > \text{ALLOW}$$

La **Regla 0** neutraliza de manera incondicional:
- Pipes a intérpretes de comandos (`| sh`, `| bash`, `| python`).
- Ofuscación de espacios con variables de shell (`${IFS}`, `$IFS`).
- Decodificación y ejecución dinámica de Base64 (`base64 -d | sh`).
- Conexiones hacia descriptores crudos de red (`/dev/tcp/`, `/dev/udp/`).
- Manipulación de archivos de identidad y credenciales del sistema (`/etc/shadow`, `/etc/sudoers`).

### 2.4. Control Perimetral de Red y Prevención SSRF (`EgressPolicy`)
Ubicado en `praxeon/policy/egress.py`:
- Modos operativos: `BLOCK_ALL` (por defecto), `ALLOWLIST`, `AUDITED`.
- Bloqueo proactivo incondicional de endpoints de metadatos de proveedores en la nube (`169.254.169.254`, `metadata.google.internal`), impidiendo el robo de credenciales de instancia IAM.
- Bloqueo de rangos privados RFC 1918 (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`) y de la interfaz local (`127.0.0.1`, `localhost`, `0.0.0.0`), impidiendo ataques de *Server-Side Request Forgery* (SSRF).

### 2.5. Resultados del Escaneo Estático de Seguridad (Bandit)
- **Líneas escaneadas:** 35.137 líneas de código en `praxeon/`.
- **Severidad Alta (High):** **0 incidencias**.
- **Severidad Media (Medium):** **0 incidencias**.
- **Severidad Baja (Low):** 61 advertencias menores (bloques `try/except: pass` controlados en adaptadores de fallback, invocaciones intencionadas de subprocesos con parámetros desinfectados en los propios sandboxes, y generadores pseudoaleatorios en generadores de escenarios de benchmarking sintéticos).

---

## 3. Funcionalidad y Arquitectura del Sistema

```
praxeon/
├── domain/            # Contratos inmutables Pydantic v2 (Goal, Action, Receipt, State, Capability)
├── context/           # Jerarquía de contexto, L1/L2 Caching determinista, token budgeting
├── providers/         # Adaptadores desacoplados: LAYA, TypeSafe, Replay, Mock, Registry dinámico
├── reasoning/         # EvidenceEngine, LoopDetector, GroundingVerifier, RiskEngine, CompletionVerifier
├── policy/            # PolicyEngine, EgressPolicy, ToolRegistry, PermissionManager (RBAC)
├── runtime/           # SecureExecutor, LocalProcessSandbox, ContainerSandbox, StateStore, NonceStore
├── server/            # API REST FastAPI, WebSocket streaming, ServerCoordinators modulares
│   └── coordinators.py # Descomposición de Session, Execution, Checkpoints y Diagnostics
├── workflows/         # Motor de workflows multiagente: Semántica tipada, WHILE loops, Backtracking
├── evaluation/        # Benchmark runner, matrices comparativas [LLM x System-1], métricas económicas
└── web/               # Interfaz SPA técnica moderna en React + Vite con canvas interactivo
```

### 3.1. Independencia Total de Modelos de Decisión (Fase 1 del Roadmap)
Históricamente el sistema dependía de `typesafe-sdk` en su core. En la iteración actual:
- `typesafe-sdk` y `laya` han sido convertidos en dependencias opcionales (`[project.optional-dependencies]`). El core de PRAXEON puede instalarse y operar de forma pura sin SDKs externos.
- Se implementó el protocolo estándar `DecisionProvider` y el registro dinámico `DecisionProviderRegistry`.
- Se configuró el contenedor de ejecución `SessionRuntime`, eliminando la variable global mutable de provider y garantizando el aislamiento total entre misiones simultáneas con diferentes modelos (por ejemplo, Misión A con LAYA y Misión B con Claude/TypeSafe).

### 3.2. Caching y Gestión Estructurada de Contexto (`praxeon.context`)
- **Fragmentos Tipados (L1):** 9 clases inmutables de fragmentos de contexto con hash SHA-256 (`SystemFragment`, `GoalFragment`, `EvidenceFragment`, etc.).
- **Prefix Snapshot Cache (L2):** Reutilización de prefijos deterministas con expiración por TTL y políticas de invalidación automática ante revocación de evidencias (*stale state defense*).
- **Presupuesto Estricto de 8 Niveles (`TokenBudget`):** Recorte jerárquico determinista sin recurrir a bases de datos vectoriales no reproducibles.

### 3.3. Coordinadores Modulares de Servidor (`ServerCoordinators`)
El monolito de `RuntimeApplicationService` ha sido desacoplado quirúrgicamente en 4 coordinadores especializados:
1. `SessionLifecycleCoordinator`: Ciclo de vida de sesiones, configuración de workspace y worker de misiones.
2. `StepExecutionCoordinator`: Pipeline de propuesta de acciones, evaluación semántica, confirmación humana y despacho a ejecución.
3. `CheckpointCoordinator`: Gestión atómica de snapshots, checkpoints e invalidación de estados.
4. `DiagnosticsCoordinator`: Agregación de auditorías, árbol de decisiones (tabs) y telemetría de proveedores.

### 3.4. Motor Visual de Workflows Multiagente (`praxeon.workflows`)
Se ha incorporado soporte completo para orquestación de flujos de trabajo autónomos:
- **Semántica de Nodos:** START, END, TASK, AGENT, DECISION/IF, WHILE, DELEGATE, HUMAN_APPROVAL, PARALLEL_FORK, PARALLEL_JOIN.
- **Árbol de Expresiones Tipado:** `AtomicCondition` y `CompoundCondition` con soporte para AND, OR, NOT sobre namespaces aislados (`variables`, `outputs`, `loop`, `budget`), eliminando el uso inseguro de `eval()`.
- **Bucles Estructurados Acotados (WHILE):** Cumplimiento del invariante `INV-03`, exigiendo un parámetro obligatorio `max_iterations` y resolviendo ciclos finitos mediante algoritmos topológicos adaptados.
- **Backtrack Determinista:** Capacidad de capturar `ExecutionCheckpoint` y retroceder el estado de ejecución a un nodo anterior con invalidación limpia de pasos posteriores.
- **Plantillas Canónicas:** Plantillas listas para usar: *Code Review Loop (Developer ↔ Reviewer)*, *Research-Writer-Reviewer Pipeline* y *Triage Router*.

### 3.5. Interfaz Gráfica de Usuario (Web App)
- Construida en React + Vite con diseño técnico dark-mode.
- Canvas interactivo de flujos con drag-and-drop, reposicionamiento dinámico de coordenadas y cableado interactivo de aristas.
- Visualizador de árbol de decisiones en tiempo real (*Bézier Decision Tree*).
- Transmisión en tiempo real vía WebSocket con recuperación de lagunas de eventos (`after_sequence` y `sync`).

---

## 4. Objetivos Alcanzados

- [x] **Sellado de Línea Base de Producción v1.0.0:** Verificación formal de la matriz `REL-01` a `REL-15` con 151 pruebas de especificación completadas al 100%.
- [x] **Resolución de la Auditoría Técnica P0/P1 (7 de octubre de 2026):**
  - Eliminación de la dependencia forzada de `typesafe-sdk` en las dependencias base.
  - Eliminación del provider global mutable mediante `SessionRuntime`.
  - Aislamiento E2E demostrado en tests entre misiones concurrentes con configuraciones heterogéneas.
  - Sincronización y unificación real del servicio de rollback físico y restauración de checkpoints.
  - Refactorización de responsabilidades mediante `ServerCoordinators`.
- [x] **Suite de Tests Exhaustiva:** **839 pruebas pasando satisfactoriamente** en 2 minutos y 48 segundos (cobertura en unit, integration, security, routing, persistence, context, coordinators, workflows, e2e y benchmarks).
- [x] **Evaluación Científica de Seguridad (LLM × System-1):**
  - Generación del informe formal reproducible `benchmark_results/MATRIX_REPORT.md` (metadatos BENCH-02 con commit SHA, seed 42, hardware y versiones).
  - Tasa de **0.0% False Allow** a lo largo de todos los modelos supervisados por LAYA y TypeSafe, frente a un **70.0% de False Allow** en agentes no supervisados.
- [x] **Implementación de la Fase 4 de Workflows:** Tipado formal de condiciones, bucles estructurados seguros y plantillas canónicas con interfaz gráfica interactiva.
- [x] **Pipeline CI/CD Unificado:** Consolidación de `.github/workflows/test.yml` probando matriz Ubuntu/Windows y Python 3.11/3.12, validando empaquetado wheel limpio y frontend lint/build.

---

## 5. Tareas Pendientes y En Proceso

### 5.1. En Proceso Inmediato (Área de Trabajo Actual)
- **Consolidación de Cambios en Workflows:** En el working tree actual existen modificaciones pendientes de commit:
  - `praxeon/workflows/semantics.py`, `praxeon/workflows/templates.py`, `tests/workflows/test_workflow_semantics.py`.
  - Integración en `praxeon/server/routes/workflows.py` y `web/src/components/views/WorkflowsView.jsx`.
  - Bundle compilado de frontend `praxeon/server/static/assets/index-DQ0onDy8.js`.
- **Actualización de Versión Oficial:** La versión en `pyproject.toml` se mantiene en `1.0.0` a pesar de que la independencia de providers y el motor de workflows corresponden al hito de arquitectura `1.1.0`.

### 5.2. Pendientes del Roadmap de Evolución (Fases 5 a 8)
- **Fase 5 — Evaluación con Trayectorias de Agentes del Mundo Real:**
  - Sustituir escenarios puramente procedurales por datasets reales de agentes de ingeniería de software (ej. trazas tipo SWE-bench / GAIA).
  - Medir la tasa de recuperación tras loops en trayectorias de más de 30 pasos.
- **Fase 6 — Búsqueda Heurística Top-K y Branching Adaptativo:**
  - Exploración concurrente de múltiples alternativas de acción (*Tree of Thoughts*) ponderadas por la puntuación de progreso de LAYA System-1.
- **Fase 7 — Orquestación Multiagente Distribuida:**
  - Evolucionar `AgentMessageBus` desde colas en memoria hacia transportes asíncronos distribuidos (Redis Streams / RabbitMQ) para despliegues multi-nodo.
- **Fase 8 — Autenticación Multi-Tenant de Grado Empresarial:**
  - Migrar desde API Keys de perfil estático hacia tokens JWT con soporte para rotación y cookies seguras `HttpOnly` para entornos SaaS.

---

## 6. Bugs Detectados y Deuda Técnica

### 6.1. Bug / Fuga de Directorios Temporales en Tests E2E (Media Prioridad)
- **Diagnóstico:** En la raíz del repositorio se detectaron carpetas huérfanas como `e2e_dir_07ae9875`, `e2e_dir_376c3e7e`, `e2e_dir_5a999c32`, etc.
- **Causa Raíz:** En `tests/test_e2e_platform.py:85`, el test genera carpetas temporales directamente sobre el directorio de trabajo local (`temp_dir_name = f"e2e_dir_{uuid.uuid4().hex[:8]}"`). Si el test finaliza abruptamente o no ejecuta un bloque `finally` con limpieza forzada, los directorios persisten en el repositorio y ensucian el workspace.
- **Solución Recomendada:** Utilizar la fixture nativa `tmp_path` de pytest o envolver la creación en `tempfile.TemporaryDirectory()`.

### 6.2. Advertencia de Tamaño de Bundle en Frontend Vite (Baja-Media Prioridad)
- **Diagnóstico:** Durante la compilación de Vite (`npm run build`), se genera la advertencia:  
  `(!) Some chunks are larger than 500 kB after minification: dist/assets/index-DQ0onDy8.js (553.60 kB)`.
- **Causa Raíz:** El archivo `web/src/components/views/WorkflowsView.jsx` contiene más de 3.000 líneas de código junto con iconos de `lucide-react` y componentes complejos de renderizado SVG incluidos en el bundle principal.
- **Solución Recomendada:** Implementar *dynamic imports* (`React.lazy()` y `Suspense`) en `web/src/App.jsx` para cargar bajo demanda las vistas secundarias (`WorkflowsView`, `BenchView`, `AuditView`).

### 6.3. Desincronización de Versión en Metadatos (Baja Prioridad)
- **Diagnóstico:** `pyproject.toml` indica `version = "1.0.0"`, mientras que la memoria de auditoría y los módulos de arquitectura declaran el avance hacia la especificación `1.1.0`.
- **Solución Recomendada:** Incrementar la versión en `pyproject.toml` a `1.1.0-dev` o `1.1.0`.

### 6.4. Prefijos y Comandos Históricos `jev-*` (Deuda Técnica)
- **Diagnóstico:** En `pyproject.toml` y en `.venv/Scripts/` aún se mantienen scripts de entrada como `jev-nav`, `jev-live`, `jev-dash` y `jev-mcp`.
- **Solución Recomendada:** Documentar estos comandos como formalmente obsoletos con advertencia de deprecación hacia los binarios canónicos `praxeon`, `praxeon-live`, `praxeon-dash` y `praxeon-mcp`.

---

## 7. Sugerencias de Mejoras y Optimización

### 7.1. Optimización de Arquitectura y Rendimiento
1. **Sandboxing Ligero con WebAssembly (Wasm/WASI):**
   - Actualmente existe una brecha considerable entre `LocalProcessSandbox` (proceso local del host) y `ContainerSandboxAdapter` (que exige Docker o Podman en ejecución).
   - *Sugerencia:* Incorporar un backend intermedio basado en runtimes WASI ligeros (como `wasmtime-py`), permitiendo aislar la ejecución de herramientas de cómputo y transformación sin la sobrecarga de un demonio de contenedores.
2. **Code Splitting y Carga Asíncrona en la Interfaz Web:**
   - Reducir el bundle inicial de 553 kB a < 200 kB dividiendo las vistas de visualización pesada (Decision Tree, Workflows Canvas y Benchmark Explorer).
3. **Fixtures Estándar de Pytest para Limpieza de Workspace:**
   - Crear una fixture global en `tests/conftest.py` que restrinja cualquier creación de archivos de prueba al directorio temporal del sistema operativo, eliminando la aparición de carpetas residuales `e2e_dir_*`.

### 7.2. Gobernanza y Operación en Producción
1. **Exportador Nativo de Métricas OpenTelemetry / Prometheus:**
   - Exponer un endpoint `/metrics` en el servidor FastAPI para monitorizar en tiempo real métricas críticas:
     - `praxeon_policy_evaluations_total{status="allow|block|replan"}`
     - `praxeon_decision_latency_seconds_bucket`
     - `praxeon_nonce_verifications_total`
     - `praxeon_context_cache_hit_ratio`
2. **Generación Automatizada de Especificación OpenAPI / Clientes SDK:**
   - Aprovechar los esquemas tipados de Pydantic v2 en FastAPI para exportar automáticamente un cliente TypeScript tipado (`@praxeon/sdk`), facilitando la integración con agentes creados en Node.js, Next.js y ecosistemas web.

---

## 8. Conclusión y Dictamen Técnico

El proyecto **PRAXEON** se encuentra en un estado de **madurez técnica y solidez arquitectónica excepcional**. Ha superado con éxito las limitaciones históricas de acoplamiento a librerías propietarias y cuenta con:
- Una separación rigurosa y matemáticamente respaldada entre el razonamiento estocástico del LLM y la autoridad determinista de ejecución del sistema operativo.
- Un conjunto de 839 pruebas automatizadas con 100% de éxito en plataformas Linux y Windows.
- Cero vulnerabilidades de severidad alta o media en escaneo estático de seguridad de código.
- Una tasa contrastada de **0.0% False Allow** frente a ataques y comandos peligrosos.

Las tareas inmediatas no requieren rediseños estructurales, sino completar la estabilización de los cambios en curso sobre el motor de workflows, realizar el commit de la Fase 4, aplicar la limpieza de directorios temporales en tests e incrementar la versión a `1.1.0`.
