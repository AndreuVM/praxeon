# PRAXEON — Informe de análisis del estado actual

**Fecha:** 2026-10-05  
**Ámbito:** revisión del estado actual del proyecto tras aplicar la hoja de ruta  
**Objetivo:** identificar regresiones, problemas arquitectónicos, deuda técnica y cambios recomendados antes de continuar con nuevas funcionalidades.

---

## 1. Diagnóstico ejecutivo

PRAXEON ha avanzado mucho en funcionalidades y ya contiene piezas suficientes para evolucionar hacia una plataforma de orquestación y supervisión de agentes. El principal problema actual ya no es la falta de features, sino que varias piezas nuevas todavía no forman un único runtime coherente y algunas regresiones han roto comportamientos que antes eran válidos.

### Estado por área

| Área | Estado | Prioridad |
|---|---|---|
| Core de supervisión | 🟢 Muy bueno | — |
| Policy / Capability / Executor | 🟢 Bueno | P0 validar |
| Persistencia / eventos | 🟢 Bueno | P1 |
| Context Manager | 🟠 Interesante, pero necesita redefinición | P0 |
| Agent Registry | 🟠 Buena base, integración insuficiente | P1 |
| Agent Router | 🟠 Funcional, todavía experimental | P1 |
| Agent messaging | 🟠 Prototipo sólido, no aún infraestructura | P1 |
| Workflow engine | 🔴 Tiene semántica incorrecta de “completado” | P0 |
| Adaptive Runtime | 🔴 Existe, pero está parcialmente aislado | P0 |
| Web App | 🟠 Avanzada visualmente | P1 |
| Web/API auth | 🔴 Roto para el flujo web autenticado | P0 |
| Benchmarks | 🟠 Buenos como experimento, débiles como evidencia final | P1 |
| CI/CD | 🔴 Inconsistencia importante | P0 |
| Packaging | 🟠 Hay dependencias/compatibilidad que revisar | P1 |
| Mantenibilidad | 🟠 Varios módulos son demasiado monolíticos | P1 |
| Naming / branding | 🟡 Deuda técnica | P2 |

---

# 2. P0 — Regresión ALLOW → REVIEW

Es el cambio más urgente.

En `praxeon/server/dependencies.py`, `RuntimeApplicationService.propose_action()` calcula una `RiskAssessment` y posteriormente la pasa a `PolicyEngine`.

En `praxeon/policy/engine.py`, la lógica de determinadas operaciones de bajo riesgo solo rebaja el riesgo cuando `risk_assessment is None`. Sin embargo, el runtime está pasando una `risk_assessment` explícita.

Esto provoca que operaciones que conceptualmente deberían terminar en `ALLOW` puedan acabar en `REVIEW`.

Ejemplos afectados:

- `git status`
- `read_file`
- `pytest`
- operaciones de inspección o build/test

### Problema conceptual

La evaluación debe combinar:

```text
Tool-level risk
+
Operation-level classification
+
Evidence
+
Policy
↓
Effective Risk
```

No debe existir una dependencia accidental entre “hay una RiskAssessment” y “ya no se puede refinar el riesgo”.

### Cambio recomendado

Crear una fase explícita de reconciliación, por ejemplo:

```text
RiskReconciliation
```

o equivalente:

```text
Input:
- base risk
- operation category
- evidence
- policy
- environment

Output:
- effective risk
```

Ejemplo:

```text
read_file + INSPECTION + read_only
→ LOW

git status + INSPECTION
→ LOW

pytest + BUILD_TEST
→ LOW

write_file
→ MEDIUM / REVIEW

rm -rf ...
→ HIGH / BLOCK
```

### Tests P0

Crear una matriz de casos:

- LOW + allowed → ALLOW
- MEDIUM + confirmation → REVIEW
- HIGH + forbidden → BLOCK
- insufficient evidence → REVIEW/BLOCK según policy
- loop/fixation → REVIEW/BLOCK
- unavailable provider → fail closed
- manual override no debe saltarse policy

---

# 3. P0 — Web App incompatible con el modo autenticado real

El backend protege rutas mediante `verify_api_key`, pero el frontend realiza peticiones REST y WebSocket sin proporcionar las credenciales correspondientes.

Actualmente:

```text
Frontend
  ↓
fetch(...)
```

y:

```text
new WebSocket(wsUrl)
```

no adjuntan API key ni mecanismo de sesión apropiado.

Esto produce la siguiente separación:

```text
DEV / localhost sin auth
→ funciona

PRODUCTION / auth requerida
→ REST puede fallar
→ WebSocket puede fallar
```

### Cambio recomendado

No introducir una API key permanente dentro del frontend.

Preferiblemente:

```text
Login / sesión
↓
HTTPOnly session cookie
↓
REST API
↓
short-lived WebSocket ticket
↓
WebSocket
```

### Test E2E requerido

Con perfil de producción:

```text
abrir Web App
→ autenticación
→ crear/consultar sesión
→ proponer acción
→ confirmar
→ ejecutar
→ recibir eventos por WebSocket
```

Debe probarse tanto el camino válido como el rechazo sin credenciales.

---

# 4. P0 — Workflow Engine puede declarar completada una tarea que no se ha ejecutado

Este es uno de los problemas conceptuales más importantes del nuevo sistema multiagente.

En `praxeon/workflows/engine.py`, determinados `TASK` pueden producir un resultado sintético del tipo “Executed ...” cuando no existe un handler real.

Esto permite:

```text
TASK
↓
no execution
↓
status = completed
```

Una tarea de workflow no debería estar `COMPLETED` simplemente porque ha sido procesada por el engine.

## Caso AGENT

La situación es similar:

```text
PRAXEON
↓
send message to agent
↓
dispatched
↓
workflow = completed
```

aunque el agente aún no haya respondido.

### Semántica recomendada

Separar:

```text
READY
↓
DISPATCHED
↓
WAITING_RESULT
↓
COMPLETED
```

o:

```text
DISPATCHED
├── RESULT
├── FAILED
├── TIMEOUT
└── CANCELLED
```

Y correlacionar:

```text
workflow_node
↓
execution_id
↓
message_id
↓
correlation_id
↓
agent result
```

Solo cuando el resultado real llega y es validado:

```text
Agent result received
↓
validate result
↓
policy / runtime
↓
node COMPLETED
```

### P0

Reescribir la semántica de ejecución de `TASK` y `AGENT`.

---

# 5. P0 — Las nuevas piezas aún no forman un único runtime canónico

Actualmente existen componentes interesantes como:

```text
AgentRegistry
AgentRouter
AgentMessageBus
ContextManager
WorkflowEditorService
AdaptiveRuntime
```

pero parte de la lógica de servidor sigue centrada en:

```text
RuntimeApplicationService
↓
Policy
↓
Capability
↓
Executor
```

Esto crea subsistemas que funcionan parcialmente como islas.

### Objetivo arquitectónico

Hacer que `RuntimeApplicationService` sea el único **control plane**.

Arquitectura objetivo:

```text
RuntimeApplicationService
├── Agent Registry
├── Agent Router
├── Context Manager
├── Workflow Engine
├── Agent Message Bus
├── Evidence
├── Risk
├── Policy
├── Capability
└── Secure Executor
```

No:

```text
old runtime
+
adaptive runtime
+
workflow runtime
+
live agent runtime
```

sino:

```text
                    PRAXEON RUNTIME
                          │
          ┌───────────────┼───────────────┐
          │               │               │
       Agent           Workflow         Tool
       task               task           task
          │               │               │
          └───────────────┼───────────────┘
                          │
                     supervision
```

### P0

Integrar las nuevas capacidades en un único flujo canónico y evitar runtimes paralelos que evolucionen de forma independiente.

---

# 6. P1 — Context Cache: la arquitectura es buena, pero no equivale todavía a ahorrar tokens del LLM

El `ContextManager` ya dispone de mecanismos de cache local de snapshots/fragments, fingerprints y reutilización de contexto.

Eso es positivo.

Pero hay que diferenciar:

```text
cache de construcción local del contexto
```

de:

```text
cache real de prompt/provider
```

Si el prompt final sigue enviándose completo al proveedor:

```text
Cache local
↓
mismo prompt
↓
LLM
```

puede disminuir trabajo local, pero no necesariamente el número real de input tokens facturados.

## Además

La idea de `DAGContextSelector` debe distinguirse entre:

```text
context selection
```

y:

```text
dependency-aware retrieval
```

Una selección de ventanas, observaciones recientes y prioridades no equivale todavía a resolver dependencias completas del DAG.

### Implementación recomendada

```text
active node
↓
ancestors
↓
dependencies
↓
required evidence
↓
minimal relevant context
```

---

# 7. P1 — Benchmark de Context Cache necesita una métrica más rigurosa

El proyecto contiene distintos resultados experimentales relacionados con reducción de contexto.

Pueden corresponder a workloads distintos, pero la documentación debe dejar claro:

- qué benchmark se ejecutó;
- qué versión;
- qué workload;
- qué proveedor;
- qué tokenizer;
- qué supuestos;
- qué mide exactamente cada porcentaje.

No debería afirmarse:

> “PRAXEON reduce X% el coste real de LLM”

cuando el experimento solo mide reducción estimada en un workload sintético.

### Niveles recomendados

```text
L1 — local context optimization
L2 — provider/prefix caching
L3 — real LLM A/B benchmark
```

### Métricas

```text
estimated_context_tokens_before
estimated_context_tokens_after

actual_prompt_tokens
actual_cached_tokens
actual_output_tokens

cache_hits
cache_misses
latency
success_rate
```

---

# 8. P1 — Agent Registry necesita pasar de prototipo a infraestructura

`AgentDefinition` es una buena base.

Actualmente puede modelar:

```text
model
prompt
skills
tools
capabilities
risk profile
context policy
version
hash
```

Pero el registry todavía depende demasiado de almacenamiento sencillo.

### Problemas

- persistencia poco integrada con SQLite principal;
- concurrencia limitada;
- escritura de archivos sin semántica claramente transaccional;
- errores de lectura que pueden quedar silenciados;
- falta de una historia de versiones plenamente integrada.

### Cambio recomendado

Separar:

```text
AgentDefinition
```

de:

```text
AgentVersion
```

y persistir ambas mediante una capa coherente con el resto del runtime.

Un fallo de carga no debería hacer desaparecer silenciosamente un agente.

Debe ser:

```text
load failure
↓
log
↓
quarantine / invalid state
↓
report
```

---

# 9. P1 — AgentRouter tiene un posible bypass del filtro de elegibilidad

El flujo de selección manual permite seleccionar directamente un `agent_id`.

Sin embargo, la selección manual no debería saltarse los filtros de:

- capabilities;
- herramientas;
- risk profile;
- estado;
- requisitos de la tarea.

### Regla correcta

```text
Task requirements
↓
Hard eligibility filter
↓
Eligible agents
↓
Manual selection
↓
selected ∈ eligible?
├── YES → continue
└── NO → reject
```

**Manual ≠ bypass de seguridad.**

### Tests

- agente sin capability requerida;
- agente con risk profile incompatible;
- agente sin tool requerida;
- agente deshabilitado;
- agente incompatible con la policy.

---

# 10. P1 — AgentMessage no garantiza todavía integridad autenticada

La existencia de `integrity_hash` es positiva, pero un hash recalculable por el receptor no demuestra integridad frente a un atacante.

Si al deserializar se elimina el hash recibido y luego se recalcula:

```text
mensaje manipulado
↓
crear nuevo objeto
↓
calcular nuevo hash
↓
“válido”
```

no se ha verificado la procedencia del valor original.

### Cambio recomendado

Separar:

```text
received_integrity_hash
```

de:

```text
computed_integrity_hash
```

y comparar:

```text
received == computed
```

Cuando el mensaje cruza una frontera de confianza, usar:

```text
HMAC
```

o firma digital, según el modelo de amenaza.

---

# 11. P1 — IDs temporales con módulo

Persisten patrones del tipo:

```python
int(time.time() * 1000) % 100000
```

o similares.

Esto funciona para demos pero no es adecuado para:

```text
multi-agent
+
parallel workflows
+
multiple clients
+
async execution
```

### Recomendación

Usar:

```python
uuid.uuid4()
```

o UUIDv7/ULID si se desea mantener orden temporal.

Aplicarlo a:

- workflow IDs;
- execution IDs;
- message IDs;
- routing decision IDs;
- edge IDs;
- correlation IDs.

---

# 12. P1 — Separar Workflow Definition de Workflow Execution

Actualmente parte del estado activo está asociado a `workflow_id`.

Arquitectónicamente debe distinguirse:

```text
WorkflowDefinition
```

de:

```text
WorkflowExecution
```

Así:

```text
Workflow X
├── Execution A
├── Execution B
└── Execution C
```

y cada ejecución posee su propio:

- estado;
- nodo actual;
- retries;
- timers;
- correlation IDs;
- resultados;
- eventos.

Esto será imprescindible para multiusuario y ejecuciones concurrentes.

---

# 13. P1 — Retry y timeout necesitan semántica real

Existen campos como:

```text
retry_policy
timeout_seconds
backoff
max_retries
```

pero deben reflejar una ejecución temporal real.

La arquitectura recomendable es:

```text
READY
↓
RUNNING
├── COMPLETED
├── FAILED
└── TIMEOUT
```

y un scheduler que controle:

```text
timeout
retry count
backoff
next attempt
cancellation
```

No conviene que estos conceptos existan solo como configuración declarativa sin una máquina de estados que los haga cumplir.

---

# 14. P1 — `PARALLEL_FORK` todavía no implica ejecución paralela real

El workflow graph puede representar ramas paralelizables, pero esto no equivale necesariamente a un scheduler concurrente.

Por ahora la documentación debería diferenciar entre:

> grafo con ramas paralelizables

y:

> ejecución paralela real.

Más adelante:

```text
PARALLEL_FORK
├── Execution A
├── Execution B
└── Execution C
        ↓
PARALLEL_JOIN
```

debe implicar concurrencia real y sincronización.

---

# 15. P1 — `dependencies.py` y `live_agent.py` están creciendo demasiado

`dependencies.py` es demasiado grande para su responsabilidad actual y `live_agent.py` concentra demasiada lógica.

Esto hará difícil seguir incorporando:

- routing;
- agent library;
- workflow;
- context;
- multi-agent.

### División recomendada

```text
server/
├── auth_service.py
├── session_service.py
├── decision_service.py
├── execution_service.py
├── mission_service.py
├── agent_service.py
├── workflow_service.py
└── runtime_application.py
```

Y:

```text
live_agent/
├── llm_adapter.py
├── mission_runner.py
├── trajectory_controller.py
└── runtime_client.py
```

---

# 16. P1 — Adaptive Runtime debe ser el runtime real, no una arquitectura paralela

La carpeta adaptativa contiene piezas muy interesantes:

```text
AdaptiveRuntime
StepDispatcher
ContextManager
AgentRouter
AgentMessageBus
```

pero el objetivo debe ser que estas piezas formen parte del flujo real que ejecuta el servidor.

### Objetivo

Una única entrada operacional:

```text
Task
↓
Adaptive Runtime
↓
Agent / Workflow / Tool
↓
Context
↓
Supervision
↓
Policy
↓
Capability
↓
Execution
```

y no varios caminos que implementen parcialmente la misma lógica.

---

# 17. P1 — La vista de Agents todavía es una demo, no una Agent Library real

La UI contiene perfiles de agentes y herramientas, pero todavía no constituye una biblioteca de agentes persistentes administrada por backend.

Para convertirlo en producto:

```text
Agent Library UI
↓
REST API
↓
AgentRegistry
↓
persistent storage
```

API mínima:

```text
GET    /v1/agents
POST   /v1/agents
GET    /v1/agents/{id}
PATCH  /v1/agents/{id}
DELETE /v1/agents/{id}
POST   /v1/agents/{id}/versions
```

---

# 18. P1 — Workflow Editor debería acabar integrado en la misma aplicación

Usar un `iframe` puede ser una buena solución intermedia, pero a largo plazo la aplicación debería sentirse como un único producto:

```text
PRAXEON App
├── Dashboard
├── Sessions
├── Agents
├── Workflows
├── Routing
└── Runtime
```

Con un único modelo de sesión, permisos, eventos y navegación.

---

# 19. P0 — CI/CD

La configuración actual de CI no sigue la ubicación estándar esperada por GitHub Actions.

Debe pasar a:

```text
.github/
└── workflows/
    ├── test.yml
    └── security.yml
```

y evitar reglas de `.gitignore` que oculten `.github`.

Además, la documentación debe coincidir exactamente con la ubicación real.

---

# 20. P0 — CI debe comprobar también el frontend

Ahora que PRAXEON incluye una Web App, el pipeline debe cubrir:

```text
Python
+
Frontend
+
Packaging
```

Mínimo:

```text
python -m pytest
npm ci
npm run lint
npm run build
python -m build
```

Separar jobs cuando sea útil.

---

# 21. P1 — Tests y proveedores

Hay pruebas que dependen de SDKs/proveedores externos.

Conviene disponer de una interfaz:

```text
Provider interface
├── MockProvider
├── TypeSafeProvider
├── LAYAProvider
└── ReplayProvider
```

Los tests unitarios e integración básica deberían poder utilizar `MockProvider` sin necesitar el SDK real.

Esto reduce fragilidad del CI y separa claramente:

```text
correctness of PRAXEON
```

de:

```text
availability of external provider
```

---

# 22. P1 — Separar niveles de test

Recomendación:

```text
tests/
├── unit/
├── integration/
├── e2e/
├── security/
├── providers/
├── benchmark/
└── live/
```

Y markers:

```python
@pytest.mark.unit
@pytest.mark.integration
@pytest.mark.e2e
@pytest.mark.security
@pytest.mark.provider
@pytest.mark.live
@pytest.mark.benchmark
```

El PR normal no debería requerir LLMs reales o infraestructura pesada si no es necesario.

---

# 23. P1 — Branching no debe activarse por defecto todavía

El benchmark actual sugiere que más ramas pueden mejorar el éxito en workloads sintéticos, pero el coste en tokens crece considerablemente.

La pregunta correcta es:

```text
quality gain
/
extra tokens
```

y:

```text
quality gain
/
extra latency
```

La conclusión todavía no debería ser:

> K=3 siempre es mejor.

Debe permanecer como experimento.

---

# 24. P2 — Limpiar naming legado de JEV

Todavía quedan nombres como:

```text
jev_engine.py
JEVDashboard
jev_v2_*
jev_cache
jev_...
```

El proyecto ya se presenta como PRAXEON, por lo que conviene completar la migración.

Mantener nombres antiguos solo cuando sean:

```text
legacy compatibility
```

y marcarlos claramente.

También conviene migrar bases/artefactos como:

```text
jev_cache
```

a:

```text
praxeon_cache
```

---

# 25. P1 — Packaging

Hay módulos que importan `yaml`, por lo que la dependencia debe estar explícitamente declarada en `pyproject.toml` si no lo está de forma transitoria.

Además, la compatibilidad de paquetes legado debe probarse contra el artefacto realmente construido:

```text
python -m build
↓
wheel
↓
fresh environment
↓
pip install
↓
CLI
↓
imports
```

No basta con probar el repo directamente.

---

# 26. P1 — Renombrar métricas de contexto para distinguir estimación de consumo real

Evitar mezclar:

```text
tokens_before
tokens_after
tokens_saved
```

cuando son estimaciones locales.

Preferible:

```text
estimated_context_tokens_before
estimated_context_tokens_after
estimated_context_tokens_saved
```

y por separado:

```text
actual_prompt_tokens
actual_cached_tokens
actual_output_tokens
```

Esto será especialmente importante para benchmarks publicados.

---

# 27. P1 — Diferenciar claramente estimación de cache de provider cache

No presentar actualmente:

```text
L1/L2 Prefix Cache
```

como si ya existiera una infraestructura de cache distribuida o un mecanismo de cache nativo del proveedor.

La implementación actual puede describirse honestamente como:

```text
fragment cache
+
context snapshot cache
```

Posteriormente puede añadirse:

```text
provider-side prefix caching
```

cuando exista soporte real.

---

# 28. P1 — Fallback sintético del Live Agent

El modo de fallback sintético es útil para:

- demos;
- tests;
- desarrollo offline.

Pero para ejecución real debería existir una distinción explícita:

```text
FAIL_CLOSED
```

vs

```text
BEST_EFFORT
```

Y el modo activo debe ser visible en:

- UI;
- telemetry;
- session state;
- audit logs.

No debería existir un comportamiento ambiguo en el que el LLM falle y PRAXEON continúe con un resultado sintético sin dejarlo claro.

---

# 29. Qué NO cambiar

Las siguientes decisiones actuales son buenas y deberían conservarse:

### Filosofía de supervisión

```text
Reasoning
→ Evidence
→ Risk
→ Policy
→ Capability
→ Execution
```

### Capabilities con HMAC

Buena base para separar decisión de autoridad operacional.

### SQLite + event store ligero

Adecuado para el tamaño y propósito actual del proyecto.

### Decision Tree

Es una pieza especialmente interesante tanto arquitectónicamente como para la visualización del runtime.

### AgentDefinition

Tiene una forma adecuada para evolucionar hacia una Agent Library real.

### Context Manager

La dirección es correcta; debe precisarse qué optimiza realmente.

---

# 30. Arquitectura objetivo inmediata

La dirección recomendada es:

```text
                         ┌────────────────────┐
                         │      Web App       │
                         │ React + WebSocket  │
                         └─────────┬──────────┘
                                   │
                                   ▼
                         ┌────────────────────┐
                         │       API          │
                         └─────────┬──────────┘
                                   │
                                   ▼
                  ┌────────────────────────────────┐
                  │ RuntimeApplicationService      │
                  │        SINGLE CONTROL PLANE    │
                  ├────────────────────────────────┤
                  │ Agent Registry                 │
                  │ Agent Router                   │
                  │ Context Manager                │
                  │ Workflow Engine                 │
                  │ Agent Message Bus              │
                  │ Evidence                       │
                  │ Risk                           │
                  │ Policy                         │
                  │ Capability                     │
                  │ Secure Executor                │
                  └───────────────┬────────────────┘
                                  │
                                  ▼
                            Sandbox / Host
```

Todos los subsistemas deberían producir eventos coherentes:

```text
Agent selected
Context built
Context reused
Decision created
Capability issued
Agent dispatched
Agent responded
Policy evaluated
Execution started
Execution finished
Workflow advanced
```

---

# 31. Kanban recomendado ahora

## 🔴 BLOCKERS

- [ ] Corregir regresión `ALLOW/REVIEW`.
- [ ] E2E API authentication.
- [ ] E2E WebSocket authentication.
- [ ] Corregir semántica de `AGENT` completion.
- [ ] Corregir `TASK` fake completion.
- [ ] Integrar Adaptive Runtime en el runtime canónico.
- [ ] E2E de Full Access.
- [ ] Mover CI a `.github/workflows`.

## 🟠 HARDENING

- [ ] Integridad real de `AgentMessage`.
- [ ] UUIDs para IDs.
- [ ] Eligibility en selección manual del Router.
- [ ] Separar Workflow Definition / Workflow Execution.
- [ ] Retry real.
- [ ] Timeout real.
- [ ] Persistencia de Agent Registry.
- [ ] Persistencia de Workflow Execution.
- [ ] Descomponer `RuntimeApplicationService`/`dependencies.py`.
- [ ] Descomponer `live_agent.py`.

## 🟡 CONTEXT / EFFICIENCY

- [ ] Renombrar métricas estimadas.
- [ ] Revisar selector DAG.
- [ ] Implementar dependency-aware retrieval real.
- [ ] Implementar context delta.
- [ ] Añadir token accounting del proveedor.
- [ ] Realizar benchmark A/B con LLM real.
- [ ] Consolidar artefactos de benchmark.

## 🟢 PRODUCT

- [ ] Agent CRUD API.
- [ ] Agent Library persistente.
- [ ] Integrar Agent Library en React.
- [ ] Integrar Workflow Editor en la aplicación.
- [ ] Agent execution lifecycle.
- [ ] Agent-to-agent response/ack.

## 🔵 FUTURO

- [ ] Branching adaptativo.
- [ ] Agent Router avanzado.
- [ ] Multi-agent.
- [ ] Cost-aware routing.
- [ ] Desktop shell opcional.

---

# 32. Orden de ejecución recomendado

El orden más eficiente a partir del estado actual es:

1. **Corregir la regresión ALLOW/REVIEW.**
2. **Cerrar autenticación REST + WebSocket de extremo a extremo.**
3. **Dar semántica real a la ejecución de workflows y agentes.**
4. **Convertir Adaptive Runtime en el único control plane.**
5. **Endurecer Registry, Router y Message Bus.**
6. **Limpiar CI, packaging y naming.**
7. **Rehacer el benchmark de eficiencia con métricas reales.**
8. **Completar Agent Library y Workflow como producto.**
9. **Después profundizar en routing, branching y multi-agent.**

---

# 33. Conclusión

PRAXEON ya tiene suficientes piezas para empezar a parecer una plataforma completa:

```text
Runtime
+
Context
+
Agents
+
Router
+
Messaging
+
Workflow
+
Web UI
```

El principal riesgo ahora es que esas piezas evolucionen como subsistemas parcialmente independientes.

El siguiente objetivo no debería ser:

> “añadir más funcionalidades”.

Debería ser:

> **“convertir todas las funcionalidades existentes en un único runtime coherente, seguro, medible y trazable.”**

Después de esa consolidación, la evolución hacia:

```text
Runtime Supervisor
→ Agent Orchestrator
→ Adaptive Agent Runtime
```

tendrá una base mucho más sólida.

