# Changelog

Todas las modificaciones notables de este proyecto están documentadas en este archivo según los lineamientos de [Keep a Changelog](https://keepachangelog.com/es-ES/1.0.0/) y siguen [Semantic Versioning](https://semver.org/lang/es/).

---

## [1.0.0] — 2026-10-02

### Añadido
- **Matriz Formal de Verificación de Release (REL-01 a REL-15)**:
  - Suite exhaustiva de certificación en `tests/release/` con 151 tests dedicados pasando al 100%.
  - **Seguridad y Hardening de Red (`REL-01`, `REL-02`)**:
    - Autenticación fail-closed obligatoria en endpoints sensibles (rechazo 401/403 ante credenciales inválidas o ausentes).
    - Prohibición de bindings de red no-loopback (`0.0.0.0`, IPs de LAN o hostnames públicos) sin API key configurada.
    - Rechazo estricto de comodines CORS (`*`) y configuraciones de orígenes vacíos en perfil de producción.
  - **Gobernanza y Políticas de Full Access (`REL-03`, `REL-04`, `REL-05`)**:
    - Invariante `BLOCK Always Wins`: las reglas críticas y preflights estáticos jamás pueden ser sobreescritos por Full Access (0 ejecuciones físicas garantizadas).
    - Conservación estricta de estados `REPLAN`, imposibilitando ejecuciones no deseadas durante la reformulación de trayectorias.
    - Formalización de la política en `docs/FULL_ACCESS_POLICY.md`: retención de `REVIEW` en sesiones interactivas, requerimiento de delegación criptográfica de operador para modo autónomo y registro inmutable de auditoría en `EventStore`.
  - **Ligaduras Criptográficas y Prevención de Replay (`REL-06` a `REL-09`)**:
    - Ligadura multidimensional de capabilities con `session_id`, `action_hash` y `state_hash`.
    - Expiración temporal rigurosa por TTL (`expires_at`) rechazada en `SecureExecutor` y en endpoints de la API.
    - Validación matemática de integridad HMAC-SHA256 contra payloads alterados, secretos erróneos o firmas ausentes.
    - Pruebas de estrés concurrente con 100 hilos simultáneos sobre el mismo capability con `NonceStore` atómico: exactamente 1 ejecución exitosa y 99 rechazos deterministas.
  - **Persistencia Multi-BD y Crash Recovery (`REL-10`)**:
    - Arquitectura transaccional de tres bases de datos SQLite en modo WAL (`praxeon_events.db`, `praxeon_state.db`, `praxeon_nonces.db`).
    - Supervivencia y recuperación de decisiones en estado `REVIEW` tras reinicio abrupto del servidor.
    - Persistencia de nonces ejecutados que imposibilitan cualquier repetición post-reinicio e inmutabilidad de veredictos `BLOCK`.
  - **WebSocket Streaming Resiliente (`REL-11`, `REL-12`)**:
    - Reanudación de stream con parámetro `after_sequence` y comando interactivo `sync` para recuperación de huecos (*gap recovery*) sin duplicados.
    - Keepalive liveness mediante ping/pong periódico y cierre controlado.
    - Difusión simultánea a múltiples clientes concurrentes sin pérdida de orden monótono y con aislamiento estricto de sesiones.
  - **Supervisión Canónica y Smoke Test E2E (`REL-13`, `REL-14`)**:
    - Invariantes canónicos de `ProviderAssessment`, fail-safe ante indisponibilidad y máquina de estados robusta de `CircuitBreaker` (`CLOSED` -> `OPEN` -> `HALF_OPEN` -> `CLOSED`).
    - Smoke test E2E de agente en vivo con ciclo de 3 pasos (inspección -> mutación fundamentada -> finish terminal).
    - Prevención estricta de finalización prematura (`PREMATURE_COMPLETION_WITHOUT_EVIDENCE`).
  - **Reproducibilidad de Benchmarks (`REL-15`)**:
    - Metadatos estandarizados de ejecución (`git_commit`, `timestamp`, `praxeon_version`, `model`, `seed`) inyectados automáticamente en `benchmark_run_metadata.json` y cabeceras de `SUMMARY.md`.
    - Interfaz CLI completa en `scripts/run_benchmarks.py` con argumentos `--output-dir`, `--seed`, `--model` y `--quick`.
- **Remediaciones de Auditoría Técnica Formal (2 de octubre de 2026)**:
  - **BUG-01 (Context Fingerprint)**: Eliminado ordenamiento artificial en cálculo de fingerprints de contexto en `praxeon/context/fingerprint.py`, preservando la sensibilidad al orden y posición de fragmentos de memoria y nodos relevantes.
  - **BUG-02 (Robustez de Provider DTO)**: Corregido crash ante providers sin calibración o desconectados mediante campo opcional `score: Optional[float] = None` y `available: bool` en esquemas y repositorios de decisión.
  - **CHG-01 (Semántica Unknown != Malicious)**: Herramientas no registradas inocuas se clasifican contextualmente y retienen en estado de revisión humana (`DecisionStatus.ABSTAIN` / `REVIEW` con `requires_confirmation=True`), reservando `BLOCK` determinista para comandos destructivos comprobados o veto de supervisor.
  - **CHG-02 (Gobernanza Estricta de Full Access)**: Eliminada inferencia permisiva implícita de `created_by == 'system'`; las operaciones en modo autónomo requieren ahora autorización explícita y verificada del operador (`full_access_authorized_by_operator: True`).
  - **CHG-03 (Independencia de Packaging)**: Desacopladas las pruebas de packaging de artefactos preexistentes en `dist/`, incorporando construcción bajo demanda o skip descriptivo.
  - **BENCH-01 (Trazabilidad de Benchmarks de Caché)**: Ejecutados y versionados formalmente los benchmarks de caching y presión de contexto (`--seed 42`), generando `benchmark_results/context_caching_evaluation.json` y `benchmark_results/context_cache_pressure_evaluation.json`.
  - **MAINT-01 (Modernización UTC)**: Migradas todas las llamadas deprecadas de `datetime.utcnow()` a `datetime.now(timezone.utc)` en la totalidad del paquete `praxeon/`.
  - **DOC-02 / DOC-03 (Consistencia de Naming y Rutas)**: Limpieza definitiva de referencias a `JEV-Reasoning-Navigator` en headers CLI y sustitución de rutas absolutas Windows por enlaces relativos en `README.md`.
- **Regresión Completa de la Suite**:
  - 543 tests pasando al 100% (544 recolectados: 543 passed, 1 skipped, 0 failed) en toda la suite `pytest tests/`.


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
