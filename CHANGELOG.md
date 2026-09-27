# Changelog

Todas las modificaciones notables de este proyecto están documentadas en este archivo según los lineamientos de [Keep a Changelog](https://keepachangelog.com/es-ES/1.0.0/) y siguen [Semantic Versioning](https://semver.org/lang/es/).

---

## [1.0.0] — 2026-09-27

### Añadido
- **PRAXEON 1.0 Platform**:
  - **Runtime Semantics & Precedencia Operacional**:
    - Motor de clasificación determinista `CommandClassifier` con 10 categorías canónicas (`inspection`, `build_test`, `package_management`, `local_mutation`, `network`, `process_control`, `privilege`, `destructive`, `remote_mutation`, `unknown`).
    - Desacoplamiento estricto de la identidad de la herramienta frente a la semántica de la operación concreta: operaciones de solo lectura (`cat`, `wc`, `git status`, `ls`) ejecutadas vía `run_command` ya no sufren sobre-restricción estática.
    - Precedencia formal de seguridad: Reglas críticas estáticas (`DESTRUCTIVE`, `PRIVILEGE`) > Restricciones de sesión > Riesgo contextual > Señal semántica > ALLOW.
    - Manejo de incertidumbre sintáctica: comandos desconocidos en herramientas admisibles derivan a `REVIEW` (requieren confirmación auditada), nunca bloqueo ciego (`BLOCK`).
  - **Fase 1 (Hardening & Event Model)**:
    - Tipos de eventos inmutables (`EventType`, `make_event`) con secuencia monótona estricta por sesión y método `append_new()`.
    - `EventBus` persistente con SQLite WAL y recuperación de huecos (*gap recovery*).
    - `DecisionTreeReducer` canónico para reconstruir el árbol jerárquico de decisiones en memoria a partir del log de eventos.
    - NonceStore atómico `consume-once` y validación criptográfica HMAC-SHA256 en recibos de decisión con `execution_mode` ligado a la firma.
  - **Fase 2 (Web Server FastAPI & WebSocket Streaming)**:
    - Servidor FastAPI modular en `praxeon/server/` con arquitectura de capas y separación estricta de autoridad.
    - Endpoints REST completos: gestión de sesiones (`/v1/sessions`), evaluación de propuestas (`/v1/sessions/{id}/actions`), Decision Inspector en 4 pestañas (`/v1/decisions/{id}`), confirmación humana (`/v1/decisions/{id}/confirm`), rechazo explícito auditado (`/v1/decisions/{id}/reject`), ejecución en sandbox (`/v1/decisions/{id}/execute`), historial de eventos paginado (`/v1/sessions/{id}/events`), salud (`/v1/health`) y métricas dinámicas (`/v1/metrics`).
    - Canal dúplex WebSocket `/v1/sessions/{id}/stream` con parámetro `after_sequence` y supresión de eventos duplicados en reconexión.
  - **Fase 3 (Frontend Web Application & Design System)**:
    - SPA moderna construida con Vite + React en `web/` con estética dark mode técnica e idéntica al mockup de referencia.
    - Componentes de alta fidelidad: `Header`, `Sidebar`, `SessionKPIs`, `DecisionTree` interactivo (zoom, pan, curvas Bézier SVG, estados dinámicos), `ConsolePanel` (terminal en vivo con coloreado de veredictos y timeline de eventos) y `DecisionInspector` (4 pestañas: Decision, Evidence, Policy, Receipt).
    - Command Classification Card con metadatos contextuales (categoría, read-only, reversibilidad, acceso a red, regla de preflight).
    - Integración de gap recovery en stream WebSocket y rechazo auditado de decisiones.
  - **Fase 4 (Consolidación, Vistas Complementarias & Benchmark de Sobre-restricción)**:
    - Vistas completas de navegación: `SessionsView`, `DecisionsView`, `AgentsView`, `ProvidersView`, `SecurityView` y `SettingsView`.
    - Suite normativa de Benchmark de Sobre-restricción (`tests/benchmarks/test_over_restriction_benchmark.py`) evaluando las 6 familias de la Sección 15 del PDF:
      - Safe Known Operations: 100% de precisión, 0.0% de falsos bloqueos.
      - Safe Uncommon Operations: 100% de precisión, 0.0% de falsos bloqueos.
      - Ambiguous Operations: 100% ruteadas a revisión humana (REVIEW), 0 bloqueos ciegos.
      - Dangerous Operations: 100% bloqueadas determinísticamente, 0.0% de falsos permisos.
      - Context-Dependent Operations: Validación de fronteras de aislamiento en `ExecutionMode`.
      - Adversarial Syntax: 100% bloqueadas mediante preflight estático y detección de wrappers.
    - Suite de pruebas exhaustiva con 240+ tests unitarios, de integración y de seguridad pasando al 100%.

## [0.4.0] — 2026-09-25

### Cambiado
- **Rebranding oficial a PRAXEON**:
  - Nombre del proyecto y paquete actualizado a **PRAXEON** (`praxeon`).
  - Descripción oficial: *"Runtime supervision for autonomous AI agents"*.
  - Entry points de CLI actualizados a `praxeon`, `praxeon-dash`, `praxeon-live` y `praxeon-mcp` (manteniendo compatibilidad hacia atrás con los alias `jev-nav`, `jev-dash`, `jev-live` y `jev-mcp`).
  - Módulo de compatibilidad `jev_navigator` con redirección automática transparente de imports y `DeprecationWarning`.

### Añadido
- **Suite de Evaluación Multidimensional (Fase 4)**:
  - Dataset procedural masivo de 1.000+ escenarios con división estricta y determinista: 800 Train y 200 Holdout (`ScenarioCatalog.get_holdout_scenarios`).
  - **Provider Benchmark** (`run_provider_comparison`): Evaluación de concordancia inter-proveedor (*agreement rate*), tasa de discrepancias y latencias percentiles entre JEV, LAYA y CascadeRouter.
  - **Policy Benchmark** (`run_policy_benchmark`): Matrices de confusión completas, tasa de falsos permitidos (`false_allow_rate = 0.0%`) y precisión de bloqueo justificado.
  - **Enforcement Benchmark** (`run_enforcement_benchmark`): Verificación al 100% de barreras físicas contra firmas HMAC manipuladas, ataques de replay, evasión por path traversal y SSRF a metadatos cloud.
  - **Runtime Benchmark** (`run_runtime_benchmark`): Medición de throughput (*ops/sec*) y distribución percentil de latencias ($p50 < 0.1\text{ ms}$, $p95$, $p99$, media y máxima).
  - **Trajectory Benchmark** (`run_trajectory_benchmark`): Evaluación de agentes multi-paso con detección de bucles, reversión a checkpoints válidos (`CheckpointManager.restore_checkpoint`) y prevención de finalizaciones prematuras.
- **Estudio de Ablaciones de 6 Capas Arquitecturales** (`run_expanded_ablation_study`):
  - Inclusión de Config 6: *Confidence-Aware Cascade Router*.
  - Cálculo automático de valor económico neto ($\text{NavigatorValue}$) según la Sección 20 de la auditoría técnica.
- **Scripts y Herramientas de Publicación y CI (Fase 5)**:
  - `scripts/run_benchmarks.py`: Script oficial para reproducir benchmarks y exportar reportes estructurados JSON y `benchmark_results/SUMMARY.md`.
  - `examples/demo_offline.py`: Demostración visual e interactiva sin dependencias de red ni costo de API.
  - Actualización de CI (`.github/workflows/test.yml`) ejecutando los 199 tests, la suite de benchmarks y la demo offline.
- **Enrutador en Cascada Sensible a la Confianza (`ConfidenceAwareRouter`)**:
  - Implementación formal según el paper *«JEV-as-a-Judge: Accept When Confident, Escalate When Unsure»* (arXiv:2609.26550).
  - Vía rápida local (LAYA) con escalado automático a System-2 (TypeSafe) ante incertidumbre o riesgo operacional elevado.
  - Calibración formal: métricas ECE, MCE, Brier score y curvas de Riesgo Selectivo vs Cobertura (AURC).

---

## [0.3.0] — 2026-09-25

### Añadido
- **Enforcement Físico y Barreras Criptográficas**:
  - `DecisionReceipt` firmado mediante HMAC-SHA256 con verificación matemática de integridad antes de invocar cualquier herramienta en el SO.
  - `SqliteNonceStore` y `InMemoryNonceStore` con caducidad temporal (`expires_at`) y poda automática por TTL (`prune_expired`) contra ataques de replay.
  - `SqliteStateStore` para persistencia transaccional de sesiones con SQLite WAL.
- **Sandboxing y Control de Egress**:
  - `ContainerSandboxAdapter` para aislamiento en contenedores OCI (Docker/Podman) con filesystem de solo lectura y supresión de capabilities.
  - `LocalProcessSandbox` con resolución canónica de rutas (`os.path.realpath`) para anular escapes por symlinks y traversals.
  - `EgressPolicy` con modo `block_all`, listas blancas de dominios y bloqueo de IPs reservadas y metadatos cloud (`169.254.169.254`).
- **Integración del Proveedor LAYA (`LayaProvider`)**:
  - Primitivas probabilísticas de System-1: `choice`, `score` y `noul` (probabilidades calibradas de bucle y groundedness).
  - Modos de ejecución `local`, `hosted`, `simulated` y `auto`.
- **Presupuesto y Acotación de Contexto (`ProviderContextBuilder`)**:
  - Ventanas temporales acotadas (`max_history_steps`), límite de tokens (`token_budget`) y truncamiento seguro de observaciones extensas.

---

## [0.2.2] — 2026-09-24

### Añadido
- **Verificación Estructurada de Completitud (`CompletionVerifier`)**:
  - Evaluación rigurosa de criterios tipados (`CriterionType.FILE_EXISTS`, `TESTS_PASS`, `EXIT_CODE_ZERO`, `STATE_VALUE`, `CUSTOM`).
  - Bloqueo sistemático de finalizaciones prematuras sin evidencia empírica (`UNVERIFIED_COMPLETION`).
- **Fundamentación Empírica y Control de Premisas (`EvidenceEngine`)**:
  - Registro de aserciones (`Claim`) y verificación de precondiciones observables en el entorno.
  - Detección de evidencias obsoletas tras mutaciones de archivos (*stale state defense*).
- **Gestor de Checkpoints y Rollback Formal (`CheckpointManager`)**:
  - Creación de snapshots canónicos de sesión con hash determinista SHA-256.
  - Reversión de estado ante bucles cíclicos con invalidación de descendientes y prohibición de transiciones fallidas.
- **Inspección de Riesgo Operacional (`RiskEngine`) y Política Fail-Safe (`FailSafePolicy`)**:
  - Clasificación de comandos destructivos y modo `BLOCK` inmediato ante indisponibilidad del proveedor de inferencia ($FalseAllowRate = 0.0\%$).

---

## [0.1.0] — 2026-09-23

### Añadido
- Prototipo inicial de supervisión cognitiva basado en Joint Expected Value (JEV).
- Adaptador de integración con TypeSafe AI System One.
- Middleware proxy para interceptar trazas de agentes ReAct y Model Context Protocol (MCP).
