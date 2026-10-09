# PRAXEON — Informe de Auditoría Técnica y Plan de Evolución

**Fecha:** 7 de octubre de 2026  
**Versión declarada:** 1.0.0  
**Repositorio:** `AndreuVM/praxeon`  
**Ámbito:** Core, Runtime, Decision Providers, Agents, Context, Workflows, API, WebSocket, Web App, seguridad, benchmarks y CI/CD.

---

## 1. Resumen ejecutivo

PRAXEON cuenta actualmente con una base técnica sólida: evaluación semántica, evidence/risk, políticas deterministas, capabilities firmadas, control anti-replay, ejecución protegida, sandbox, persistencia de sesiones, eventos, WebSocket, árbol de decisiones y una interfaz web.

El principal reto ya no es añadir funcionalidades aisladas, sino **consolidar las fronteras entre estos componentes**.

La dirección de producto que debe guiar la siguiente iteración es:

> **El usuario puede escoger independientemente el LLM que razona y el modelo System-1 que supervisa sus propuestas. PRAXEON coordina ambos y conserva la autoridad operativa mediante policy, capability y executor.**

Por tanto, TypeSafe/Jev no debe sustituirse simplemente por LAYA. Debe convertirse en **un provider opcional** dentro de una arquitectura agnóstica respecto al modelo de decisión.

### Hallazgos prioritarios

| Prioridad | Hallazgo |
|---|---|
| **P0** | Provider global mutable que puede provocar contaminación entre misiones concurrentes. |
| **P0** | Falta una configuración explícita de modelo System-1 por sesión/misión. |
| **P0** | `typesafe-sdk` continúa siendo dependencia base del proyecto. |
| **P0** | La ruta pública de rollback no está alineada de forma consistente con la restauración real de estado. |
| **P0** | Faltan pruebas E2E que demuestren aislamiento entre misiones con diferentes LLM/System-1. |
| **P1** | `ContextManager` existe, pero no está integrado de forma uniforme en el camino principal de Live Mission. |
| **P1** | APIs de checkpoint divergentes entre servicios y store. |
| **P1** | Full Access necesita separación más explícita entre uso manual y autónomo. |
| **P1** | Parte de la telemetría de providers del frontend es estática/hardcodeada. |
| **P1** | Benchmarks históricos y ejecución actual no están completamente sincronizados. |
| **P1** | Falta una estrategia completa de recuperación de misiones tras reinicio. |
| **P1** | El servicio de runtime concentra demasiadas responsabilidades. |
| **P2** | Persisten nombres y rutas históricas relacionadas con JEV. |
| **P2** | Agent Router, branching y multi-agent deben mantenerse detrás de las mediciones de eficiencia. |

### Recomendación principal

Crear **PRAXEON 1.1 — Decision Model Independence**.

El objetivo es conseguir:

```text
                    PRAXEON
                       │
          ┌────────────┴────────────┐
          │                         │
       ANY LLM                ANY SYSTEM-1
          │                         │
          └────────────┬────────────┘
                       │
                PRAXEON Runtime
                       │
           Evidence → Risk → Policy
                       │
                  Capability
                       │
                  Execution
```

---

# 2. Estado actual

## 2.1 Arquitectura conceptual

La arquitectura central es:

```text
LLM
 ↓
Proposal
 ↓
Evidence
 ↓
Risk
 ↓
System-1 / Decision Model
 ↓
Policy
 ↓
Capability
 ↓
SecureExecutor
 ↓
Sandbox
 ↓
Observation
```

La separación conceptual es correcta:

```text
Semantic Judgment
        ≠
Operational Policy
        ≠
Execution Authority
```

El modelo de decisión evalúa una propuesta, pero **no tiene autoridad directa para ejecutar acciones**.

Policy, capability, firma, anti-replay y SecureExecutor siguen siendo los mecanismos de enforcement.

Esto es una de las fortalezas principales del proyecto.

---

# 3. Arquitectura objetivo

La evolución recomendada es:

```text
                         PRAXEON
                            │
             ┌──────────────┴──────────────┐
             │                             │
       AGENT RUNTIME                DECISION RUNTIME
             │                             │
       ┌─────┴─────┐               ┌──────┴──────┐
       │           │               │             │
     LLM         Context        Provider       Model
       │           │               │             │
   Ollama       ContextMgr       LAYA          v1
   OpenAI       Cache            KEV           v2
   Gemini       Budget           Model X       ...
   etc.        Selector          ...
       │                             │
       └──────────────┬──────────────┘
                      │
                ENFORCEMENT RUNTIME
                      │
             ┌────────┼────────┐
             │        │        │
          Evidence   Risk    Policy
             │        │        │
             └────────┼────────┘
                      │
                 Capability
                      │
                SecureExecutor
                      │
                   Sandbox
                      │
                   Tool/API
```

Cada misión debe tener un runtime aislado:

```text
SessionRuntime
│
├── AgentDefinition
├── LLMRuntime
├── DecisionRuntime
├── ContextManager
├── PolicyProfile
├── ExecutionProfile
└── Telemetry
```

El `RuntimeApplicationService` no debe contener el provider de decisión de una misión como estado mutable global.

---

# 4. Independencia de TypeSafe

## 4.1 Problema actual

En el `pyproject.toml` auditado:

```toml
dependencies = [
    "fastapi>=0.115.0",
    "google-genai>=2.24.0",
    "networkx>=3.6.1",
    "pydantic>=2.13.5",
    "python-dotenv>=1.2.3",
    "pyyaml>=6.0",
    "rich>=15.0.0",
    "typesafe-sdk>=0.7.0",
    "uvicorn>=0.30.0",
    "websockets>=12.0",
]
```

TypeSafe está dentro de las dependencias principales.

En cambio LAYA aparece como extra:

```toml
[project.optional-dependencies]

laya = [
    "laya>=0.3.0",
]
```

Esto genera una asimetría:

```text
PRAXEON
 ├── TypeSafe → obligatorio
 └── LAYA     → opcional
```

La arquitectura objetivo debería ser:

```text
PRAXEON
 ├── LAYA     → opcional
 ├── TypeSafe → opcional
 ├── KEV      → opcional
 ├── Replay   → incluido
 └── Mock     → incluido
```

---

# 5. Diseño de `DecisionProvider`

La interfaz pública debería ser independiente del SDK utilizado.

Por ejemplo:

```python
class DecisionProvider(Protocol):
    provider_id: str

    def evaluate(
        self,
        context: DecisionContext,
        question: DecisionQuestion,
    ) -> DecisionResult:
        ...

    def is_available(self) -> bool:
        ...

    def metadata(self) -> DecisionProviderMetadata:
        ...
```

La interfaz no debe exponer:

```text
typesafe_sdk.*
jev.*
laya.*
```

en el dominio común.

Cada provider adapta su implementación al contrato PRAXEON.

---

# 6. `DecisionModelConfig`

Debe existir una configuración explícita para el modelo System-1.

```python
class DecisionModelConfig(BaseModel):
    provider: str
    model_id: str
    model_version: str | None = None

    backend: str | None = None
    endpoint: str | None = None
    model_path: str | None = None

    device: str | None = None
    timeout_seconds: float = 10.0

    calibration_profile: str | None = None
    fallback_policy: str = "none"
```

### Propósito

Permitir distinguir:

```text
provider
model
version
backend
device
calibration
fallback
```

Por ejemplo:

```yaml
decision_model:
  provider: laya
  model_id: laya-v1
  model_version: "0.3"
  backend: local
  device: auto
  timeout_seconds: 10
```

Esto evita que:

```text
"Laya"
```

sea tratado como una identidad suficiente.

---

# 7. `DecisionProviderRegistry`

Debe existir un registro central de providers:

```text
DecisionProviderRegistry
│
├── laya
├── typesafe
├── replay
├── mock
├── kev
└── ...
```

Conceptualmente:

```python
registry.register("laya", LayaProviderFactory)
registry.register("typesafe", TypeSafeProviderFactory)
registry.register("replay", ReplayProviderFactory)
registry.register("mock", MockProviderFactory)
```

Y:

```python
provider = registry.create(config)
```

### Reglas

El registry debe:

1. Validar providers desconocidos.
2. Comprobar que el extra necesario está instalado.
3. Validar backend.
4. Validar parámetros obligatorios.
5. No cambiar silenciosamente a otro provider.
6. Registrar provider/modelo/backend efectivos.
7. Aplicar fallback solo cuando esté explícitamente configurado.

---

# 8. `DecisionRuntime`

La misión debe poseer su propio runtime de decisión:

```text
MissionRuntime
│
├── LLMRuntime
├── DecisionRuntime
├── ContextManager
├── PolicyProfile
└── ExecutionProfile
```

Por ejemplo:

```python
class DecisionRuntime:
    config: DecisionModelConfig
    provider: DecisionProvider

    def evaluate(...):
        ...
```

Esto elimina la necesidad de:

```python
self.provider = ...
```

en un servicio singleton compartido.

---

# 9. Problema crítico de concurrencia

Actualmente existe un riesgo conceptual importante cuando el provider se modifica dentro de un servicio compartido.

Situación peligrosa:

```text
Mission A
LLM = Qwen
System-1 = LAYA
        ↓
self.provider = LAYA


Mission B
LLM = Claude
System-1 = KEV
        ↓
self.provider = KEV
```

La misión A podría terminar utilizando KEV.

La arquitectura correcta es:

```text
RuntimeApplicationService
│
├── Session A
│   ├── LLM Runtime
│   └── Decision Runtime → LAYA
│
├── Session B
│   ├── LLM Runtime
│   └── Decision Runtime → KEV
│
└── Session C
    ├── LLM Runtime
    └── Decision Runtime → Model X
```

Nunca debe existir un provider global mutable que represente el estado de una misión.

---

# 10. Independencia de TypeSafe en `pyproject.toml`

Propuesta:

```toml
[project]
dependencies = [
    # dependencias realmente necesarias para el core
]

[project.optional-dependencies]

dev = [
    ...
]

server = [
    ...
]

decision-laya = [
    "laya>=..."
]

decision-typesafe = [
    "typesafe-sdk>=0.7.0"
]

decision-all = [
    "laya>=...",
    "typesafe-sdk>=0.7.0"
]
```

Objetivo:

```bash
pip install praxeon
```

debe instalar únicamente el core.

Para LAYA:

```bash
pip install "praxeon[decision-laya]"
```

Para TypeSafe:

```bash
pip install "praxeon[decision-typesafe]"
```

Para todos:

```bash
pip install "praxeon[decision-all]"
```

Las versiones concretas deben comprobarse contra el lockfile antes de aplicar el cambio.

---

# 11. Imports opcionales

No basta con modificar `pyproject.toml`.

Si el core contiene:

```python
from typesafe_sdk import ...
```

en módulos importados durante el arranque, PRAXEON seguirá dependiendo de TypeSafe.

La integración debe quedar aislada:

```text
praxeon/
└── providers/
    ├── base.py
    ├── registry.py
    ├── laya.py
    ├── typesafe.py
    ├── replay.py
    └── mock.py
```

Y el SDK debe importarse exclusivamente dentro de `typesafe.py` o una capa equivalente.

Si no está instalado:

```text
DecisionProviderError:
TypeSafe provider is unavailable.
Install praxeon[decision-typesafe].
```

No debe romper:

```bash
import praxeon
praxeon benchmark
praxeon-server
```

---

# 12. TypeSafe como provider opcional

La integración TypeSafe debe seguir existiendo si aporta valor, pero conceptualmente debe ser:

```text
DecisionProvider
       │
       ├── LAYA
       ├── TypeSafe
       ├── KEV
       ├── Replay
       └── ...
```

No:

```text
PRAXEON
   ↓
TypeSafe
   ↓
LAYA
```

El runtime debe ser neutral.

---

# 13. API

Una misión debería aceptar algo similar a:

```json
{
  "llm": {
    "provider": "ollama",
    "model": "qwen3-coder:14b"
  },

  "decision_model": {
    "provider": "laya",
    "model_id": "laya-v1",
    "backend": "local",
    "device": "auto"
  },

  "execution": {
    "profile": "local_restricted"
  }
}
```

Esto permite:

```text
Mission A
LLM = Qwen
System-1 = LAYA

Mission B
LLM = Claude
System-1 = KEV
```

simultáneamente.

---

# 14. AgentDefinition

El agente también debería poder declarar:

```python
class AgentDefinition:
    identity: ...
    model: ModelConfig
    decision_model: DecisionModelConfig
    skills: ...
    tools: ...
    capabilities: ...
    risk_profile: ...
    context_policy: ...
```

Así un agente puede ser reutilizable:

```yaml
name: Developer Agent

model:
  provider: ollama
  model: qwen3-coder:14b

decision_model:
  provider: laya
  model_id: laya-v1
  backend: local
```

---

# 15. Decision Cascade

La arquitectura actual de router/cascade puede evolucionar hacia:

```yaml
decision:
  strategy: cascade

  providers:
    - model_id: laya-v1
      priority: 1
      threshold: 0.80

    - model_id: kev-v2
      priority: 2
      threshold: 0.85
```

Conceptualmente:

```text
Proposal
   ↓
LAYA
   ↓
confidence suficiente?
   ├── sí → Policy
   └── no
        ↓
       KEV
        ↓
      Policy
```

La cascada nunca debe saltarse:

```text
Evidence
Risk
Policy
Capability
Executor
```

---

# 16. Metadata de decisiones

`ProviderAssessment` debería incluir:

```text
provider_id
model_id
model_version
backend
confidence
latency_ms
calibration_profile
fallback_used
```

Ejemplo:

```json
{
  "provider": "laya",
  "model": "laya-v1",
  "model_version": "0.3",
  "backend": "local",
  "confidence": 0.91,
  "latency_ms": 4.8
}
```

Esto será fundamental para los futuros benchmarks LLM × System-1.

---

# 17. ContextManager

Existe una implementación bastante completa de `ContextManager`, incluyendo:

```text
DAG selection
token budget
context cache
fragment cache
fingerprints
deltas
invalidation
telemetry
```

Sin embargo, la ruta principal de Live Mission todavía construye gran parte de la conversación directamente.

La arquitectura objetivo debería ser:

```text
LLM
 ↓
Context Request
 ↓
ContextManager
 ├── DAG dependencies
 ├── Evidence
 ├── State
 ├── Cache
 ├── Summarization
 └── Budget
 ↓
LLM
```

El `ContextManager` debe convertirse en la única autoridad para construir el contexto que se envía al LLM.

---

# 18. Rollback

Existe una inconsistencia entre las diferentes capas de rollback/checkpoint.

Debe existir un único flujo:

```text
API
 ↓
SessionService
 ↓
CheckpointStore
 ↓
restore
 ↓
persist restored state
 ↓
invalidate descendants
 ↓
emit rollback event
```

El endpoint no debe devolver:

```json
{
  "status": "RolledBack"
}
```

si realmente solo emitió un evento sin restaurar el estado.

### Importante

Rollback de estado de PRAXEON:

```text
SÍ
```

Rollback de efectos externos:

```text
NO necesariamente
```

Una acción:

```text
send_email
delete_file
POST /api
```

puede no ser reversible.

---

# 19. Checkpoints

Hay señales de dos generaciones de API:

```text
save_checkpoint()
get_checkpoint()
get_latest_checkpoint()
list_checkpoints()
```

frente a consumidores que esperan operaciones tipo:

```text
create_checkpoint()
restore_checkpoint()
```

Debe existir una única interfaz de store.

Por ejemplo:

```python
class CheckpointStore(Protocol):
    def save(...)
    def get(...)
    def list(...)
    def restore(...)
```

Todos los servicios deben utilizar la misma abstracción.

---

# 20. Full Access

Actualmente Full Access sigue pasando por capability y SecureExecutor, lo cual es correcto.

Pero deben distinguirse:

```text
FULL_ACCESS_MANUAL
FULL_ACCESS_AUTONOMOUS
```

### Manual

```text
ALLOW
 ↓
Human confirmation
 ↓
Capability
 ↓
Execution
```

### Autónomo

```text
ALLOW
 ↓
Explicit autonomous policy
 ↓
Capability
 ↓
Execution
```

La UI debe mostrar claramente cuál está activo.

---

# 21. Sandbox

La arquitectura de sandbox es buena para el alcance del proyecto:

```text
LocalProcessSandbox
ContainerSandbox
DryRunSandbox
EgressPolicy
resource limits
path containment
```

Pero debe mantenerse la distinción:

```text
LocalProcessSandbox
≠
kernel isolation
```

Especialmente en Windows.

Docker/Podman y namespaces ofrecen un aislamiento diferente al proceso local.

No debe presentarse el sandbox local como equivalente a un contenedor endurecido.

---

# 22. SSRF / `base_url`

El campo `base_url` puede permitir que un cliente provoque peticiones del servidor hacia destinos arbitrarios.

En un entorno local controlado el riesgo es menor.

En un servidor expuesto:

```text
Client
 ↓
PRAXEON
 ↓
custom base_url
 ↓
internal service
```

puede convertirse en SSRF.

Recomendación:

```text
allow_custom_endpoints = false
```

por defecto.

Si se habilita:

- allowlist de hosts.
- validación de esquema.
- bloqueo de loopback.
- bloqueo de redes privadas según perfil.
- bloqueo de metadata endpoints.
- validación de redirecciones.
- configuración explícita por entorno.

---

# 23. Fallback del LLM

Debe distinguirse:

```text
FAIL_CLOSED
```

de:

```text
SYNTHETIC_FALLBACK
```

El fallback sintético es útil para:

```text
tests
demo
development
```

pero no debería ser silencioso en una misión autónoma.

La UI debería mostrar:

```text
⚠ DEGRADED MODE
LLM unavailable
Synthetic planner active
```

Y nunca convertir un fallo en:

```text
ALLOW
```

por defecto.

---

# 24. WebSocket

La arquitectura es buena:

```text
EventStore
 ↓
EventBus
 ↓
Async Queue
 ↓
WebSocket
 ↓
React
```

Se han contemplado:

- sequence numbers.
- gap recovery.
- event IDs.
- deduplicación.
- múltiples clientes.
- tickets efímeros.
- Origin.
- ping/pong.
- cleanup.

Debe mantenerse una prueba E2E de:

```text
connect
 ↓
receive
 ↓
disconnect
 ↓
reconnect
 ↓
after_sequence
 ↓
recover gap
```

---

# 25. Frontend

La interfaz necesita pasar de:

```text
Supervisor:
  LAYA
  TypeSafe
```

a:

```text
LLM
  Provider
  Model

Decision Model
  Provider
  Model
  Version
  Backend
  Device

Execution
  Profile
```

Además, algunas métricas de providers están actualmente hardcodeadas.

Eso debe cambiarse por:

```text
GET /v1/providers
```

o un endpoint equivalente.

Si una métrica no ha sido medida:

```text
N/A
```

en lugar de presentar un número estático.

---

# 26. Agent Registry

La evolución del proyecto ya proporciona una buena base:

```text
AgentDefinition
ModelConfig
RiskProfile
AgentContextPolicy
AgentRegistry
AgentService
AgentRouter
AgentMessageBus
```

Esto permite avanzar posteriormente hacia:

```text
Developer
Auditor
Researcher
Project Manager
Security Auditor
Writer
Data Analyst
```

pero no debería ser el foco antes de cerrar la independencia de modelos.

---

# 27. Workflow Engine

La semántica de workflow está bastante avanzada.

Se contemplan:

```text
TASK
AGENT
DISPATCH
WAITING_RESULT
COMPLETED
```

y ejecución paralela.

La parte que necesita consolidación es la persistencia y recuperación:

```text
WorkflowExecution
 ↓
SQLite
 ↓
restart
 ↓
resume
```

Debe existir una garantía explícita de qué ocurre después de un reinicio.

---

# 28. Runtime Application Service

El servicio principal concentra demasiadas responsabilidades:

```text
session management
decision evaluation
execution
mission worker
provider management
LLM integration
events
full access
fallback
trajectory control
```

Debe evolucionar hacia:

```text
RuntimeApplicationService
│
├── SessionRuntimeService
├── DecisionRuntimeService
├── ExecutionService
├── MissionRunner
├── ProviderRegistry
├── ModelRegistry
├── RecoveryService
└── TelemetryService
```

No es necesario hacerlo de golpe.

Pero sí es recomendable antes de seguir aumentando funcionalidades.

---

# 29. Benchmarks

Los benchmarks actuales son útiles, especialmente para enforcement.

Sin embargo, hay una diferencia entre:

### Enforcement benchmark

```text
path traversal
replay
tampering
SSRF
dangerous command
unknown tool
```

y:

### Agent/System-1 benchmark

```text
LLM proposal
+
trajectory
+
context
+
evidence
→
System-1
```

El segundo será necesario para demostrar la tesis futura de PRAXEON.

---

# 30. Benchmark principal futuro

La matriz experimental debería ser:

```text
                 SYSTEM-1
             LAYA   KEV   X   Y
            ┌───────────────────
Qwen 7B     │
Qwen 14B    │
Llama 8B    │
DeepSeek    │
Claude      │
GPT         │
```

Medir:

```text
Task success
False Allow
False Block
Loop rate
Recovery rate
LLM calls
System-1 calls
Tokens
Latency
RAM
VRAM
Cost
```

La métrica más crítica para PRAXEON seguirá siendo:

> **False Allow**

---

# 31. Benchmark reproducible

Cada resultado debe guardar:

```json
{
  "praxeon_version": "1.1.0",
  "commit_sha": "...",
  "benchmark_version": "...",
  "dataset_version": "...",
  "seed": 42,

  "python_version": "...",
  "os": "...",

  "hardware": {
    "cpu": "...",
    "ram_gb": 0,
    "gpu": "..."
  },

  "llm": {
    "provider": "...",
    "model": "..."
  },

  "decision_model": {
    "provider": "...",
    "model_id": "...",
    "version": "...",
    "backend": "..."
  },

  "config_hash": "..."
}
```

Esto es especialmente importante para el TFG.

---

# 32. Benchmarks históricos desincronizados

Durante la auditoría se detectó que algunos resultados almacenados en `benchmark_results/` no coinciden exactamente con las configuraciones/resultados que produce el código actual.

Por ejemplo:

- informes históricos con configuraciones distintas.
- número de configuraciones diferente.
- porcentajes diferentes a una ejecución actual.

Esto no implica que los resultados históricos sean inválidos.

Significa que deben etiquetarse como:

```text
historical
```

y regenerarse con el código/dataset actuales antes de usarlos como resultados principales.

---

# 33. CI/CD

Actualmente existen workflows en:

```text
.ci/workflows/
.github/workflows/
```

Debe existir una fuente canónica:

```text
.github/workflows/
```

El pipeline debe comprobar:

```text
pytest
lint
frontend build
wheel build
wheel install
CLI smoke test
server startup
API E2E
WebSocket E2E
```

Especialmente:

```bash
python -m build
pip install dist/*.whl
praxeon --help
praxeon benchmark
praxeon-server --help
```

---

# 34. Tests

Durante la auditoría anterior se registraron:

```text
788 tests collected
```

y grupos concretos:

```text
52 passed, 1 skipped
133 passed
68 passed
```

Además:

```text
compileall → OK
```

La ejecución completa de la suite no pudo confirmarse en el tiempo disponible.

Un test del adaptador TypeSafe falló en el entorno de auditoría por:

```text
No module named 'typesafe_sdk'
```

Esto no debe interpretarse automáticamente como bug de PRAXEON porque el entorno no tenía instalada esa dependencia opcional.

Sin embargo, es precisamente una evidencia adicional de por qué TypeSafe debe quedar aislado del core.

Antes del release hay que ejecutar una matriz limpia:

```text
core
core + LAYA
core + TypeSafe
all providers
```

---

# 35. Naming JEV

Todavía existen elementos históricos como:

```text
jev_engine.py
JEVEngine
JEVDashboard
JEVProxyMiddleware
JEVScore
JEVConfig
jev_v2_*
```

Esto genera deuda conceptual.

La arquitectura pública debería migrar hacia:

```text
DecisionEngine
DecisionProvider
DecisionAssessment
DecisionRuntime
PraxeonRuntime
```

Los aliases `jev_*` pueden permanecer temporalmente como compatibilidad.

---

# 36. Lista completa de mejoras

| Prioridad | Hallazgo | Acción |
|---|---|---|
| P0 | Provider global mutable | Provider por `SessionRuntime`. |
| P0 | Sin `DecisionModelConfig` completo | Crear configuración tipada. |
| P0 | TypeSafe obligatorio | Convertir en extra opcional. |
| P0 | Rollback inconsistente | Unificar restauración real. |
| P0 | Falta E2E de aislamiento | Dos misiones con LLM/System-1 distintos. |
| P1 | ContextManager parcialmente integrado | Convertirlo en ruta principal. |
| P1 | Checkpoint APIs divergentes | Unificar contrato. |
| P1 | Full Access ambiguo | Manual vs autónomo. |
| P1 | Métricas UI estáticas | Telemetría real desde backend. |
| P1 | Benchmarks desincronizados | Regenerar/versionar. |
| P1 | Sin metadata completa | Commit, seed, hardware, modelos. |
| P1 | Poco benchmark con trayectorias reales | Dataset de agentes. |
| P1 | SSRF vía `base_url` | Allowlist y perfiles de endpoint. |
| P1 | Misión en memoria | Persistencia/recovery. |
| P1 | Runtime service demasiado grande | Refactor por responsabilidades. |
| P1 | CI duplicado | Unificar workflows. |
| P2 | Naming JEV | Migración a Decision*. |
| P2 | Router de agentes | Mantener experimental. |
| P2 | Workflow persistence | Resume tras restart. |
| P2 | Multi-agent | Después de benchmark. |
| P2 | Branching | Después de eficiencia. |
| P2 | Auth frontend | Evaluar cookies HttpOnly para multiusuario. |

---

# 37. Plan de implementación

## Fase 0 — Correcciones P0

1. Corregir rollback.
2. Unificar checkpoint contract.
3. Eliminar provider global mutable.
4. Añadir aislamiento por sesión.
5. Crear tests de concurrencia entre diferentes providers.

---

## Fase 1 — PRAXEON 1.1: Decision Model Independence

1. `DecisionProvider`.
2. `DecisionModelConfig`.
3. `DecisionProviderRegistry`.
4. `DecisionRuntime`.
5. `LayaProvider`.
6. `TypeSafeProvider`.
7. `ReplayProvider`.
8. `MockProvider`.
9. Dependencias opcionales.
10. Metadata de provider/modelo.
11. Configuración por sesión.
12. Configuración por agente.
13. API.
14. UI.
15. Tests de conformidad.

---

## Fase 2 — UI y observabilidad

Selector:

```text
LLM Provider
LLM Model

System-1 Provider
System-1 Model
Backend
Device

Policy
Execution Profile
```

Mostrar:

```text
provider
model
version
backend
latency
confidence
fallback
availability
```

---

## Fase 3 — Contexto y recuperación

Integrar:

```text
Live Mission
 ↓
ContextManager
 ↓
LLM
```

y:

```text
Session
 ↓
SQLite
 ↓
restart
 ↓
recovery
```

---

## Fase 4 — Evaluación científica

Crear:

```text
LLM × System-1
```

y comparar:

```text
sin supervisor
PRAXEON + System-1
PRAXEON + cascade
```

Medir:

```text
quality
false allow
false block
loops
recovery
tokens
latency
RAM/VRAM
```

---

## Fase 5 — Evolución posterior

Solo después:

```text
Branching
Top-K
Agent Router
Multi-Agent
Adaptive Runtime
```

La expansión debe estar condicionada a resultados medibles.

---

# 38. Criterios de aceptación de PRAXEON 1.1

La independencia de providers puede considerarse completada cuando:

- [ ] `pip install praxeon` funciona sin TypeSafe.
- [ ] Importar el core no importa `typesafe_sdk`.
- [ ] LAYA funciona mediante su extra.
- [ ] TypeSafe funciona mediante su extra.
- [ ] Providers implementan el mismo contrato.
- [ ] LLM y System-1 pueden seleccionarse independientemente.
- [ ] Dos misiones concurrentes pueden utilizar modelos diferentes.
- [ ] Provider timeout/failure no produce ALLOW implícito.
- [ ] Cada assessment registra provider/model/version/backend.
- [ ] API devuelve la configuración efectiva.
- [ ] UI permite elegir el modelo System-1.
- [ ] Full Access conserva policy/capability/executor.
- [ ] Anti-replay sigue funcionando.
- [ ] Rollback restaura realmente el estado.
- [ ] Benchmarks incluyen metadata reproducible.
- [ ] CI instala y prueba el wheel.
- [ ] README y badges coinciden con el estado real.

---

# 39. Principio arquitectónico definitivo

La evolución de PRAXEON debe evitar este acoplamiento:

```text
PRAXEON
   ↓
LAYA
```

y también:

```text
PRAXEON
   ↓
TypeSafe
```

La arquitectura correcta es:

```text
                   PRAXEON
                      │
             ┌────────┴────────┐
             │                 │
          LLM Runtime     Decision Runtime
             │                 │
       ┌─────┼─────┐     ┌─────┼─────┐
       │     │     │     │     │     │
      GPT   Qwen  Llama  LAYA  KEV   ...
             │                 │
             └────────┬────────┘
                      │
                Enforcement
                      │
               Policy/Capability
                      │
                  Execution
```

El modelo de decisión es reemplazable.

La autoridad pertenece al runtime.

---

# 40. Conclusión

PRAXEON está en un punto en el que **la consolidación arquitectónica es más importante que añadir nuevas features**.

El núcleo de runtime es suficientemente maduro como para que el siguiente salto tenga sentido: desacoplar completamente el modelo que razona del modelo que decide.

La meta no debe ser:

> “Cambiar TypeSafe por LAYA.”

Debe ser:

> **“Hacer que PRAXEON sea independiente del modelo System-1.”**

Eso permite:

```text
Qwen + LAYA
Qwen + KEV
Qwen + Model X

Claude + LAYA
Claude + KEV

Llama + LAYA
Llama + Model X
```

y eventualmente:

```text
LLM A
   ↓
System-1 A
   ↓
PRAXEON

LLM B
   ↓
System-1 B
   ↓
PRAXEON
```

manteniendo siempre:

```text
Reasoning
   ↓
Evidence
   ↓
Risk
   ↓
System-1
   ↓
Policy
   ↓
Capability
   ↓
Execution
```

La frase que mejor resume la arquitectura final sigue siendo:

> **The LLM proposes. The System-1 evaluates. PRAXEON decides what gets executed.**

---

## Prioridad inmediata

El orden recomendado es:

```text
1. Rollback + checkpoint
          ↓
2. Provider isolation
          ↓
3. DecisionModelConfig
          ↓
4. DecisionProviderRegistry
          ↓
5. TypeSafe optional
          ↓
6. LLM/System-1 selection
          ↓
7. API + UI
          ↓
8. ContextManager integration
          ↓
9. Reproducible benchmarks
          ↓
10. Branching / Agent Router / Multi-Agent
```

**No se recomienda continuar ampliando branching, multi-agent o agent routing hasta completar los puntos P0 y demostrar mediante benchmarks que la nueva arquitectura de modelos aporta una mejora medible.**
