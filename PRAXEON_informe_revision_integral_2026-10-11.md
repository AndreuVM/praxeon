# Informe Formal de Revisión Integral — PRAXEON v1.1.0

**Fecha:** 11 de octubre de 2026
**Alcance:** repositorio completo (`praxeon/`, `web/`, `tests/`, CI, documentación)
**Entorno:** Windows 11, Python 3.11 (`.venv`), Node 20, Vite 8
**Rama / último commit revisado:** `main` @ `aee00df`

---

## 1. Resumen ejecutivo

PRAXEON es un proyecto ambicioso y, en su núcleo, bien pensado: la separación *propuesta → decisión → capability firmada → ejecución confinada* está implementada, probada (889 tests) y el código del *runtime de supervisión* (HMAC de capabilities, anti-replay, políticas, sandbox de contenedor fail-closed) es de buena calidad.

Sin embargo, **la capa de servidor SaaS añadida en la Fase 8 (auth multi-tenant, JWT, RBAC) no está a la altura del resto**. He confirmado con una prueba de concepto ejecutable que, **con el servidor en perfil `production` y una API key configurada, cualquier persona sin credenciales obtiene un token de administrador** (§3.1). Esto invalida hoy cualquier despliegue expuesto en red y contradice las afirmaciones de `SECURITY.md` y de los informes anteriores.

| Dimensión | Valoración | Comentario |
| :--- | :---: | :--- |
| Núcleo de supervisión (policy, capability, executor) | 🟢 Buena | Sólido y bien cubierto por tests |
| Seguridad de la API / auth | 🔴 **Crítica** | Bypass de autenticación; RBAC y multi-tenancy no aplicados |
| Aislamiento de ejecución local | 🟠 Sobredeclarado | `LOCAL_RESTRICTED`/`WASM` no son sandboxes reales |
| Calidad / mantenibilidad del código | 🟡 Aceptable | God-file de 3.200 líneas, duplicados, código muerto |
| Módulo de Workflows | 🟠 Parcial | La ejecución REST es una simulación sin supervisión; 14 hallazgos (§4B) |
| Tests | 🟢 Buena | 887 ✔ / 1 ✘ (frágil por entorno) / 1 omitido |
| Frontend | 🟡 Aceptable | Compila (0,6 s) y ya hace code-split; deuda de hooks y almacenamiento de clave |
| Higiene del repositorio / documentación | 🟡 Mejorable | Artefactos versionados, informes que no reflejan la realidad |

**Recomendación:** no desplegar ni publicar la v1.1.0 como "hardened" hasta corregir los 4 hallazgos críticos/altos de §3 y los tres críticos de workflows W-01, W-02 y W-03 (§4B). Estimación: 2–3 días.

**Workflows (revisión ampliada, §4B):** el modelo y la semántica de condiciones están bien diseñados, pero la API REST de workflows **ejecuta en modo simulación y marca como `COMPLETED` tareas que no se han ejecutado**, no pasa por la cadena de supervisión de PRAXEON (política *fail-open* editable por el propio usuario), y el motor presenta un fallo en el límite de bucles `WHILE`, condiciones de carrera y una edición que borra datos del workflow. Se reprodujeron 6 de los 14 hallazgos con scripts.

---

## 2. Metodología y límites

Ejecutado: suite completa de `pytest`; `bandit -r praxeon`; `ruff` (reglas F, E9, B, S); `oxlint` y `vite build` en `web/`; lectura manual de los módulos críticos (`auth_service`, `dependencies`, rutas `auth`, `websocket`, `sandbox`, `full_access`, `policy/registry`, `proxy_middleware`, `app`); y **PoC ejecutables** contra la app FastAPI para los hallazgos de auth.

**No verificado** (declarado por honestidad): ejecución real de contenedores Docker/Podman (no disponible aquí), proveedores externos LAYA/TypeSafe/Gemini (requieren claves/red), cobertura de tests (no hay `pytest-cov` configurado), y lectura línea a línea de todos los ~44.000 LOC (se priorizó por riesgo). Los hallazgos de §4 en adelante son por tanto una muestra dirigida, no un inventario exhaustivo.

---

## 3. Hallazgos de seguridad

### 3.1 🔴 CRÍTICO — Bypass total de autenticación en `POST /v1/auth/login`
`praxeon/server/routes/auth.py:73`
```python
if req.api_key and not auth_service.verify_api_key(req.api_key):   # solo valida SI se envía clave
    raise HTTPException(401)
tokens = auth_service.issue_token_pair(tenant_id=req.tenant_id, user_id=req.user_id, role=req.role)
```
Si el cliente **omite** `api_key`, la comprobación se salta y se emiten tokens para el `tenant_id`, `user_id` y **`role` que el propio cliente solicite** (por defecto `admin`).
**PoC verificada** (perfil `production`, API key de 38 caracteres): `POST /v1/auth/login {"tenant_id":"victim","role":"admin"}` → `200`, y el token resultante accede a `/v1/auth/me` con los 10 permisos de admin.
**Corrección:** exigir `api_key` siempre que haya clave configurada (`if not verify_api_key(req.api_key): 401`), y **no aceptar `role`/`tenant_id` del cliente**: derivarlos de la credencial verificada.

### 3.2 🔴 ALTO — Los *refresh tokens* se aceptan como *access tokens*
`auth_service.py::authenticate_jwt` no comprueba `payload["type"] == "access"` (ni `iss`). **PoC verificada:** el refresh token (7 días) devuelve `200` en `/v1/auth/me` como Bearer. Anula la vida corta de 15 min del access token. Además, un token sin `exp` nunca caduca.
**Corrección:** validar `type`, `iss`, exigir `exp`.

### 3.3 🔴 ALTO — RBAC y multi-tenancy declarados pero **no aplicados**
- `require_permission(...)` **no se usa en ninguna ruta** (grep sobre `praxeon/`: solo su definición). Las rutas usan `verify_api_key`, que solo autentica. Un rol `viewer` puede hacer lo mismo que `admin`.
- No existe `tenant_id` en sesiones, eventos, workflows ni persistencia (`grep tenant` solo aparece en `dependencies.py`, `routes/auth.py`, `auth_service.py`). **No hay aislamiento entre tenants**: cualquier usuario autenticado ve y manipula todo.
- La API key estática equivale siempre a `ADMIN` del `default_tenant`.
La documentación ("multi-tenant SaaS con RBAC") describe una capacidad que hoy no existe en la práctica.

### 3.4 🟠 ALTO — Secreto JWT por defecto hardcodeado
`auth_service.py:~165`: `self._secret_key = ... or "praxeon_default_development_secret_key_32_chars!"`. Con `PRAXEON_REQUIRE_AUTH=1` pero sin `PRAXEON_SECRET_KEY`/API key, cualquiera puede **forjar JWT válidos** (el secreto está en GitHub). Además, si no hay `SECRET_KEY` se reutiliza la API key como clave de firma (mezcla de dos propósitos). Solo el perfil `production` valida longitud/valor; el resto de rutas de arranque no.
**Corrección:** si se requiere auth y no hay secreto, fallar al arrancar; generar un secreto aleatorio por proceso en dev.

### 3.5 🟠 MEDIO-ALTO — Sandbox local y "WASM" sobredeclarados
- `LocalProcessSandbox.execute_command` ejecuta `/bin/sh -c <cmd>` / `powershell -Command <cmd>` con el comando íntegro. La contención de rutas solo se aplica a `cwd` y a `read_file/edit_file`, **no al contenido del comando** (`cat /etc/passwd`, `cd / && …` pasan). El "bloqueo de red" es una lista de nombres (`curl`, `wget`…) más variables proxy: se elude con `python -c "import urllib…"`, `bash /dev/tcp`, etc. `SECURITY.md` habla de "red totalmente apagada" para `LOCAL_RESTRICTED`; solo es cierto en `CONTAINER`.
- `WasmSandbox` **sin `wasmtime`** (caso por defecto) delega en `LocalProcessSandbox` (`fallback_occurred=True`), es decir, no hay WASM real.
- `FullAccessExecutor` (`runtime/full_access.py:~158`): cualquier `tool_name` que no contenga las subcadenas `unregistered/unknown/malicious` se **ejecuta como comando de shell** (`shell=True` en Unix y en el fallback `.bat`). El filtro por subcadenas es una lista negra frágil; debería ser una lista blanca de herramientas registradas.
- La depuración de entorno cubre `API_KEY`, `SECRET_KEY`, `PASSWORD`… pero no `TOKEN`, `PASSWD`, `CREDENTIAL`, `NPM_TOKEN`, `HF_TOKEN`, etc.
**Corrección:** documentar `LOCAL_RESTRICTED` como *best-effort / no seguro frente a adversario*, recomendar `CONTAINER` para código no confiable, y ampliar la lista de variables sensibles.

### 3.6 🟡 MEDIO — WebSocket y superficie expuesta
- `/v1/sessions/{id}/stream` solo acepta la API key estática (y *ticket*); **no acepta JWT/cookies**, inconsistente con el resto de la API. Acepta la clave maestra por query-string (`?token=`), que acaba en logs/historial.
- Los tickets WS (`websocket.py`) no se ligan al usuario/tenant ni verifican que la sesión exista o pertenezca al emisor; el valor por defecto `session_id="default"` permite emitir para cualquier id.
- `/metrics`, `/health`, `/docs`, `/redoc`, `/openapi.json` responden **sin autenticación** incluso en `production` (verificado). `/metrics` puede filtrar información operativa.
- `is_auth_required(client_host=…)` trata `127.0.0.1` como "no requiere auth": tras un reverse proxy local (nginx en la misma máquina) **todo el tráfico aparece como loopback** y se salta la autenticación en perfil dev.
- `secrets.compare_digest(str, str)` con caracteres no ASCII lanza `TypeError` (→ HTTP 500) en `get_current_tenant`/`verify_api_key`/WS; comparar `bytes`.
- Cookies: `secure=False` por defecto, `SameSite=lax`; sin protección CSRF si se usa cookie con métodos mutantes entre sitios.
- Lista de revocación de JTI **en memoria**, sin límite y perdida al reiniciar: un logout no sobrevive a un reinicio y un refresh token revocado vuelve a ser válido.

### 3.7 🟡 MEDIO — Frontend
`web/src/services/api.js` persiste la API key en `localStorage` (accesible a cualquier XSS), contradiciendo el diseño con cookies HttpOnly. No se encontró `dangerouslySetInnerHTML`/`innerHTML` (positivo).

### 3.8 🟡 MEDIO — Clasificación de comandos "observacionales" errónea
`policy/registry.py::is_observational` usa `startswith` sobre prefijos: `"ls; rm -rf /"`, `"cat a > ~/.bashrc"`, `"find . -delete"`, `"echo x > /etc/passwd"`, `"git branch -D main"`, `"lsof"` devuelven **`True`** (verificado). Hoy se consume en `interceptor/proxy_middleware.py` (detección de bucles/alucinaciones/finalización), no en la puerta de política, por lo que no es una ejecución directa, pero un paso mutante queda exento de la detección de bucle y cuenta como "evidencia". Debe tokenizarse (`shlex`) y rechazar metacaracteres (`; & | > < $( )` ` `) y flags destructivos.
Relacionado: `_is_dynamic_cli_tool` admite como "herramienta conocida" **cualquier ejecutable presente en el PATH** y contiene una lista de subcadenas de *fixtures de test* (`"unregistered"`, `"malicious"`, `"exploit"`, `"act_unknown"`) dentro de la lógica de producción.

### 3.9 Aspectos positivos de seguridad
- `.env` **no está versionado** (verificado `git log --all -- .env`); solo `.env.example`.
- Bandit: **0 High / 0 Medium**, 65 Low (principalmente `try/except/pass` y uso legítimo de `subprocess`).
- HMAC de capabilities con `compare_digest`, nonces atómicos, contenedor *fail-closed* (`--network=none`, `--read-only`, `--cap-drop=ALL`), sin `pickle`/`eval`/SQL concatenado, validación de perfil `production` (clave ≥32, CORS sin comodín), protección CSWSH por `Origin`.

---

## 4. Calidad, elegancia y eficiencia del código

**Diagnóstico de ruff (reglas F/B/S):** 494 incidencias, de las cuales:
- **1 error de nombre indefinido:** `workflows/decision_bridge.py:68` usa `Set` sin importarlo (F821). No rompe en ejecución (la anotación de un atributo dentro de un método no se evalúa), pero es un defecto latente para `get_type_hints`/mypy.
- 311 imports sin usar (F401); 33 `raise` sin `from` dentro de `except` (B904) que pierden la causa; 44 `try/except/pass` que silencian errores (varios en `editor_service.py`, `dependencies.py`, `prometheus.py`).
- Redefiniciones: `dependencies.py:3049-3052` reimporta `secrets` y `AuthService` a mitad de fichero; `typesafe_client.py:499` y `runner.py:730` redefinen símbolos.
- Conjuntos con elementos duplicados (B033): `registry.py:40` (`"pytest"`), `completion.py:80`, `mission_service.py:442` — indican listas de palabras mal depuradas.
- Valores por defecto con llamadas a funciones en firmas (B008, 41), mayoritariamente `Depends(...)` de FastAPI (aceptable) pero conviene configurar `extend-immutable-calls`.

**Estructura:**
- `server/dependencies.py` (3.221 líneas) alberga la clase `RuntimeApplicationService` (~2.900 líneas) más auth y utilidades: un *god object*. Mezcla DI de FastAPI, wiring del runtime, auth y estado global (`_runtime_service_instance`, `_ACTIVE_SECURITY_PROFILE`, `_warned_dev_auth`). Debe dividirse en servicios (ya existe `server/services/`) y un contenedor de composición.
- `get_current_tenant` y `verify_api_key` duplican ~80 % de lógica de extracción de token. Los `isinstance(x, str)` defensivos revelan acomodación a llamadas directas en tests en lugar de inyección limpia.
- `app.py::create_app` calcula `prof` dos veces.
- Otros ficheros grandes: `workflows/engine.py` (1.270), `dashboard.py` (1.128), `sandbox.py` (949), `evaluation/*` (>2.000 entre cuatro ficheros).
- Convivencia de nombres: `jev_*`/`JEV_*` (legacy) y `praxeon` en 11 sitios + 4 *aliases* CLI deprecados; mezcla de español/inglés en identificadores y mensajes.
- Falta de configuración de linter/formatter/typing en `pyproject.toml` (ni ruff, ni mypy, ni cobertura): la deuda de arriba se acumula porque no hay *gate* en CI.

**Eficiencia:** sin problemas graves detectados en la muestra. Puntos a vigilar: `_purge_expired_tickets_locked` y `_ws_tickets` recorren el diccionario completo en cada emisión (O(n)); `_revoked_jtis` crece sin límite; `refresh`/`login` son síncronos (`def`) con trabajo CPU trivial (correcto). La suite tarda 3 min 13 s, dominada por tests CLI/dashboard (~10 s cada uno).

---

## 4B. Revisión en profundidad del módulo de Workflows

Ficheros revisados línea a línea: `workflows/models.py`, `engine.py`, `semantics.py`, `scheduler.py`, `decision_bridge.py`, `editor_service.py`, `templates.py`, `server/routes/workflows.py`, `server/services/workflow_service.py` y `persistence/sqlite_store.py` (persistencia de ejecuciones). Los hallazgos marcados **[PoC]** se reprodujeron con scripts ejecutables.

**Valoración global:** el modelo de datos (Pydantic inmutable, validación de grafo, semántica de condiciones sin `eval`) está bien diseñado y las tres plantillas canónicas validan y se ejecutan. Los problemas están en la **integración**: la API REST no ejecuta nada real, hay dos orquestadores paralelos, y el motor tiene condiciones de carrera y varios casos límite incorrectos.

### 4B.1 🔴 Críticos / altos

**W-01 — La ejecución REST es una simulación que reporta éxito [PoC].** `editor_service.get_or_create_engine` crea el motor con `allow_synthetic_fallback=True` y sin ningún *handler* ni `ToolRegistry`. Un nodo `TASK` con `tool_name="rm_rf_prod"` termina `COMPLETED` con la salida inventada `{"task_result": "Executed rm_rf_prod"}`; los `AGENT`/`DELEGATE` igual. El motor declara que un "runtime gobernado requiere handlers reales" (`NotImplementedError`), pero la única ruta expuesta lo desactiva. Consecuencias: (i) la UI muestra workflows "completados" sin que haya ocurrido nada; (ii) el día que se registren handlers reales, no existirá la barrera de seguridad que el diseño prevé. Corrección: `False` por defecto, activable solo explícitamente en modo demo y visible en la respuesta (`"simulated": true`).

**W-02 — Los workflows no pasan por la cadena de supervisión de PRAXEON.** Se buscó en todo `workflows/`: no hay referencias a `SecureExecutor`, capabilities HMAC, `PolicyEngine` ni sandbox. `WorkflowDecisionBridge` sustituye la política por una función propia que, **sin `policy_evaluator`, permite todo** salvo que el propio nodo lleve `metadata["forbidden"]` o `metadata["require_confirmation"]`. Esas marcas viven en `metadata`, que **cualquiera con acceso a `PUT /nodes/{id}` puede reescribir** (`UpdateNodeRequest.metadata` reemplaza el diccionario entero). Es decir: la política es *fail-open* y la define el mismo actor al que debería limitar. Esto contradice el axioma central del proyecto ("el modelo propone, el runtime decide") justo en el módulo de orquestación.

**W-03 — Bucle `WHILE` falla al terminar de forma natural en el límite [PoC].** En `engine.py` la comprobación `if current_iter >= max_iter:` se hace *antes* de considerar la condición. Un bucle `n < 3` con `max_iterations=3` ejecuta 3 vueltas correctas y, en la evaluación siguiente (condición ya falsa), lanza `RuntimeError: Límite de iteraciones alcanzado` y el workflow queda `FAILED` (resultado reproducido: `FAILED … n=3`). Debe evaluarse primero la condición y solo abortar/escalar si la condición sigue siendo verdadera con el límite agotado. (Las correcciones "P0 WHILE" del último commit no cubren este caso.)

**W-04 — Condiciones de carrera entre scheduler, API y workers.** `execute_node` usa un `RLock`, pero muchas mutaciones no lo toman: `cancel`, `pause`, `resume`, `retry_node`, `backtrack_to_node`, `handle_agent_response`, `_check_agent_responses` (cuando se llama desde `step`), `check_scheduled_retries_and_timeouts` (desde el scheduler), toda la rama `WHILE` de `execute_node` (`variables.setdefault("_loops")`, `_active_loop`) y `step()` completo. El scheduler corre en un hilo daemon cada 0,5 s sobre los mismos motores que la API modifica. Riesgos: `RuntimeError: dictionary changed size during iteration` en `get_evaluation_context`/`poll_once`, y estados incoherentes. Ejemplo reproducible: **`cancel()` sobre un workflow `COMPLETED` lo pasa a `CANCELLED` [PoC]** (no hay guarda de estado terminal); a la inversa, `_finalize_terminal_state()` sobrescribe incondicionalmente el estado a `COMPLETED`, pudiendo pisar una cancelación concurrente.

**W-05 — Respuestas de agentes: buzón compartido, sin autenticidad ni correlación.** El motor consume `agent_bus.receive("praxeon_supervisor")`, un buzón **único para todas las ejecuciones**. Cada motor extrae mensajes (`pop`) y **descarta los que no son suyos**; si dos ejecuciones del mismo workflow están vivas (mismos `node_id`), una puede completar el nodo de la otra con su resultado. Además: (i) se correlaciona por `task_id = node_id`, no por `message_id`/`in_reply_to`, así que una respuesta tardía de un intento anterior (tras timeout/reintento o de una iteración previa de un `WHILE`) completa el nodo con datos obsoletos; (ii) no se comprueba que `msg.sender_id == node.agent_id`, por lo que cualquier agente puede resolver cualquier nodo; (iii) no se mira el tipo de mensaje: un `ERROR` se trata como éxito (`COMPLETED`). Existe `receive_reply_for(...)` en el bus, que haría la correlación correcta, pero no se usa.

**W-06 — El estado real y la definición divergen: motor "obsoleto" tras editar [PoC].** `_active_engines` cachea **un único motor por `workflow_id`**, creado con la definición del momento. Tras añadir un nodo, el workflow tenía 4 nodos y el motor activo, 3. Ejecutar/avanzar después usa el grafo antiguo sin avisar. Además, al haber un solo motor por workflow, **no pueden coexistir varias ejecuciones** por la ruta REST (sí por `WorkflowService`, ver W-07).

**W-07 — Dos orquestadores paralelos que no se hablan.** `WorkflowService.run_workflow_execution` (motor por ejecución + `WorkflowScheduler` con timeouts, reintentos y persistencia periódica) **no está expuesto por ninguna ruta** (`grep`: solo se define). La API usa `WorkflowEditorService`+`WorkflowDecisionBridge`, que **no invoca** `check_scheduled_retries_and_timeouts` ni `_check_agent_responses`. Efecto en la ruta REST: un nodo con `retry_policy` queda en `RETRYING` para siempre; un nodo `AGENT` con bus real queda en `WAITING_RESULT` para siempre; los timeouts de nodo y global (`workflow.timeout_seconds`) nunca se evalúan. Los tests de scheduler/timeouts pasan porque ejercitan la ruta que la API no usa. Además `WorkflowService` arranca el scheduler solo con `enable_scheduler=True` y nadie lo activa.

### 4B.2 🟠 Medios

**W-08 — Cada edición borra `timeout_seconds` y `metadata` del workflow [PoC].** Todas las operaciones del editor (`add_node`, `update_node`, `update_node_position`, `remove_node`, `connect_nodes`, `disconnect_nodes`, `update_edge`) reconstruyen `WorkflowDefinition(...)` copiando solo 6 campos; se pierden `timeout_seconds` y `metadata` (verificado: tras mover un nodo, `timeout_seconds: None`, `metadata: {}`). Debería usarse `wf.model_copy(update={...})`. El patrón se repite 7 veces (≈150 líneas duplicadas).

**W-09 — Edición concurrente sin control.** El editor hace *leer-modificar-escribir* sobre `_workflows` sin bloqueo ni comprobación de `version` (concurrencia optimista): dos usuarios arrastrando nodos pierden cambios. `register_workflow` traga errores de persistencia (`except Exception: pass`): la API responde 200 aunque no se haya guardado. Hay 8 de estos `pass` en el fichero.

**W-10 — Comparaciones con tipos incompatibles rompen el ciclo del motor [PoC].** `EdgeCondition`/`AtomicCondition` usan `val > expected` directamente. Con `v="abc"` y `> 5`, `is_node_ready` lanza `TypeError` **fuera** de cualquier `try`; `run_to_completion` revienta y, en el scheduler, el error se registra en cada tick (cada 0,5 s) y el workflow queda atascado sin pasar a `FAILED`. En los nodos DECISION sí se captura, así que el comportamiento es inconsistente. Envolver la evaluación y tratar el error como `False` (o `on_error`).

**W-11 — Semántica implícita de *fan-in* no documentada.** Un nodo normal con varias aristas entrantes se considera listo cuando **cualquiera** se cumple (OR). En el workflow demo, `decision` recibe de `research` y `dev` sin `PARALLEL_JOIN`, por lo que se ejecuta con la primera que termine sin esperar a la otra. Muchos usuarios esperan AND (como en un DAG clásico). Añadir validación/aviso en el editor o `join_policy` por defecto en nodos con >1 entrada.

**W-12 — Aprobaciones humanas sin autorización ni consumo.**
- `approver_role` se guarda y se muestra, pero **no se comprueba** en ningún sitio (`grep`: solo escritura). `POST /nodes/{id}/approve` no usa `require_permission` (ver §3.3): cualquier cliente autenticado aprueba cualquier nodo. Tampoco se registra *quién* aprobó (solo `comment`).
- En `WorkflowDecisionBridge`, `_approved_nodes` nunca se vacía: una aprobación única autoriza **todas las iteraciones futuras** del mismo nodo en un bucle.
- `bridge.approve_node` y `engine.approve_node` son dos mecanismos distintos con semánticas diferentes.

**W-13 — `backtrack_to_node` restaura solo parte del estado.** Restaura `node_states/outputs/variables/history`, pero no `node_retries`, `node_started_at` ni `node_retry_after`, y no trunca los checkpoints posteriores. Es un "rollback" solo de estado: las acciones ya ejecutadas (despliegues, escrituras) no se compensan; debe documentarse. `retry_node` vuelve a `RUNNING` un workflow `FAILED` sin limpiar `finished_at`, y el scheduler ya lo habrá desregistrado al llegar al estado terminal, de modo que nadie lo avanza.

**W-14 — Código declarado pero sin implementar.** `ControlConfig.merge_policy="reduce"`, `default_edge`, `input_mapping`/`output_mapping` de `WorkflowNode`, `approver_role` y `routing_mode=AUTOMATIC` (solo toma `candidates[0]`: no hay enrutamiento real) aparecen en el modelo/documentación pero no tienen efecto. `tool_registry.has_tool(...)` se invoca en `engine.py`, pero `ToolRegistry` **no define `has_tool`**: esa rama del motor es código muerto. Además `validate_graph` no valida que un `TASK` tenga `tool_name`, que un `AGENT` tenga `agent_id`, ni que un `IF/DECISION` tenga condición o aristas coherentes.

### 4B.3 🟡 Eficiencia y calidad

- **Crecimiento O(n²) de checkpoints:** `execute_node` crea un checkpoint por nodo con copia de *todas* las salidas y variables, y la lista `checkpoints` no se acota. El scheduler persiste `get_execution()` (que re-serializa todos los checkpoints a JSON) **cada 0,5 s por ejecución**, aunque no haya cambios. En un bucle de 1.000 iteraciones el JSON persistido crece de forma cuadrática. Medidas: persistir solo si cambió el estado (hash/versión), limitar checkpoints (anillo o solo los de nodos con `backtrack` habilitado) y almacenar *deltas*.
- **Fuga de datos hacia agentes/eventos:** `resolved_inputs = dict(self.context.variables)` pasa **todas** las variables (incluidas las internas `_loops`, `_budget`, `_agent_state`) al *handler* y al payload del mensaje al agente; las salidas completas de cada nodo se publican en el `EventStore` (`EXECUTION_COMPLETED`). Debe usarse una lista de entradas declaradas (`input_mapping`, hoy inutilizado) y redactar secretos.
- **Nombres colisionables:** `resolved_inputs.update(upstream_output)` fusiona las salidas de predecesores en el mismo espacio plano, y `resolve_field` hace una búsqueda de *primer acierto* entre las salidas de todos los nodos (orden de inserción), por lo que el resultado depende del orden y puede resolver el campo de otro nodo. `_navigate` además recorre atributos con `getattr`, innecesario para datos JSON.
- **Validación del grafo:** toda arista que apunta a un nodo `WHILE` se trata como *back-edge* permitida, incluida la de entrada. Un ciclo que pase por un `WHILE` pero no vuelva al cuerpo del propio bucle no se detecta como error. `WorkflowDefinition.from_dict` no convierte `datetime` anidados ni valida versiones.
- **`NOT` con varias condiciones** se evalúa como `not all(...)` (NAND), que no es la lectura habitual de "ninguna se cumple". Documentar o restringir a una condición.
- **Parseo silencioso:** una condición no interpretable en `DECISION` devuelve `matched=False` sin error, y en `WHILE` provoca una salida inmediata del bucle; una cadena arbitraria se interpreta como `field is_true`. Preferible fallar de forma explícita en validación.
- **API:** `POST /execute?max_steps=` no tiene tope superior (un cliente puede enviar un valor enorme que bloquea un hilo del servidor); las rutas son síncronas y el estado se guarda en memoria del proceso (varios *workers* de uvicorn darían estados distintos).
- **Plantilla `code_review_loop`** queda en `WAITING_APPROVAL` en el `human_gate` (esperado), pero ese estado solo se resuelve por `engine.approve_node`, de nuevo sin control de rol.
- **Tests:** hay 12 ficheros de test de workflows, pero ninguno cubre W-01/W-02 (porque están "diseñados" así), W-03 (límite exacto de `WHILE`), W-05 (dos ejecuciones simultáneas), W-08 (round-trip conserva `timeout_seconds`/`metadata`) ni W-10. El test `test_contracts_and_editor_roundtrip.py` debería haber detectado W-08: conviene revisar qué compara.

### 4B.4 Plan específico para workflows

| Prioridad | Acción |
| :---: | :--- |
| P0 | W-01: desactivar `allow_synthetic_fallback` por defecto y marcar simulaciones. W-02: enrutar cada `TASK`/`AGENT` por `PolicyEngine` → capability → `SecureExecutor`; política *fail-closed* y `metadata` de política no editable por la API. W-03: corregir el orden condición/límite en `WHILE`. |
| P1 | W-04: un único punto de entrada con bloqueo por motor (o actor/cola por ejecución) y guarda de estados terminales. W-05: correlacionar por `message_id`/`in_reply_to`, validar emisor y tipo; buzón por ejecución. W-06/W-07: unificar en un solo orquestador (`WorkflowService` + scheduler) con una ejecución por motor y exponer sus rutas; invalidar/recargar el motor al editar o impedir editar con ejecución activa. |
| P1 | W-08/W-09: `model_copy`, bloqueo y `version` esperada (`If-Match`); no tragar errores de persistencia. W-10: manejo de errores de evaluación. W-12: `require_permission(SESSION_EXECUTE)` + verificar `approver_role` + registrar aprobador y consumir aprobaciones. |
| P2 | W-11, W-13, W-14, optimización de checkpoints, `input_mapping`, validaciones extra en `validate_graph`, tests de regresión de cada hallazgo. |

---

## 5. Funcionalidades — estado por área

| Área | Estado | Observaciones |
| :--- | :---: | :--- |
| Runtime: policy, capability HMAC, executor | ✅ Completo | Bien probado (propiedades + concurrencia) |
| Sandbox `CONTAINER` | ✅ Completo (no ejecutado aquí) | Fail-closed; requiere Docker/Podman |
| Sandbox `LOCAL_RESTRICTED` / `WASM` | ⚠️ Parcial | Ver §3.5; WASM sin `wasmtime` es un alias |
| `FULL_ACCESS` | ⚠️ Funcional, revisar | Catch-all de shell por nombre de herramienta |
| Context manager (L1/L2 cache, presupuestos) | ✅ | |
| Razonamiento (ToT, clasificador, completado) | ✅ | Heurísticas basadas en listas de cadenas en español; frágiles ante otros idiomas |
| Providers (TypeSafe, LAYA, Router, resiliencia) | ✅ / sin verificar | Dependen de servicios externos; independencia de TypeSafe "Fase 1" |
| Workflows (engine, editor, semantics) | ⚠️ Parcial | Motor de estados correcto en lo básico; la ruta REST es solo una simulación y hay 14 hallazgos (ver §4B) |
| Agentes distribuidos / bus | ⚠️ Parcial | `agents/distributed.py` y `context/cache.py` tienen interfaces con `NotImplementedError` (abstractas; confirmar que existe al menos una implementación para cada una) |
| Servidor REST + WebSocket | ⚠️ Con defectos críticos | Ver §3 |
| Auth / RBAC / multi-tenant | 🔴 Incompleto e inseguro | Ver §3.1–3.4 |
| Persistencia SQLite | ✅ | Consultas parametrizadas; no revisada la migración de esquema |
| MCP bridge / proxy middleware | ✅ | Ver §3.8 |
| Frontend (React 19 + Vite 8) | ✅ | 8 vistas con *lazy loading*; mayor chunk 211 kB (65 kB gz) |
| Telemetría Prometheus | ✅ | `/metrics` sin auth |
| CLI / dashboard TUI | ✅ | |

---

## 6. Tests y CI

- **Resultado real:** `887 passed, 1 failed, 1 skipped` en 193 s (889 recogidos). Los informes anteriores indican "839 passed, 0 failed": el recuento ha crecido pero **no hay "100 % verde"**.
- **Fallo:** `tests/security/test_wasm_sandbox.py::test_wasm_sandbox_command_execution` ejecuta `python -c …` y asume `python` en el PATH. En este equipo `python` resuelve al *stub* de Microsoft Store al usar `.venv\Scripts\python.exe` directamente. Es un test frágil, no un fallo del producto; usar `sys.executable`.
- **Hueco principal:** ningún test cubre los caminos de §3.1–3.3 (login sin clave, tipo de token, RBAC por ruta, aislamiento de tenant), lo que explica que sigan latentes pese a la suite. Añadirlos como tests de regresión.
- **CI:** dos workflows (matriz Ubuntu/Windows × 3.11/3.12; auditoría Bandit semanal con `-ll -ii`, que **oculta** los 65 hallazgos Low). Faltan: ruff/mypy, cobertura mínima, `pip-audit`/dependabot, build y lint del frontend, y tests con Docker real.

---

## 7. Higiene del repositorio y documentación

- Versionados pero prescindibles: `jev_navigator/` (shim legacy), `benchmark_results/` y su copia `historical/` (21 ficheros duplicados), `openapi.json` (200 KB, generable), `generate_audit_pdf.py`, `__pycache__`. Sin versionar pero presentes: `dist/` (22 MB), `videos/` (220 MB) y `docs/GUION_VIDEO_PROMOCIONAL.md` están **sin seguimiento** (`git status`); decidir si van a `.gitignore` o a almacenamiento externo (Git LFS).
- `.gitignore` ignora `scratch/` pero el commit contiene `scratch/*.py` anteriores; un fichero llamado `uv.lock/` aparece como directorio en `git ls-files` (revisar).
- Dos informes de auditoría en la raíz (más este) afirman "0 críticas", "false-allow 0 %" y "seguridad auditada"; tras esta revisión conviene **marcar como obsoletas** esas afirmaciones o consolidar en `docs/`.
- `pyproject.toml`: dependencias de producción (`google-genai`, `fastapi`, `uvicorn`, `websockets`) sin `server` extra real (duplicadas en `dependencies` y `[server]`); `laya`/`typesafe-sdk` como extras opcionales pero `dev` exige `typesafe-sdk` (un tercero de pago/clave). Sin límite superior de versiones ni `pip-audit`.
- README de >43 KB; útil, pero con insignias estáticas ("839 passed") que ya no coinciden.

---

## 8. Plan de acción priorizado

**P0 — antes de cualquier despliegue (≈1 día)**
1. Corregir el bypass de `/v1/auth/login` y no confiar en `role`/`tenant_id` del cliente (§3.1).
2. `authenticate_jwt`: verificar `type=="access"`, `iss`, `exp` obligatorio (§3.2).
3. Eliminar el secreto JWT por defecto; fallar al arrancar si falta con auth activa (§3.4).
4. Aplicar `require_permission` en todas las rutas mutantes y añadir `tenant_id` a sesiones/eventos/workflows con filtrado en el repositorio (§3.3).
5. Tests de regresión para 1–4.

**P0-bis — Workflows (≈1 día):** W-01 (sin *fallback* sintético por defecto), W-02 (supervisión real y política *fail-closed*), W-03 (límite `WHILE`). Detalle completo en §4B.4.

**P1 (≈2–3 días)**
6. Autenticar `/metrics`, `/docs`, `/openapi.json` en producción; JWT/cookie en WebSocket y retirar `?token=`; ligar tickets al usuario/sesión (§3.6).
7. Cambiar `compare_digest` a bytes; persistir/acotar la lista de revocación.
8. Reescribir `is_observational` con `shlex` + lista blanca, y retirar las subcadenas de test de `_is_dynamic_cli_tool` (§3.8).
9. Sustituir el catch-all de `FullAccessExecutor` por herramientas registradas; ampliar `BLOCKED_ENV_VARS` (§3.5).
10. Corregir `Set` no importado y los duplicados/redefiniciones de §4; hacer pasar `ruff --select F,B904`.

**P2 (planificación)**
11. Dividir `dependencies.py`/`RuntimeApplicationService`.
12. Añadir ruff + mypy (modo gradual) + cobertura (≥80 %) + `pip-audit` + job de frontend a CI; quitar `-ll -ii` o complementar con un informe completo.
13. Documentar honestamente los límites de `LOCAL_RESTRICTED`/`WASM`; actualizar `SECURITY.md`, README y badges.
14. Mover `api key` fuera de `localStorage` hacia cookies HttpOnly + CSRF token.
15. Limpiar repositorio (`benchmark_results/historical`, `openapi.json`, `jev_navigator`, `dist`, `videos`).
16. Retirar los alias `jev-*` y el prefijo `JEV_` según el calendario v2.0.

---

## 9. Conclusión

El *producto diferencial* de PRAXEON —el pipeline determinista que impide que un LLM ejecute acciones sin capability— es técnicamente sólido y está bien verificado. El riesgo reside en el **perímetro**: la capa de API/auth, añadida más tarde, tiene un fallo de autenticación explotable sin credenciales y funcionalidades de seguridad (RBAC, multi-tenancy) que se anuncian pero no se aplican. Son correcciones acotadas y bien localizadas; una vez resueltas y cubiertas con tests, el proyecto estaría en condiciones de respaldar las afirmaciones de seguridad de su documentación.

*Este informe se generó con revisión automatizada asistida; los hallazgos 3.1, 3.2, 3.6 (parcial), 3.8 y W-01, W-03, W-04 (parcial), W-06, W-08 y W-10 fueron reproducidos con scripts ejecutables; el resto se basa en lectura de código y herramientas estáticas. Los puntos W-05 y W-07 se derivan de la lectura del código (no se ejecutaron dos ejecuciones simultáneas con un bus real).*

---

## 10. Registro de correcciones aplicadas (2026-10-11)

| ID | Estado | Cambio |
| :--- | :---: | :--- |
| 3.1 Bypass de login | ✅ Corregido | `routes/auth.py`: con clave configurada, `api_key` es obligatoria; sin clave y con auth exigida → 503 (fail-closed) |
| 3.2 Refresh como access | ✅ Corregido | `authenticate_jwt` exige `type=="access"`; `decode_jwt_hs256` exige `exp` numérico e `iss=="praxeon-auth"` |
| 3.4 Secreto JWT por defecto | ✅ Corregido | Sin secreto configurado se genera uno aleatorio por proceso; ya no se reutiliza la API key como clave de firma |
| 3.3 RBAC | ✅ Parcial | Nueva dependencia `require_route_permission` aplicada a todos los routers (lectura = `*_READ`, métodos mutantes = permiso de escritura). **Pendiente:** aislamiento por `tenant_id` en datos |
| 3.6 `compare_digest` no ASCII | ✅ Corregido | Helper `constant_time_equals` (bytes) en HTTP y WebSocket |
| W-01 Simulación por defecto | ✅ Corregido | `WorkflowEditorService(allow_simulation=…)` o `PRAXEON_WORKFLOW_SIMULATION=1`; por defecto fail-closed; salidas sintéticas marcadas `"simulated": true` |
| W-03 Límite `WHILE` | ✅ Corregido | El límite solo actúa si la condición sigue siendo verdadera |
| W-04 (parcial) | ✅ Parcial | `cancel()` no altera estados terminales y `_finalize_terminal_state` no pisa `FAILED/CANCELLED`. **Pendiente:** bloqueo en el resto de mutaciones |
| Test frágil wasm | ✅ Corregido | Usa `sys.executable` |

Tests de regresión añadidos: `tests/security/test_auth_p0_regressions.py` (6) y `tests/workflows/test_p0_review_regressions.py` (5).

**Pendiente de P0:** aislamiento multi-tenant real (§3.3) y W-02 (enrutar workflows por `PolicyEngine`/capability/`SecureExecutor`, política fail-closed); ambos requieren decisiones de diseño.

### 10.1 Segunda tanda (P1 y workflows)

| ID | Estado | Cambio |
| :--- | :---: | :--- |
| 3.6 `/metrics`, `/docs`, `/openapi.json` | ✅ | `/metrics` exige `METRICS_READ`; en `production` se desactivan `/docs`, `/redoc` y `/openapi.json` |
| 3.6 WebSocket | ✅ Parcial | Acepta JWT de acceso (cabecera, query o cookie HttpOnly) con permiso `session:read`. Pendiente: retirar `?token=` y ligar tickets al usuario |
| 3.5 Variables de entorno del sandbox | ✅ | Se purgan también `TOKEN`, `PASSWD`, `CREDENTIAL`, `PRIVATE_KEY` |
| 3.8 `is_observational` | ✅ | Lista blanca con `shlex`, rechazo de metacaracteres de shell y de flags destructivos (`-delete`, `-exec`, `branch -D`, …) |
| W-02 (parcial) | ✅ Parcial | El puente ya no autoriza por omisión: una herramienta `TASK` no catalogada o de riesgo alto exige aprobación humana (`HUMAN_CONFIRMATION_REQUIRED`); las aprobaciones se consumen (valen una sola vez). Pendiente: capability HMAC + `SecureExecutor` |
| W-05 | ✅ | El motor solo consume mensajes de su propia ejecución, valida el emisor, ignora respuestas de otros agentes y trata `NACK`/`ALERT`/`error` como fallo del nodo |
| W-06 | ✅ | El motor cacheado se descarta si la definición cambió de versión (salvo ejecución activa) |
| W-08 | ✅ | El editor usa `model_copy`: conserva `timeout_seconds`, `metadata`, `created_at` |
| W-09 | ✅ | Bloqueo por servicio en todas las mutaciones; los fallos de persistencia se registran en lugar de silenciarse |
| W-10 | ✅ | Comparaciones con tipos incompatibles devuelven `False` en vez de lanzar `TypeError` |
| Checkpoints / persistencia | ✅ | Máximo 200 checkpoints por ejecución; el scheduler solo persiste si el estado cambió |
| API `execute` | ✅ | `max_steps` limitado a 1–1000 |

**Sigue pendiente:** aislamiento multi-tenant en datos (§3.3), capability + `SecureExecutor` en workflows (W-02), W-04 completo (bloqueo en `pause/resume/retry/backtrack/scheduler`), W-07 (unificar orquestadores), W-11 a W-14, división de `dependencies.py`, limpieza de lint (311 imports sin usar) y documentación honesta de los límites de `LOCAL_RESTRICTED`/`WASM` (§3.5).

### 10.2 Decisión de alcance: sin multi-tenant

PRAXEON es de uso personal y de un único usuario, por lo que **el aislamiento multi-tenant (§3.3, segunda viñeta) queda descartado** y deja de ser un hallazgo pendiente. El campo `tenant_id` se mantiene solo como etiqueta informativa por compatibilidad con la API y los tests, y la documentación del código deja de describirlo como "multi-tenant SaaS". El RBAC se conserva como defensa en profundidad (por ejemplo, para dar un token de solo lectura a un panel o a una integración). Si algún día se ofreciera como servicio a terceros, habría que retomarlo desde el diseño de datos.

### 10.3 W-02 resuelto: workflows bajo la cadena de supervisión

Nuevo módulo `praxeon/workflows/supervised.py` (`SupervisedToolRunner`). Cada nodo `TASK` con herramienta recorre `ActionCandidate → PolicyEngine → DecisionReceipt (HMAC) → SecureExecutor → sandbox`, igual que el resto de PRAXEON:

- **ALLOW** → se ejecuta; la salida lleva `decision_id` y `supervised: true`.
- **ABSTAIN** (herramienta desconocida, riesgo que exige confirmación) → el nodo pasa a `WAITING_APPROVAL` sin ejecutarse. Al aprobar, se emite un ticket auditable (`approved_by`, hash de la acción) y el nodo se reejecuta ya autorizado. Una herramienta no registrada y sin handler sigue sin poder ejecutarse (fail-closed).
- **BLOCK/REPLAN** (p. ej. `rm -rf /`) → el nodo falla y su salida de error queda registrada.
- Aprobar un nodo bloqueado por el puente ya **ejecuta** la herramienta; antes el editor lo marcaba como completado sin ejecutarlo.
- El modo simulación (`allow_simulation`) sigue siendo explícito y desactiva el runner.

Tests: `test_task_runs_through_capability_chain`, `test_dangerous_command_is_blocked_by_policy`, `test_unknown_tool_needs_approval_and_still_fails_closed`.

### 10.4 Robustez del motor y orquestación unificada (W-04, W-07, W-11, W-13)

| ID | Estado | Cambio |
| :--- | :---: | :--- |
| W-04 | ✅ | `pause/resume/retry_node/backtrack_to_node/get_evaluation_context/check_scheduled_retries_and_timeouts` y toda la rama `WHILE` se ejecutan bajo el `RLock` del motor; un resultado que llega tras `cancel()` se descarta; el scheduler ya no toca el estado fuera del bloqueo |
| W-07 | ✅ | `engine.tick()` (timeout global, reintentos, timeouts de nodo y respuestas de agentes) es común al scheduler y al puente interactivo. Nuevas rutas por ejecución, con un motor por ejecución y scheduler: `POST /v1/workflows/{id}/executions`, `GET /v1/workflows/executions/{eid}`, `POST …/cancel` y `POST …/nodes/{nid}/approve`. Usan la misma supervisión (`SupervisedToolRunner`) y la simulación solo si es explícita |
| W-11 | ✅ | `WorkflowDefinition.lint_graph()` avisa de fan-in implícito OR sin `PARALLEL_JOIN`, `TASK` sin `tool_name` y `AGENT` sin `agent_id`; `POST /validate` devuelve `warnings` |
| W-13 | ✅ | `backtrack_to_node` reinicia reintentos y temporizadores, poda los checkpoints posteriores y limpia `finished_at`; `retry_node` limpia `finished_at` |

Tests: `tests/workflows/test_w07_unified_orchestration.py` (6). **Pendiente de workflows:** W-12 (verificar `approver_role`; hoy el RBAC de ruta exige `workflow:write`), W-14 (campos declarados sin implementar: `merge_policy="reduce"`, `default_edge`, `input_mapping/output_mapping`, rama `has_tool`).

### 10.5 Limpieza de código

Eliminados 309 imports sin usar (F401) en 96 ficheros de `praxeon/` (excluidos los `__init__.py`, que son reexportaciones). Un verificador de imports (`from praxeon.X import Y` en `praxeon/`, `tests/`, `scripts/`, `examples/`) detectó que `domain/models.py` actúa como *hub* de reexportación (`sign_receipt`), por lo que se restauró. Suite: 916 pasados, 1 omitido; el *benchmark* de CLI funciona. **Pendiente de calidad:** dividir `server/dependencies.py` (3.200 líneas), añadir ruff/mypy y cobertura al CI.

### 10.6 Lint y CI

- **Lint:** configuración `ruff` en `pyproject.toml` con reglas bloqueantes `F, E9, B904, B033, B007, B905`. Corregidos 33 `raise` sin causa (`from exc`/`from None`), 8 `zip` sin `strict`, 10 variables de bucle sin usar, 39 variables sin usar, 10 f-strings sin marcadores, 3 conjuntos con duplicados y las redefiniciones de `dependencies.py`. `ruff check .` pasa limpio sobre todo el repositorio.
- **Cobertura:** medida real **77 %** (19.742 sentencias, con ramas). Umbral en CI: **70 %** (margen para diferencias Linux/Windows). Módulos más flojos: `session_service` (21 %), `coordinators` (37 %), `dashboard` (41 %), `cli` (45 %), `live_agent/llm_adapter` (45 %).
- **CI (`test.yml`):** nuevo job `lint`; la combinación Ubuntu/3.11 ejecuta pytest con cobertura y `--cov-fail-under=70` y sube `coverage.xml` como artefacto. (Corrección al apartado 6: el job de frontend con `oxlint` y `vite build` ya existía.)
- **Seguridad (`security.yml`):** Bandit bloqueante en severidad media/alta; informe completo de Bandit y `pip-audit` como pasos informativos.
- **Dependabot:** `pip`, `npm` (web) y `github-actions`.
- `pytest-cov` y `ruff` añadidos al extra `dev`; `uv.lock` regenerado.

**No incluido a propósito:** `mypy` (la base de código no está anotada de forma uniforme; activarlo ahora produciría cientos de errores sin valor) y las reglas `S110` (≈44 `try/except/pass`) y `B008` (falsos positivos de `Depends` en FastAPI). Candidatas a una segunda fase.
