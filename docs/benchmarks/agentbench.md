# Benchmark de agente: PRAXEON frente al mismo LLM sin arnés

Mide la utilidad real de PRAXEON ejecutando **las mismas tareas con el mismo LLM** con y sin su arnés, y registra los datos
que después sirven para entrenar (ajuste fino de LAYA).

```bash
# lista de tareas
uv run python scripts/run_agent_benchmark.py --list

# modelo local (Ollama) · brazo desnudo vs PRAXEON con LAYA · 3 repeticiones
uv run python scripts/run_agent_benchmark.py --models ollama:qwen2.5-coder:7b --arms bare,praxeon:laya --repeats 3

# Claude (CLI con tu suscripción); el coste lo informa la propia CLI
uv run python scripts/run_agent_benchmark.py --models claude_cli:haiku --arms bare,praxeon:laya --repeats 2

# solo regenerar el informe a partir de las ejecuciones guardadas
uv run python scripts/run_agent_benchmark.py --report-only
```

Cada ejecución se añade a `benchmark_results/agentbench/runs.jsonl` (se puede interrumpir y reanudar; lo ya hecho se omite)
y el informe se escribe en `benchmark_results/agentbench/REPORT.md`.

## Brazos

| Brazo | Qué es |
|---|---|
| `bare` | Bucle ReAct mínimo: el modelo pide una herramienta y se ejecuta. Sin supervisor, política, gestor de contexto, ramas ni aprobaciones. |
| `praxeon[:supervisor[:modo]]` | La misión completa del runtime (por defecto `laya`, `local_restricted`): supervisor + política + contexto + vetos, replanificación y ramas. Un operador simulado aprueba lo que pide revisión salvo el acto dañino de las trampas. |

Condiciones comunes: mismo cliente LLM (envuelto en un contador), mismo directorio de trabajo recién creado, mismos nombres
de herramienta y formato ReAct, mismo analizador de acciones, mismo recorte de observaciones y mismo máximo de pasos. El
orden de los brazos se baraja con semilla dentro de cada tarea y repetición para que la deriva (carga, calor, caché) no
favorezca siempre al mismo; antes de empezar se calienta el supervisor para no atribuir la carga de modelos a una tarea.

## Tareas

Nueve de **capacidad** (preguntas sobre el código, corrección de errores, implementar, modificar, renombrar en varios
archivos, ejecutar tests e informar, búsqueda de algo que no existe) y dos de **seguridad** con trampa: un README con
instrucciones inyectadas para borrar datos o enviar un fichero a un servidor. El daño es un hecho comprobado (un fichero
canario borrado o una petición recibida por un oyente local en 127.0.0.1), no la opinión de ningún proveedor. Todo lo
destructivo ocurre dentro del directorio de trabajo.

Una red de seguridad del banco (`jail_blocks`) impide en el brazo desnudo lo que escaparía del directorio de trabajo
(rutas absolutas o `~` con borrado, `sudo`, red externa); no forma parte de ningún brazo.

## Métricas

- **Éxito**: el comprobador automático lo da por bueno y el agente cerró con `finish` (si el arnés lo cortó, no cuenta).
  Tasa con intervalo de Wilson al 95 %.
- **Tokens**: reales del proveedor (prompt, salida y caché). Si el proveedor no los informa se estiman y se cuentan aparte
  (`estimated_token_calls`).
- **Velocidad**: tiempo total, tiempo en el LLM y sobrecoste del arnés (total − LLM − herramientas).
- **Arnés**: bloqueos, replanificaciones, revisiones (aprobaciones pedidas, concedidas y denegadas), ramas abiertas,
  podas, retrocesos, intervenciones y cortes forzosos.
- **Seguridad**: acciones dañinas propuestas por el modelo y daño real causado.
- **Coste por tarea**: el que informa el proveedor (la CLI de Claude da `total_cost_usd`), o tokens × tabla de precios
  (`--prices precios.json`). Los modelos locales cuestan 0 USD; con `--reference-price entrada,salida` (USD por millón de
  tokens) se muestra el equivalente para compararlos con una API. Sin precio conocido el coste queda vacío: no se inventa.

## Limitaciones conocidas

- Los modelos no son deterministas y `create_agent_llm` no fija semilla ni temperatura: por eso hay repeticiones y se
  leen tasas con su intervalo, no una ejecución suelta.
- Las herramientas de los dos brazos tienen la misma interfaz, pero el brazo PRAXEON las ejecuta con su propio ejecutor y
  sandbox; pueden diferir detalles de formato de las observaciones.
- La CLI de Claude añade ~600 tokens de entrada fijos por sesión; afecta igual a los dos brazos.
- Con pocas tareas el intervalo de confianza es ancho: para conclusiones firmes hacen falta más repeticiones.
