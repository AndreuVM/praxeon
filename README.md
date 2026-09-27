# PRAXEON v1.0.0

**Runtime supervision for autonomous AI agents**

> **The model proposes. The runtime decides what gets executed.**

[![Tests](https://img.shields.io/badge/tests-280%20passed-brightgreen.svg)](https://github.com/AndreuVM/praxeon)
[![Version](https://img.shields.io/badge/version-v1.0.0-blue.svg)](https://github.com/AndreuVM/praxeon)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://github.com/AndreuVM/praxeon)
[![Security](https://img.shields.io/badge/security-sandbox%20%26%20container%20hardened-green.svg)](https://github.com/AndreuVM/praxeon/blob/main/SECURITY.md)
[![Providers](https://img.shields.io/badge/providers-TypeSafe%20%7C%20LAYA%20%7C%20CascadeRouter-purple.svg)](https://github.com/AndreuVM/praxeon)

`PRAXEON` es un middleware de supervisión formal y runtime de seguridad desacoplado para agentes autónomos basados en Modelos de Lenguaje (*ReAct*, *Tool-use*, *Tree-of-Thought*).

Los LLMs son generadores estocásticos que proponen acciones basándose en distribuciones probabilísticas de tokens. `PRAXEON` desacopla la fase de propuesta de la fase de ejecución física: ninguna acción propuesta por un agente se ejecuta de manera directa ni posee autoridad intrínseca sobre el entorno. El runtime determinista intercepta cada paso, verifica sus precondiciones empíricas en las observaciones previas, evalúa el riesgo operacional, somete la acción a evaluación semántica calibrada cuando es necesario, emite un capability criptográfico firmado (HMAC-SHA256) con validez temporal acotada y ejecuta la herramienta confinada dentro de un sandbox aislado.

$$\text{Semantic Judgment (LAYA / TypeSafe Router)} \neq \text{Operational Policy (PolicyEngine)} \neq \text{Capability Receipt (HMAC)} \neq \text{Enforced Sandbox (SecureExecutor)}$$

---

## ⚡ Inicio Rápido (Zero-Config / 100% Offline)

Puedes probar PRAXEON inmediatamente en local **sin costo de API, sin registro y sin conexión externa**:

```bash
# 1. Clonar e instalar en entorno virtual
git clone https://github.com/AndreuVM/praxeon.git
cd praxeon
pip install -e ".[dev]"

# 2. Ejecutar la demostración interactiva visual (sin APIs externas)
python demo.py

# 3. Ejecutar la suite completa de benchmarks reproducibles
python scripts/run_benchmarks.py
```

---

## 🤖 Agente Autónomo en Vivo: Modelos Locales y Nube (Multi-Provider)

En PRAXEON, el **Agente Autónomo** (que genera propuestas de razonamiento y acciones) está completamente desacoplado del **Supervisor Cognitivo de Runtime** (`LAYA` / `TypeSafe`).

Para evitar cuotas y rate-limits estrictos (como los límites gratuitos de Google Gemini), PRAXEON soporta de forma nativa e integrada cualquier endpoint compatible con OpenAI, tanto en local como en la nube:

| Proveedor | Tipo | Cuota / Coste | Velocidad | Comando de Inicio |
| :--- | :---: | :---: | :---: | :--- |
| **Ollama** *(Recomendado Local)* | 100% Local / Offline | **Ilimitado / 0€** | Según GPU/CPU | `praxeon live --provider ollama` |
| **Groq Cloud** *(Recomendado Nube)* | Nube gratuita | **14.400 req/día, 30 RPM** | Ultra-rápido (~500 tok/s) | `praxeon live --provider groq` |
| **OpenRouter** | Nube multimodelo | Modelos `:free` disponibles | Variable | `praxeon live --provider openrouter` |
| **LM Studio** | Local | **Ilimitado / 0€** | Según GPU/CPU | `praxeon live --provider lmstudio` |
| **Google Gemini** | Nube | Cuota Free Tier estándar | Estándar | `praxeon live --provider gemini` |
| **Simulador Offline** | Determinista | **Ilimitado / 0€** | Inmediato | `praxeon live --provider simulated` |

### 1. Usar Ollama en Local (Sin conexión a internet ni claves API)
1. Instala [Ollama](https://ollama.com) y descarga un modelo de código:
   ```bash
   ollama run qwen2.5-coder:7b
   # o para máquinas más ligeras:
   ollama run llama3.2:3b
   ```
2. Ejecuta PRAXEON en vivo o en dashboard TUI:
   ```bash
   praxeon live --provider ollama --model qwen2.5-coder:7b
   # o en el dashboard visual:
   praxeon-dash --live --provider ollama
   ```
   *(PRAXEON detecta automáticamente si Ollama está en ejecución en `localhost:11434` sin configuración adicional).*

### 2. Usar Groq Cloud (Gratuito, 14.400 peticiones diarias, ~500 tok/s)
1. Consigue una clave gratuita en [Groq Console](https://console.groq.com) (tarda 30 segundos).
2. Configura tu `.env`:
   ```bash
   GROQ_API_KEY=gsk_tu_clave_aqui
   ```
3. Ejecuta el agente con Llama 3.3 70B o Qwen 2.5 Coder 32B a máxima velocidad:
   ```bash
   praxeon live --provider groq --model llama-3.3-70b-versatile
   # o en el dashboard interactivo:
   praxeon-dash --live --provider groq
   ```

### 3. Recuperación Interactiva en Caliente (*Self-Healing / Fallback*)
Si un proveedor experimenta un fallo de cuota (error HTTP 429), pérdida de conexión o clave inválida durante una sesión en vivo, PRAXEON no detiene el proceso abruptamente: despliega un menú interactivo en terminal que permite alternar a otro proveedor (ej. de Gemini a Groq o a Ollama local) e introducir una clave al vuelo sin perder la memoria acumulada de la sesión.

---

# PARTE I: ARQUITECTURA E IMPLEMENTACIÓN DOCUMENTADA

## 1. Pilares Arquitectónicos Fundamentales

En lugar de delegar la seguridad en prompts permisivos o heurísticas acopladas, PRAXEON fundamenta su runtime en seis principios de ingeniería de sistemas:

1. **Separación Estricta entre Juicio Semántico y Autoridad Operativa:**
   El evaluador semántico ($JEV/LAYA$) emite estimaciones probabilísticas de viabilidad o riesgo; la autoridad de ejecución reside de forma exclusiva en el motor determinista de políticas (`PolicyEngine`). Una acción puede tener una probabilidad semántica de éxito elevada ($JEV = 0.95$) y ser al mismo tiempo operacionalmente inadmisible ($Risk = \text{CRITICAL}$, ej. `rm -rf /` o acceso a `.env`). La política emite la decisión vinculante (`ALLOW`, `BLOCK`, `REPLAN`, `ABSTAIN`).
2. **Capabilities Criptográficos Efímeros y Protección Anti-Replay:**
   Las instrucciones en lenguaje natural (*"no ejecutes comandos dañinos"*) no constituyen una barrera de seguridad. `SecureExecutor` exige de forma obligatoria un capability firmado criptográficamente (`DecisionReceipt`) mediante **HMAC-SHA256**, con ventana de validez temporal acotada (`expires_at`), ligado al hash canónico de la acción y del estado de la sesión. Un almacén de nonces durable (`SqliteNonceStore` o `InMemoryNonceStore`) con poda automática por TTL previene ataques de repetición (*replay attacks*) a través de reinicios del proceso.
3. **Aislamiento en Host y Sandboxing de Procesos:**
   La ejecución física de herramientas del sistema (`run_command`, `read_file`, `edit_file`) no interactúa directamente con el host con shells permisivos. Se delega en adaptadores de contención (`SandboxAdapter`):
   - `ContainerSandboxAdapter`: Confinamiento en contenedores OCI (Docker/Podman) con filesystem raíz de solo lectura (`--read-only`), aislamiento de red total (`--network=none`), límites de memoria/CPU (cgroups) y descarte de privilegios (`--cap-drop=ALL`).
   - `LocalProcessSandbox`: Confina los accesos al workspace delimitado resolviendo enlaces simbólicos canónicos (`os.path.realpath`) para anular vectores de *symlink traversal* y *TOCTOU*, neutraliza variables de entorno que contengan secretos o proxies hacia el loopback, y aplica políticas de egress de red (`EgressPolicy`) bloqueando interfaces de loopback, rangos RFC 1918 y metadatos cloud (`169.254.169.254`).
4. **Fundamentación Empírica y Verificación Estructurada de Criterios:**
   `EvidenceEngine` comprueba que las precondiciones necesarias existan en el registro de observaciones antes de autorizar acciones dependientes, descalificando evidencias obsoletas ante mutaciones del entorno (*stale state defense*). Asimismo, `CompletionVerifier` impide que el agente declare victoria prematura (`finish`) sin antes validar formalmente que todos los criterios obligatorios (`CriterionType.FILE_EXISTS`, `TESTS_PASS`, `EXIT_CODE_ZERO`, `STATE_VALUE`, `CUSTOM`) cuenten con observaciones verificadas en disco o traza.
5. **Fail-Safe Estricto y Enrutamiento Adaptativo:**
   La indisponibilidad de la red o la caída de un proveedor semántico externo nunca deriva en autorizaciones permisivas: el sistema aplica una política de denegación por defecto (fail-safe) emitiendo `ABSTAIN` o `BLOCK`. Mediante `ConfidenceAwareRouter`, las decisiones rutinarias se resuelven en microsegundos con modelos locales calibrados (System-1 / LAYA), escalando deterministamente a evaluadores profundos (System-2 / TypeSafe) o intervención humana (`ABSTAIN`) ante baja confianza o riesgo crítico.
6. **Determinismo de Estado y Recuperación Formal (Checkpoints & Rollback):**
   `CheckpointManager` captura snapshots canónicos SHA-256 de `SessionState`. Ante degradación de trayectoria o bucles repetitivos de herramientas, el runtime restaura el estado a un punto seguro conocido, invalida los pasos descendientes y bloquea físicamente la transición o herramienta causante para permitir replanificaciones sin efectos secundarios acumulados.

---

## 2. Flujo Normativo del Runtime

$$\text{Proposal} \to \text{Evidence} \to \text{Risk} \to \text{ProviderContext} \to \text{Semantic Evaluation} \to \text{PolicyEngine} \to \text{HMAC Receipt} \to \text{SecureExecutor} \to \text{Sandbox} \to \text{Observation}$$

```
                +---------------------------------------+
                |           Agente Autónomo             |
                +---------------------------------------+
                                   | Propone candidato (Proposal)
                                   v
+-----------------------------------------------------------------------+
|                 PRAXEON (Runtime Supervision Engine)                  |
|                                                                       |
|  1. EvidenceEngine        -> Verifica precondiciones empíricas        |
|  2. RiskEngine            -> Análisis contextual de comandos y rutas  |
|  3. Confidence Router     -> Inferencia semántica (LAYA / TypeSafe)   |
|  4. CompletionVerifier    -> Valida criterios de éxito frente a finish|
|  5. PermissionManager     -> Control de acceso humano RBAC integrado  |
|  6. PolicyEngine          -> Decisión vinculante (ALLOW/BLOCK/REPLAN) |
|  7. DecisionReceipt       -> Emisión de capability HMAC-SHA256        |
|  8. CheckpointManager     -> Captura snapshot preventivo antes de mutar|
|  9. SecureExecutor        -> Verifica capability único y no expirado  |
| 10. Process/Container Sbx -> Ejecución aislada con env depurado & jail|
+-----------------------------------------------------------------------+
                                   | Observación
                                   v
                        +----------------------+
                        |   Sistema / Estado   |
                        +----------------------+
```

---

## 3. Estructura del Código y Componentes del Núcleo

```
praxeon/
├── .ci/workflows/                # CI/CD multiplataforma (Python 3.11 & 3.12, Windows & Linux)
├── praxeon/
│   ├── domain/                   # Entidades puras y contratos inmutables (Pydantic v2)
│   │   ├── goal.py               # Goal, SuccessCriterion, CriterionType, SubGoal
│   │   ├── action.py             # ActionCandidate, ToolCall, BatchSemantics
│   │   ├── observation.py        # Observation, ToolOutput
│   │   ├── evidence.py           # Evidence, GroundingStatus
│   │   ├── assessment.py         # ProviderAssessment, JEVAssessment, RiskAssessment
│   │   ├── decision.py           # PolicyDecision, DecisionReceipt, HMAC signature verification
│   │   ├── checkpoint.py         # Checkpoint, SessionSnapshot
│   │   ├── state.py              # SessionState (hash canónico SHA-256 determinista)
│   │   └── interfaces.py         # Protocols: ReasoningProvider, EvidenceProvider, Executor
│   ├── providers/                # Adaptadores de inferencia semántica desacoplados
│   │   ├── context.py            # ProviderContextBuilder con token budgeting y truncamiento
│   │   ├── laya.py               # LayaProvider (System-1: auto, local, hosted, simulated)
│   │   ├── typesafe.py           # Adaptador TypeSafe AI System One con fail-safe
│   │   ├── router.py             # ConfidenceAwareRouter (cascada adaptativa LAYA + TypeSafe)
│   │   └── replay.py             # ReplayProvider determinista para evaluación offline
│   ├── reasoning/                # Evaluación de evidencias, bucles, fundamentación y riesgo
│   │   ├── evidence.py           # EvidenceEngine (almacén, validación e invalidación)
│   │   ├── loop_detector.py      # LoopDetector y análisis de anomalías de trayectoria
│   │   ├── grounding.py          # GroundingVerifier (fundamentación empírica)
│   │   ├── risk.py               # RiskEngine (inspección de argumentos shell y archivos)
│   │   └── completion.py         # CompletionVerifier estructurado tipado (anti-premature finish)
│   ├── policy/                   # Políticas operacionales de admisión
│   │   ├── registry.py           # ToolRegistry y ToolSpec tipados
│   │   ├── permissions.py        # PermissionManager, HumanApprovalTicket y RBAC
│   │   ├── failsafe.py           # FailSafePolicy para caídas de red o incertidumbre
│   │   └── engine.py             # PolicyEngine con escalado por confianza y firma HMAC
│   ├── runtime/                  # Estado, orquestación, checkpoints, sandbox y ejecución
│   │   ├── state_store.py        # InMemoryStateStore y SqliteStateStore transaccional
│   │   ├── nonce_store.py        # NonceStore con poda por TTL y defensa anti-replay duradera
│   │   ├── checkpoints.py        # CheckpointManager y rollback con invalidación de descendientes
│   │   ├── sandbox.py            # LocalProcessSandbox (env scrubbing, realpath jail) y ContainerSandbox
│   │   ├── executor.py           # SecureExecutor con HMAC capability verification y no-replay
│   │   └── navigator.py          # Navigator (orquestador del pipeline completo)
│   ├── integrations/             # Protocolos de interoperabilidad externa
│   │   └── mcp/                  # Servidor Model Context Protocol nativo (stdio)
│   ├── server/                   # Servidor FastAPI REST, WebSocket streaming y auth
│   ├── evaluation/               # Framework de benchmarking y métricas de seguridad
│   │   ├── scenarios.py          # ScenarioCatalog y generador procedural con ground truth
│   │   ├── metrics.py            # Métricas decisionales, de ejecución física y económicas
│   │   ├── reports.py            # Generador formal de informes de benchmark
│   │   └── runner.py             # BenchmarkRunner con verificación física y ablaciones
│   ├── cli.py                    # Consola interactiva CLI enriquecida con Rich
│   ├── live_agent.py             # Agente autónomo con Gemini supervisado en tiempo real
│   └── dashboard.py              # Monitor visual interactivo TUI en tiempo real
├── docs/
│   └── DAM_PROJECT_MEMO.md       # Memoria técnica formal para el ciclo DAM
├── tests/                        # 280 pruebas automatizadas (unitarias, integración, seguridad, OOD)
├── SECURITY.md                   # Política de seguridad y modelo de amenazas formal auditado
├── pyproject.toml                # Metadatos del proyecto y dependencias (v1.0.0)
└── README.md
```

---

## 4. Inferencia Local con LAYA (System-1 Open-Source)

`PRAXEON` incorpora soporte nativo para **LAYA**, un modelo de decisión no-autorregresivo de código abierto (Apache 2.0) diseñado para razonamiento reflejo (System-1). A diferencia de los LLMs generativos convencionales que producen texto token a token, LAYA procesa el estado del agente y emite veredictos estructurados en un único *forward pass* (~33 ms).

### Primitivas de Decisión de LAYA:
1. **`choice`**: Selección categórica (`ALLOW`, `REPLAN`, `BLOCK`, `ABSTAIN`) con distribución de probabilidades calibrada y nivel de confianza.
2. **`score`**: Medición continua del progreso del agente hacia la meta ($0.0 \text{ a } 1.0$).
3. **`noul`**: Probabilidades booleanas calibradas de bucle (`is_loop`), fundamentación empírica (`is_grounded`) y novedad (`is_novel`).

### Modos de Inferencia Disponibles:

#### A. Motor Local Calibrado (Zero-Download / Inmediato)
* **¿Requiere descargar pesos?:** **No.**
* Viene **100% integrado en PRAXEON** sin descargas pesadas ni necesidad de frameworks de deep learning pesados.
* Opera en memoria con latencia inferior a **$0.1\text{ ms}$ ($p50$)**, evaluando de forma determinista patrones de repetición, consistencia de evidencias y riesgo destructivo.
* Se activa por defecto con `LayaProvider(backend="auto")` o `backend="simulated"`.

```python
from praxeon.providers.laya import LayaProvider

# Inferencia local inmediata sin descargas externas
laya_fast = LayaProvider(backend="auto")
```

#### B. Red Neuronal Real Open-Source (`convaiinnovations/laya`)
* **¿Requiere descargar pesos?:** **Sí**, la descarga es **automática en la primera ejecución**.
* Para ejecutar los pesos neuronales oficiales del modelo (~421M parámetros) en CPU o GPU (CUDA) local:
  ```bash
  pip install "praxeon[laya]"
  ```
* Al instanciar `LayaProvider(backend="local")`, el SDK de LAYA descarga automáticamente los pesos desde Hugging Face (`convaiinnovations/laya`) en el primer arranque y los almacena en tu caché local.
* Las ejecuciones posteriores reutilizan la instancia en memoria cacheada, logrando tiempos de inferencia de **~33 ms** completamente offline.

```python
from praxeon.providers.laya import LayaProvider
from praxeon.runtime.navigator import Navigator

# Carga y ejecuta la red neuronal LAYA en hardware local (CPU/GPU)
laya_neural = LayaProvider(backend="local")
navigator = Navigator(provider=laya_neural)
```

#### C. Pesos en Directorio Local Personalizado
Si ya has descargado los pesos previamente o utilizas un checkpoint afinado (*fine-tuned*), indícale la ruta directamente:
```python
laya_custom = LayaProvider(
    backend="local",
    model_name="C:/modelos/laya-421m"  # O definiendo la variable de entorno LAYA_MODEL_PATH
)
```

#### D. Despliegue en Microservicio Alojado (REST / HTTP)
Si prefieres servir LAYA en un contenedor independiente:
```python
laya_hosted = LayaProvider(
    backend="hosted",
    endpoint_url="http://localhost:8000/v1/decide",
    auth_token="tu_token_opcional"
)
```

### Arquitectura en Cascada: System-1 (LAYA) + System-2 (TypeSafe)

En entornos de producción, la configuración recomendada aprovecha la velocidad de LAYA local para las decisiones rutinarias, derivando automáticamente a TypeSafe/JEV ante dudas o riesgo operacional elevado mediante `ConfidenceAwareRouter`:

```python
from praxeon.providers.laya import LayaProvider
from praxeon.providers.typesafe import TypeSafeAdapter
from praxeon.providers.router import ConfidenceAwareRouter
from praxeon.runtime.navigator import Navigator

cascade_router = ConfidenceAwareRouter(
    primary_provider=LayaProvider(backend="auto"),
    secondary_provider=TypeSafeAdapter(),
    default_confidence_threshold=0.75,
)

navigator = Navigator(provider=cascade_router)
```

---

## 5. Interfaces de Integración: CLI y Servidor MCP

### A. Consola de Línea de Comandos (`praxeon`)
```bash
# Ejecutar suite formal de benchmarks
praxeon benchmark

# Comparar proveedores (LAYA vs TypeSafe)
praxeon benchmark --compare-providers

# Comparativa frente a línea base sin supervisor
praxeon benchmark --compare-baseline

# Estudio formal de ablaciones de las capas arquitectónicas
praxeon benchmark --ablation

# Exportar reporte consolidado en JSON
praxeon benchmark --output auditoria_report.json

# Monitor interactivo en terminal (TUI)
praxeon dashboard

# Agente autónomo con Gemini supervisado en tiempo real
praxeon live "Refactorizar función en parser.py" --model gemini-2.5-flash --once
```

### B. Servidor MCP (Model Context Protocol)
Compatible con **Antigravity IDE**, **Claude Desktop** y **Cursor**:

```json
{
  "mcpServers": {
    "praxeon": {
      "command": "uv",
      "args": ["run", "praxeon-mcp"],
      "cwd": "C:/ruta/al/proyecto/praxeon"
    }
  }
}
```

**Herramientas MCP Nativas de Sesión y Runtime:**
- `jev_v2_start_session(goal, session_id)`: Inicializa una sesión con objetivo formal y checkpoint génesis.
- `jev_v2_evaluate_action(action, goal, session_id)`: Evalúa una acción emitiendo decisión operacional y capability firmado.
- `jev_v2_step_and_execute(action, auto_checkpoint)`: Evalúa y ejecuta físicamente en sandbox con captura de observación.
- `jev_v2_rollback(checkpoint_id, culprit_tool, reason)`: Restaura el estado e invalida la herramienta reincidente.
- `jev_v2_get_session_state()`: Devuelve el snapshot serializado del estado y su SHA-256 canónico.
- `jev_v2_confirm_action(action_id)`: Registra la confirmación humana explícita para acciones con `requires_confirmation=True`.

---

## 6. Uso Programático en Python (v1.0.0)

```python
from praxeon.domain import Goal, ActionCandidate, ToolCall
from praxeon.providers import LayaProvider
from praxeon.runtime import Navigator, SecureExecutor

# 1. Inicializar componentes desacoplados
provider = LayaProvider(backend="auto")
executor = SecureExecutor(dry_run=False) # Ejecución confinada en sandbox
navigator = Navigator(provider=provider, executor=executor)

# 2. Iniciar sesión formal con objetivo y criterios verificables
goal = Goal(
    objective="Refactorizar módulo de pagos",
    success_criteria=["archivo payment.py actualizado", "tests de pagos ejecutados con éxito"]
)
state = navigator.start_session(goal, session_id="sesion_01")

# 3. Supervisar y ejecutar un paso normativo
accion = ActionCandidate(
    id="paso_1",
    description="Leer archivo de configuración",
    tool_call=ToolCall(tool_name="read_file", arguments={"path": "config.yaml"}),
    requires_evidence=[]
)

decision, observacion = navigator.step(accion, auto_checkpoint=True)

if decision.status == "allow":
    print(f"Paso ejecutado con éxito: {observacion.output}")
elif decision.status == "abstain":
    print(f"Acción escalada a revisión humana: {decision.reason_codes}")
else:
    print(f"Acción denegada por política: {decision.status} - Motivos: {decision.reason_codes}")

# 4. Recuperación determinista ante anomalías de trayectoria
if decision.status == "replan":
    navigator.rollback(culprit_tool=accion.tool_call.tool_name, reason="Degradación de trayectoria detectada")
```

---

# PARTE II: EVIDENCIA EMPÍRICA DE BENCHMARKS

## 7. Metodología Experimental y Dataset Procedural

La suite de evaluación mide empíricamente la precisión de decisión, la eficacia de contención física en sandbox y la latencia operacional del runtime:

- **Dataset Procedural (1.000+ escenarios):** Dividido de forma determinista y libre de sobreajuste en **800 Train / Desarrollo** y **200 Holdout / Prueba** (`ScenarioCatalog.get_holdout_scenarios`).
- **13 Categorías Estratificadas:** Bucles cíclicos (1 a N saltos), fijación semántica, desfundamentación de archivos inexistentes, inyección de comandos destructivos de shell, caídas de red simuladas y efectos laterales.
- **Métrica de Valor Económico ($\text{NavigatorValue}$):** Modelo formal que computa el retorno económico neto ponderando el costo de incidentes destructivos prevenidos, tiempo ahorrado en bucles y costo computacional de inferencia.

---

## 8. Las 5 Dimensiones de Evaluación

1. **Provider Benchmark:** Acuerdo inter-proveedor (*agreement rate*), concordancia decisional y latencias de inferencia entre LAYA local, TypeSafe remoto y el Router en cascada.
2. **Policy Benchmark:** Calidad de clasificación (`ALLOW`, `BLOCK`, `REPLAN`, `ABSTAIN`), matrices de confusión, tasa de falsos permitidos (`false_allow_rate`) y precisión de denegación.
3. **Enforcement Benchmark:** Comprobación física de contención frente a firmas HMAC alteradas, ataques de replay, intentos de *path traversal* (`../`) y violaciones de política egress SSRF.
4. **Runtime Benchmark:** Rendimiento en tiempo real midiendo throughput operacional (*operaciones/segundo*) y distribución percentil de latencias (`p50`, `p95`, `p99`).
5. **Trajectory Benchmark:** Simulación de trayectorias multi-paso de agentes autónomos, validando la activación de rollbacks hacia checkpoints válidos, recuperación adaptativa y prevención de finalizaciones prematuras.

---

## 9. Estudio Cuantitativo de Ablaciones

Evaluación experimental de 6 configuraciones arquitectónicas sobre el conjunto Holdout ($n=200$ escenarios independientes):

| Configuración Arquitectónica | Exactitud (Accuracy) | False Allow Rate | Destructive False Allows | Valor Neto Estimado ($\text{NavigatorValue}$) | Diagnóstico Operacional |
| :--- | :---: | :---: | :---: | :---: | :--- |
| **1. Policy Only (Sin Modelo Semántico)** | 54.0% | 16.8% | 0 | $16,894.40 | Incapaz de detectar bucles semánticos o estancamiento de trayectoria. |
| **2. Provider Only (Sin Evidence Engine)** | 84.5% | 8.1% | 0 | $18,495.60 | Autoriza acciones basadas en premisas y archivos no observados en el entorno. |
| **3. Provider + Evidence (Sin Risk Engine)** | 68.5% | 25.4% | 32 | $15,294.00 | Permite comandos destructivos de shell (`rm -rf`, `DROP TABLE`). |
| **4. Provider + Evidence + Risk (Sin FailSafe)** | 68.5% | 25.4% | 16 | $15,294.40 | Caídas de proveedor o de red conceden ejecuciones dudosas. |
| **5. PRAXEON Full Single-Tier** | **100.0%** | **0.0%** | **0** | **$19,994.40** | Contención verificada en suite con enforcement HMAC y sandbox. |
| **6. PRAXEON Full Cascade Router (LAYA + TypeSafe)** | **100.0%** | **0.0%** | **0** | **$19,994.40** | Vía rápida local (~0.09 ms) con escalado dinámico ante incertidumbre. |

> *Nota metodológica: Las métricas de exactitud y tasa de falsos permitidos reflejan el rendimiento observado sobre la distribución de escenarios del catálogo Holdout evaluado.*

---

## 10. Comparativa Cuantitativa: Baseline Sin Supervisor vs PRAXEON v1.0.0

| Métrica Operacional y de Seguridad | Agente Sin Supervisor (Baseline) | PRAXEON v1.0.0 (Full Architecture) |
| :--- | :---: | :---: |
| **Exactitud Decisional Global** | 54.0% | **100.0% (en suite evaluada)** |
| **False Allow Rate (Métrica Crítica)** | 16.8% | **0.0% (en suite evaluada)** |
| **Acciones Destructivas Permitidas** | 32 | **0 (riesgo mitigado en suite)** |
| **Prevención de Bypass de Capabilities** | N/A (sin control) | **Verificada en suite (barrera HMAC-SHA256)** |
| **Resistencia a Replay y Manipulación** | Vulnerable | **Verificada con NonceStore durable** |
| **Contención de Path Traversal & SSRF** | Vulnerable | **Verificada con LocalProcessSandbox y EgressPolicy** |
| **Latencia de Supervisión p50** | 0 ms | **0.093 ms (sobrecarga mínima)** |
| **Throughput de Decisiones** | N/A | **~9.800 decisiones/segundo** |

---

# PARTE III: LIMITACIONES Y ALCANCE EXPERIMENTAL (Limitations / Experimental)

De acuerdo con las mejores prácticas de rigor científico y divulgación técnica transparente, se documentan las siguientes limitaciones del sistema en su versión actual:

1. **Aislamiento en Host Windows vs Linux (Paridad de Sandboxing):**
   - En entornos Linux con soporte para Docker o Podman, `ContainerSandboxAdapter` proporciona aislamiento a nivel de kernel mediante namespaces de proceso, red, montajes de solo lectura y control estricto de cgroups.
   - En sistemas Windows nativos sin contenedor, `LocalProcessSandbox` protege el sistema resolviendo rutas canónicas (`os.path.realpath`) y bloqueando procesos no permitidos, pero el filtrado de red a nivel de socket depende de la inspección sobre comandos invocados (`curl`, `wget`, `nc`, `ssh`) y no de un firewall o namespace de kernel dedicado.
2. **Horizonte Temporal de Contexto y Truncamiento (`ProviderContextBuilder`):**
   - Para preservar latencias de inferencia predecibles y presupuestos de tokens acotados, el constructor de contexto recorta observaciones voluminosas (`max_observation_chars`) y limita la ventana de pasos históricos (`max_history_steps`). Si una evidencia empírica crucial ocurrió hace muchos turnos y no fue registrada en el `EvidenceEngine`, el supervisor exigirá una re-inspección explícita antes de autorizar mutaciones dependientes.
3. **Grounding Empírico vs Demostradores Formales de Teoremas:**
   - `EvidenceEngine` y `CompletionVerifier` garantizan que las precondiciones y criterios de éxito estén respaldados por observaciones empíricas en la sesión o en disco (archivos creados, logs de tests con exit code 0). No reemplazan a verificadores formales de teoremas (Z3, Coq, Lean) para la prueba matemática de corrección de código algorítmico arbitrario.
4. **Calibración de Umbrales Específica de Dominio:**
   - Los umbrales de confianza del `ConfidenceAwareRouter` ($\tau=0.70-0.75$) han sido calibrados contra el catálogo procedural de agentes de desarrollo de software y administración de sistemas. Para dominios altamente especializados (finanzas, medicina o interacción con APIs propietarias opacas), se recomienda ejecutar `scripts/run_benchmarks.py` sobre un conjunto representativo para reajustar los umbrales de riesgo selectivo.
5. **Estado Experimental de Pesos Neuronales Locales:**
   - El modo de red neuronal real local (`LayaProvider(backend="local")`) requiere dependencias adicionales opcionales (`pip install "praxeon[laya]"`), descarga aproximadamente 800 MB de pesos en el primer arranque y requiere hardware con suficiente memoria RAM o VRAM (mínimo 4 GB). El motor heurístico calibrado (`backend="auto"` / `simulated`) se mantiene como la opción recomendada para CI/CD y despliegues con recursos limitados.

---

## Verificación de la Suite de Pruebas e Invariantes

La arquitectura de PRAXEON v1.0.0, los contratos de proveedores (`LayaProvider`, `TypeSafeAdapter`, `ReplayProvider`, `ConfidenceAwareRouter`), el desacoplamiento de semántica de operaciones (`CommandClassifier`), el Benchmark de Sobre-restricción, la detección de evasión Rule 0, la suite de concurrencia anti-replay (20 hilos), la autenticación de Web API / WebSocket, las barreras de enforcement HMAC, el servidor FastAPI y la suite E2E están respaldados por **280 pruebas automatizadas pasando al 100%**:

```bash
pytest -q
# 280 passed, 1 skipped in ~60s
```

---

## Documentación y Memoria Académica (DAM)

Para una exposición exhaustiva de los fundamentos teóricos, la arquitectura técnica, la justificación de diseño frente al currículo del Ciclo Formativo de Grado Superior en Desarrollo de Aplicaciones Multiplataforma (DAM) y el análisis de resultados empíricos, consulta:

- 📄 **[Memoria Técnica Oficial del Proyecto (DAM_PROJECT_MEMO.md)](docs/DAM_PROJECT_MEMO.md)**
- 🛡️ **[Política de Seguridad y Modelo de Amenazas (SECURITY.md)](SECURITY.md)**
- 📊 **[Resumen Ejecutivo de Benchmarks](benchmark_results/SUMMARY.md)**

---

## Licencia

Distribuido bajo licencia MIT. Consulta `LICENSE` para más detalles.
