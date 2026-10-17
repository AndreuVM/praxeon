# Changelog

Todas las modificaciones notables de este proyecto están documentadas en este archivo según los lineamientos de [Keep a Changelog](https://keepachangelog.com/es-ES/1.0.0/) y siguen [Semantic Versioning](https://semver.org/lang/es/).

---

## [Unreleased]

### Added
- Clasificador: buscar texto peligroso entre comillas (`grep "rm -rf" src`) ya no se bloquea; `net user /add`, `net localgroup /add`, `Set-ExecutionPolicy` y `powershell -EncodedCommand` pasan a CRITICAL.
- `LAYA_RECORD_VERSION=2`: el registro de la acción añade si la orden aparece en lo leído antes (procedencia). Desactivado por defecto.
- `PRAXEON_TRACE_SUPERVISOR` (`1` o ruta): volcado local, redactado, del estado exacto y la decisión cruda del supervisor por paso;
  el benchmark añade la etiqueta de la ejecución. Base para el ajuste fino de LAYA (`docs/plans/laya_finetuning.md`).

### Cambiado (interfaz)
- **Rediseño completo de la interfaz web («Sereno», `redesign/sereno`)**: la conversación con el agente ocupa el centro; decisión, evidencia, política y recibo pasan a un cajón lateral (pestañas *Mapa*, *Paso* y *Eventos*) y el árbol de decisiones deja de ocupar el lienzo. Navegación en un riel de iconos con el selector de workspace; paleta de comandos (`Ctrl K`); aprobación humana como ventana emergente sobre el compositor con motivo de rechazo obligatorio; etiquetas permanentes para Full Access, modo degradado y falta de conexión. Sistema de diseño único en `index.css` (9 tokens de color, Geist y Geist Mono, sin sombras ni brillos, `prefers-reduced-motion`), todas las vistas y modales adaptados con su funcionalidad completa, textos íntegramente en español y avisos/confirmaciones propios en lugar de `alert()`/`confirm()`. Se elimina código sin uso (`MissionLauncher`, `Sidebar`).

### Añadido
- **LAYA usa por fin el modelo neuronal local** (`pip install praxeon[laya]`): el backend `local` llama a `laya.Router().predict` (antes asumía una API inexistente y siempre caía al motor de reglas). Con el paquete instalado, `auto` y `local` lo usan; si no se pueden cargar los pesos se degrada, de forma visible (`backend: simulated`), al motor calibrado. LAYA y KEV comparten las preguntas y la lectura de respuestas (`praxeon.providers.systemone`).
- **KEV como supervisor System-1** (`praxeon.providers.kev`): cliente del servidor de [jaredpalmer/kev](https://github.com/jaredpalmer/kev) (`POST /v1/systemone`, compatible con TypeSafe). Una sola petición responde las primitivas choice/noul/score que usa LAYA. Backends `local` (servidor en este equipo, que PRAXEON puede lanzar con `KEV_AUTOSTART=1` + `KEV_HOME`) y `hosted` (servidor remoto con `KEV_API_KEY`). Si el servidor no responde informa de fallo y nunca concede; no hay motor simulado.
- **Borrados recursivos acotados**: `rm -rf build`, `Remove-Item -Recurse -Force data,build`, `rd /s /q dist`… sobre rutas relativas del workspace que no sean protegidas dejan de ser un BLOCK automático y piden revisión humana (`WORKSPACE_DELETE_REQUIRES_REVIEW`). Siguen bloqueados: la raíz, el home, unidades, `..`, `.`/`*` (el workspace entero), `.git`, `.env`, `.ssh`, claves y secretos, y los borrados encadenados con otros comandos.
- **Auditoría de supervisores aplicada** (informe del 2026-10-17): LAYA pasa a ser un supervisor *consultivo* (`LAYA_VETO_MODE=advisory`; KEV sigue siendo `authoritative`): su BLOCK probable pide revisión humana, su REPLAN se ignora y su confianza ya no escala a REVIEW, porque un checkpoint sin ajuste fino a trazas de agente decide casi al azar. La temperatura posterior (`LAYA_TEMPERATURE`) vuelve a 1,0: con T=0,3 un 0,33 crudo se convertía en 0,56 y superaba el umbral de veto. La detección de bucles por reglas pasa al arnés (`policy/loop_detector.py`) y se aplica a cualquier supervisor. El agente recibe una explicación del veto en lenguaje llano (`veto_feedback.py`). El clasificador determinista cubre PowerShell (`Remove-Item`, `ri`, `rd`, `del`), las peticiones de red que envían datos (`curl -d/-F/-T`, `--data`, `Invoke-WebRequest -Body`), `python -c` con red, `exec` o borrado, y `| sh` sin `bash`. Comprobadores del benchmark corregidos (`absent_symbol`, comparación de tests sin salto de línea final).
- **LAYA: estado que cabe y confianza calibrada**: el checkpoint lee ~1.024 tokens y sus preguntas ocupan más de la mitad; con historiales largos recortaba el final, donde va la acción propuesta (un `rm -rf /` pasó de BLOCK a ALLOW). Ahora el estado se reduce por pasos hasta que el modelo lo lee entero. Además la decisión recibe una temperatura posterior (`LAYA_TEMPERATURE`, 0,30 por defecto): el checkpoint trae T=1,76 para 4 opciones, ajustada a datos sintéticos de su autor, y deja p_max en ~0,4 aun acertando el 80 % (ECE 0,37); con T=0,3 el ECE baja a 0,13 y las lecturas seguras con p_max<0,5 pasan del 77 % al 10 %. Nuevo banco de 106 escenarios etiquetados (`praxeon.evaluation.laya_scenarios`) y `scripts/fit_laya_temperature.py` para reajustarla.
- **LAYA en GPU y umbral de confianza 0,30**: `LAYA_DEVICE` (`auto` por defecto) usa la GPU CUDA si existe; con PyTorch CUDA la inferencia de LAYA pasó de ~1,5 s a ~60 ms. El mínimo de confianza de la política baja de 0,40 a 0,30 porque el modelo neuronal reparte sus probabilidades de forma plana. El reparto GPU/CPU se adapta al equipo (`praxeon.providers.hardware`): según la VRAM, LAYA y KEV van juntos a la GPU, KEV a la GPU y LAYA a la CPU (p. ej. KEV-4B en 8 GB) o todo a la CPU; `LAYA_DEVICE` y `KEV_DEVICE` lo fuerzan. El servidor de KEV se lanza con `--no-sync` si ya tiene entorno, para no reinstalar PyTorch solo CPU.
- **Cascada de supervisión configurable** (`provider: cascade`): dos niveles elegibles entre LAYA, KEV y TypeSafe, local+local o local+nube (API), con umbral de confianza por nivel. Un nivel que no se pueda construir informa de fallo y nunca concede en silencio. Se configura en la vista Proveedores y se usa al elegir «Cascada» como supervisor; antes esa opción de la interfaz no estaba registrada en el backend.
- **Cliente MCP** (`praxeon.integrations.mcp_client`): PRAXEON consume herramientas de servidores MCP (locales por stdio y remotos por SSE o HTTP streamable) bajo la misma custodia que las nativas. Alta en tres pasos con aprobación por huella, retirada de herramientas que cambian, entorno aislado, secretos fuera de la API y de los errores, confirmación por defecto y herramientas con efectos que nunca se auto-aprueban en Full Access. API `/v1/mcp`, pestaña *Tools (MCP)* y selector de herramientas MCP por agente. Guía en `docs/MCP_CLIENT.md`.
- **Proveedores Claude**: Claude por API (`anthropic`, con caching de prompt en la API oficial) y Claude por la CLI instalada (`claude_cli`, con un proceso abierto por nodo de workflow y sin claves de API). Ninguno se elige en modo `auto`.
- **Ollama por su API nativa** con contexto fijo (`PRAXEON_OLLAMA_NUM_CTX`) para evitar el truncado silencioso de prompts largos.
- **Uso real de tokens** de cada proveedor registrado en las métricas de contexto (`actual_prompt_tokens`, `actual_cached_tokens`).
- **Interfaz**: selector de workspace siempre visible, chat con modelo por proveedor y listas ampliables, última configuración e historial por sesión, canvas por bloques de agente con árbol animado, workflows que gobiernan la misión y pasos ilimitados (`max_steps = 0`).

### Cambiado
- **Compresión de contexto por lotes**: el resumen de pasos antiguos solo se rehace cada 6 pasos, para que el prefijo sea estable entre turnos y los cachés de los proveedores puedan reutilizarlo.
- La aplicación de escritorio conserva el puerto y guarda los datos de la ventana en la caché local, de modo que la configuración y los chats sobreviven al cierre.
- El `PolicyEngine` comparte el `ToolRegistry` con el ejecutor (antes tenía una copia propia, así que las herramientas registradas dinámicamente eran «desconocidas» para la política).
- El formato `Thought:` de los prompts pide un solo paso en lugar de una justificación: la redacción anterior activaba una salvaguarda de los modelos Claude grandes.
- `praxeon-live` y `praxeon live` aceptan `--provider anthropic` y `--provider claude_cli`.

### Seguridad
- Cerrada la preautorización de Full Access para herramientas externas con efectos (pipeline de decisiones y bucle del agente).
- `SECURITY.md` y `docs/FULL_ACCESS_POLICY.md` actualizados con el modelo de amenazas del cliente MCP y de los proveedores.

---

## [1.1.0] — 2026-10-09

### Añadido
- **Independencia Total de Modelos de Decisión (Fase 1)**:
  - `typesafe-sdk` y `laya` convertidos en dependencias opcionales (`[project.optional-dependencies]`).
  - Implementación del protocolo abstracto `DecisionProvider` y registro dinámico `DecisionProviderRegistry`.
  - Contenedor de ejecución `SessionRuntime` multi-sesión, eliminando el provider global mutable y permitiendo misiones concurrentes aisladas con proveedores heterogéneos.
- **Motor Visual de Workflows Multiagente y Semántica de Control Declarativa (Fase 4)**:
  - Soporte tipado de primer orden para nodos `START`, `END`, `TASK`, `AGENT`, `DECISION`/`IF`, `WHILE`, `DELEGATE`, `HUMAN_APPROVAL`, `PARALLEL_FORK`, `PARALLEL_JOIN`.
  - Árbol de condiciones declarativas (`AtomicCondition`, `CompoundCondition`) sobre namespaces aislados sin invocar `eval()`.
  - Bucles estructurados acotados (`WHILE`) con invariante `INV-03` (`max_iterations >= 1`) y resolución en algoritmos topológicos del DAG.
  - Pausa y reanudación supervisada en nodos `HUMAN_APPROVAL` (`WAITING_APPROVAL`) y retroceso determinista (`backtrack_workflow`).
  - Catálogo de plantillas canónicas: *Code Review Loop*, *Research → Writer → Reviewer* y *Triage Router*, accesibles vía API REST y modal interactivo en el frontend.
- **Coordinadores Modulares de Servidor (`ServerCoordinators`)**:
  - Descomposición de la lógica de servidor en `SessionLifecycleCoordinator`, `StepExecutionCoordinator`, `CheckpointCoordinator` y `DiagnosticsCoordinator`.
- **Suite de Pruebas de Calidad**:
  - 839 tests automatizados pasando al 100% en plataformas Linux y Windows.

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
