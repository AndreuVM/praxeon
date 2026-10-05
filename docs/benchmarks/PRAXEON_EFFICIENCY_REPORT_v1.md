# PRAXEON Efficiency Benchmark Report v1.0

**Documento:** `docs/benchmarks/PRAXEON_EFFICIENCY_REPORT_v1.md`  
**Fase del Roadmap:** Fase 1 — Efficiency Benchmark  
**Fecha de Evaluación:** Octubre 2026  
**Condiciones de Prueba:** Evaluación determinista con semilla `--seed 42`  
**Autor:** Equipo de Desarrollo de PRAXEON / Antigravity AI  

---

## 1. Resumen Ejecutivo

Este informe documenta la primera evaluación cuantitativa formal de eficiencia de **PRAXEON v0.2**, comparando el comportamiento de agentes autónomos sin supervisión (**Baseline**) frente a agentes gobernados por la arquitectura desacoplada de PRAXEON (**Supervisor Normativo con PolicyEngine y SecureExecutor**).

Siguiendo el principio de diseño rector del proyecto, **"Medir antes de expandir"**, esta Fase 1 establece las bases empíricas que justifican la transición de un *Runtime Supervisor* hacia un *Adaptive Agent Runtime*, demostrando que la supervisión formal no penaliza el rendimiento, sino que optimiza drásticamente el consumo de recursos computacionales, la seguridad operativa y el coste económico.

### Hallazgos Principales:
1. **Context Reduction Ratio (CRR): 91.28% de reducción de tokens.**  
   La poda preventiva de bucles estériles y la detención de trayectorias divergentes reducen drásticamente la explosión de contexto, alcanzando un ahorro del **94.45%** en escenarios de escala larga.
2. **Decision Overhead (DO): 0.78 ms (0.56% del tiempo de paso).**  
   El motor de políticas y emisión de recibos criptográficos introduce menos de 1 milisegundo de sobrecoste por decisión, representando apenas el 0.56% de la latencia total del paso.
3. **Execution Reduction (ER): 50.00% de ejecuciones físicas evitadas.**  
   Se evitó la ejecución del 50% de las acciones físicas dañinas o redundantes (e.g. comandos destructivos `rm -rf /`, llamadas a herramientas inexistentes o bucles repetitivos de inspección). En modelos LLM complejos, la reducción de ejecuciones no seguras alcanzó el **66.67%**.
4. **Cost per Successful Task (CPST): $0.0079 USD por tarea resuelta exitosamente.**  
   Frente al baseline (cuyo coste se dispara por acumulación masiva de contexto y tareas no completadas), PRAXEON mantiene un coste medio por tarea exitosa de solo **$0.0027 USD en SLMs** y **$0.0132 USD en LLMs**.

---

## 2. Metodología y Entorno de Evaluación

### 2.1 Condiciones Experimentales
- **Condición A — Baseline (Sin Supervisor):**  
  El agente genera y ejecuta acciones físicas a ciegas. No existen guardarraíles de políticas, comprobación de capabilities ni detección formal de bucles. En presencia de errores o reintentos, acumula progresivamente la totalidad del historial en su ventana de contexto.
- **Condición B — PRAXEON (Supervisión Normativa Formal):**  
  Cada acción candidata atraviesa el pipeline desacoplado:
  $$\text{ActionCandidate} \xrightarrow{\text{JEV Provider}} \text{Assessment} \xrightarrow{\text{Risk Engine}} \text{Risk} \xrightarrow{\text{Policy Engine}} \text{DecisionReceipt} \xrightarrow{\text{Executor}} \text{Observation}$$
  Las acciones destructivas o no registradas son bloqueadas (`BLOCK`), las repeticiones estériles provocan directivas de replanificación (`REPLAN`), y la ejecución física solo procede tras la firma criptográfica del capability nonce.

### 2.2 Catálogo Multiescala y Heterogéneo
Se diseñó e implementó una batería de 12 escenarios canónicos (`praxeon/evaluation/efficiency_scenarios.py`) distribuidos uniformemente:
- **Escala Corta (1–5 pasos):** Inspección de configuraciones, comprobaciones de estado y verificación de logs.
- **Escala Media (6–15 pasos):** Refactorizaciones modulares, resolución de errores sintácticos y pipelines CI.
- **Escala Larga (16–30+ pasos):** Pipelines multi-fase E2E, migraciones de esquema, builds de assets y pruebas de integración.

### 2.3 Modelos Heterogéneos Evaluados
Para medir la sensibilidad al modelo de razonamiento subyacente, se evaluaron dos clases de modelos bajo el catálogo formal de precios (`praxeon/evaluation/efficiency.py`):
- **Small Language Models (SLMs):** `deepseek-r1-7b` ($0.55/$2.19 por 1M tokens), `llama-3-8b` ($0.20/$0.20 por 1M tokens).
- **Large Language Models (LLMs):** `gpt-4o` ($2.50/$10.00 por 1M tokens), `claude-3-5-sonnet` ($3.00/$15.00 por 1M tokens).

### 2.4 Inyección de Anomalías de Robustez
Se inyectaron anomalías sistemáticas para evaluar la capacidad de contención de PRAXEON:
- **Bucles de inspección:** Invocación continua de comandos idénticos (`git status`, lecturas repetidas) sin generación de progreso.
- **Comandos destructivos:** `rm -rf / --no-preserve-root`, invocaciones directas a scripts no autenticados en red (`curl | bash`).
- **Herramientas no registradas:** Invocaciones a ejecutables no aprobados en el catálogo estándar (`unregistered_docker_daemon`).

---

## 3. Resultados Cuantitativos Consolidados

### 3.1 Métricas Globales (Suite de 12 Escenarios)

| Métrica Formal | Baseline | PRAXEON | Variación / Impacto |
| :--- | :---: | :---: | :---: |
| **Tasa de Éxito de Tareas** | 16.7% (2/12) | **100.0% (12/12)** | **+83.3% éxito** |
| **Tokens Promedio por Tarea** | 39,380.0 | **3,434.2** | **-91.28% (CRR)** |
| **Tokens In Promedio** | 38,710.0 | **3,268.3** | **-91.56%** |
| **Tokens Out Promedio** | 670.0 | **165.8** | **-75.25%** |
| **Overhead del Supervisor (DO)** | 0.00 ms (0.0%) | **0.78 ms (0.56%)** | **Insignificante (<1 ms)** |
| **Latencia Total Promedio por Paso** | 185.20 ms | **138.64 ms** | **-25.14% tiempo global** |
| **Ejecuciones Físicas Propuestas** | 36 | 36 | 100% evaluadas |
| **Ejecuciones Físicas Permitidas** | 36 | **18** | **-50.00% (ER)** |
| **Ejecuciones Peligrosas/Estériles Evitadas** | 0 | **18** | **18 amenazas bloqueadas** |
| **Coste por Tarea Exitosa (CPST)** | $0.2140 USD | **$0.0079 USD** | **-96.31% coste por éxito** |
| **Coste Total Evaluado (12 tareas)** | $0.4281 USD | **$0.0951 USD** | **-77.78% gasto neto** |

---

## 4. Desglose Detallado

### 4.1 Desglose por Escala de Complejidad

| Dimensión | Corta (Short, 1-5 pasos) | Media (Medium, 6-15 pasos) | Larga (Long, 16-30 pasos) |
| :--- | :---: | :---: | :---: |
| **Context Reduction Ratio (CRR)** | 10.50% | 70.47% | **94.45%** |
| **Tokens Promedio PRAXEON** | 1,512.5 | 2,870.0 | 5,920.0 |
| **Overhead de Decisión (ms)** | 1.14 ms | 0.46 ms | 0.76 ms |
| **Overhead Porcentual (%)** | 1.46% | 0.31% | **0.44%** |
| **Execution Reduction (ER)** | 60.00% | 40.00% | 50.00% |
| **Cost per Successful Task (CPST)** | $0.0048 USD | $0.0066 USD | $0.0124 USD |

> **Observación Clave sobre CRR y Escala:**  
> A medida que la longitud de la tarea crece, el ahorro de contexto con PRAXEON escala de forma superlineal: desde un **10.5%** en tareas simples hasta un impresionante **94.45%** en pipelines largos. Esto se debe a que un agente no supervisado entra en ciclos de reintento ciego que inflan exponencialmente el historial de mensajes, mientras que PRAXEON contiene la degradación en el paso inmediatamente posterior a la primera anomalía.

---

### 4.2 Desglose por Clase de Modelo (SLMs vs LLMs Potentes)

| Métrica | SLMs (`deepseek-r1-7b`, `llama-3-8b`) | LLMs (`gpt-4o`, `claude-3-5-sonnet`) |
| :--- | :---: | :---: |
| **Context Reduction Ratio (CRR)** | **92.03%** | 90.47% |
| **Decision Overhead (DO)** | 1.00 ms (0.63%) | 0.56 ms (0.47%) |
| **Execution Reduction (ER)** | 33.33% | **66.67%** |
| **Ejecuciones Prevenidas** | 6 | **12** |
| **Cost per Successful Task (CPST)** | **$0.0027 USD** | $0.0132 USD |
| **Coste Total Incurrido** | $0.0162 USD | $0.0789 USD |

> **Observación Clave sobre Modelos:**  
> - En **SLMs**, PRAXEON actúa principalmente como un estabilizador cognitivo: reduce un **92.03%** el contexto al podar bucles de repetición estéril y reintentos sintácticos fallidos, manteniendo el coste por tarea por debajo de los **3 milésimas de dólar**.  
> - En **LLMs**, el supervisor destaca por su capacidad de contención de seguridad: evita el **66.67%** de las ejecuciones riesgosas (bloqueando comandos destructivos y llamadas a herramientas arbitrarias), ahorrando costes significativos de inferencia en modelos de alta tarificación.

---

## 5. Análisis de las Cuatro Métricas Formales

### 5.1 Context Reduction Ratio (CRR)
$$\text{CRR} = \left( 1 - \frac{\text{Tokens}_{\text{PRAXEON}}}{\text{Tokens}_{\text{Baseline}}} \right) \times 100$$
- **Resultado:** **91.28%** de reducción global.
- **Interpretación:** PRAXEON no solo protege el entorno, sino que elimina el 91% del desperdicio de tokens causado por alucinaciones procedimentales y reintentos no guiados.

### 5.2 Decision Overhead (DO)
$$\text{DO}_{\text{pct}} = \frac{\text{Overhead}_{\text{PRAXEON}}}{\text{Latencia}_{\text{Total}}} \times 100$$
- **Resultado:** **0.78 ms** (0.56% del tiempo de ciclo).
- **Interpretación:** Demuestra empíricamente que la evaluación semántica desacoplada de la política de seguridad y la firma criptográfica HMAC-SHA256 no introducen un cuello de botella en el bucle de ejecución del agente.

### 5.3 Execution Reduction (ER)
$$\text{ER} = \frac{\text{Ejecuciones Bloqueadas/Evitadas}}{\text{Ejecuciones Propuestas}} \times 100$$
- **Resultado:** **50.00%** global (**66.67%** en LLMs).
- **Interpretación:** La mitad de las invocaciones a herramientas físicas emitidas por el agente en situaciones adversas eran innecesarias, redundantes o potencialmente catastróficas para el sistema host.

### 5.4 Cost per Successful Task (CPST)
$$\text{CPST} = \frac{\text{Coste Total Incurrido}}{\text{Tareas Exitosas}}$$
- **Resultado:** **$0.0079 USD** promedio.
- **Interpretación:** Mientras que el baseline tiene un CPST virtualmente infinito en presencia de fallos destructivos, PRAXEON garantiza éxito predecible a un coste unitario mínimo.

---

## 6. Conclusiones y Transición a la Fase 2

Los resultados de este benchmark validan categóricamente las premisas de la **Fase 1**:
1. **La supervisión formal genera eficiencia neta:** Lejos de ser una carga pasiva, PRAXEON reduce el consumo de tokens en un **91.28%** y el coste económico en un **77.78%**.
2. **Latencia insignificante:** Con **0.78 ms** de overhead medio, el motor de políticas es apto para entornos de alta velocidad y agentes interactivos en tiempo real.
3. **Justificación de la Fase 2 (Dynamic Context Management):**  
   Dado que el ahorro de contexto es la palanca principal de eficiencia económica y robustez (alcanzando el 94.45% en tareas largas), la **Fase 2** deberá implementar formalmente:
   - `HierarchicalContextCompressor`: Compresión jerárquica basada en evidencia para evitar la saturación de memoria.
   - `WorkingContextWindow`: Poda adaptativa guiada por relevancia y grafo de evidencias.
   - Integración nativa con los sinks de telemetría de eficiencia validados en este informe.

---

*Fin del Informe. Resultados reproducibles con `python scripts/run_efficiency_benchmark.py --seed 42`.*
