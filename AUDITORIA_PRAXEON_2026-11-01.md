# Auditoría formal de PRAXEON v1.1.0

| | |
|---|---|
| **Proyecto** | PRAXEON — runtime de supervisión para agentes autónomos (repo `jev-reasoning-navigator`) |
| **Versión / commit auditado** | v1.1.0 · `2327e1f` (rama `main`, árbol limpio) |
| **Fecha** | 2026-11-01 |
| **Alcance** | Backend Python (`praxeon/`), CI, empaquetado, dependencias, arquitectura, rendimiento y calidad. Frontend (`web/`) y SDK solo a nivel estructural |
| **Método** | Lectura dirigida del código crítico + métricas estáticas (AST) + pruebas empíricas puntuales (sondas contra el clasificador y el sandbox) |

---

## 0. Cómo leer este informe (alcance y límites)

El proyecto tiene ~95 000 líneas (~61 500 de código en `praxeon/`, ~33 800 de tests, 601 ficheros versionados). **No se ha leído línea a línea.** Para ser útil y honesto, cada hallazgo lleva una etiqueta de evidencia:

- **[V] Verificado**: reproducido ejecutando código o medido con herramienta.
- **[L] Leído**: observado directamente en el código fuente citado (fichero:línea).
- **[H] Hipótesis**: inferido, pendiente de comprobar.

**Revisado en profundidad:** `runtime/sandbox.py`, `runtime/executor.py`, `runtime/nonce_store.py`, `runtime/full_access.py` (parcial), `policy/engine.py`, `policy/egress.py` (parcial), `reasoning/classifier.py` (vía sondas), `domain/decision.py` (firma de recibos), `server/app.py`, `server/dependencies.py`, `server/services/auth_service.py`, `server/routes/auth.py`, `server/websocket.py` (autenticación), `workspace_files.py`, `persistence/` y almacenes SQLite (patrón), CI y `pyproject.toml`.

**No revisado o solo superficialmente:** `workflows/engine.py` (semántica interna), `context/` (caché L1/L2), `agents/distributed.py`, `evaluation/` y scripts de benchmark (validez estadística), `interceptor/`, frontend React (`web/src`), `packages/sdk`, `desktop/`. Las conclusiones sobre estas áreas son estructurales, no de corrección.

**Tests:** la suite se ejecutó durante la auditoría: 1454 pasan, 4 saltados, 3 deseleccionados, 0 fallos (ver §9).

---

## 1. Resumen ejecutivo

PRAXEON es un proyecto **ambicioso y, en su núcleo, bien concebido**: la separación *propuesta (LLM) ≠ juicio semántico ≠ política ≠ capability firmado ≠ ejecución* es una buena arquitectura de defensa en profundidad, y hay decisiones de diseño maduras (desconocido ⇒ revisión humana, nunca bloqueo ciego; recibos HMAC ligados a acción + estado + sesión + modo + nonce; anti-replay persistente; CSRF/CSWSH; tickets WebSocket de un solo uso). El equipo ya ha pasado por rondas de auditoría previas (ficheros `test_audit_*`, `coderabbit_audit_remediations`) y se nota.

Los riesgos principales no están en el diseño de la capa de políticas, sino en **tres frentes**:

1. **El aislamiento real es más débil que el que sugiere la documentación.** `LocalProcessSandbox` no es un sandbox: es filtrado de entorno + contención de rutas + listas de comandos. El bloqueo de red se elude con un `python -c` (verificado). La seguridad efectiva descansa casi por completo en el clasificador de comandos y en la confirmación humana.
2. **Autenticación/autorización con atajos de diseño** (una sola clave para tres funciones criptográficas, RBAC que el propio solicitante puede elegir, modo desarrollo sin autenticación en loopback sin defensa frente a DNS-rebinding).
3. **Deuda estructural** que frenará la evolución: funciones gigantes (hasta 756 líneas / 156 ramas), un objeto de servicio "dios", duplicación de módulos, seis almacenes SQLite con código repetido y sin migraciones.

### Cuadro de hallazgos

| Severidad | Nº | IDs |
|---|---|---|
| 🔴 Alta | 3 | S-01, S-02, S-03 |
| 🟠 Media | 9 | S-04, S-05, S-06, S-07, F-01, A-01, A-02, A-03, A-04 |
| 🟡 Baja | 24 | S-08…S-13, F-02…F-06, A-05…A-09, P-01…P-04, Q-01…Q-04 (ver tablas) |

### Top 8 acciones recomendadas (orden de prioridad)

1. Separar `PRAXEON_SECRET_KEY` en tres claves independientes (API, JWT, HMAC de recibos) — **S-03**.
2. Dejar de presentar `LocalProcessSandbox` como aislamiento de red; documentarlo, y para ejecución no confiable exigir `container` por defecto — **S-01**.
3. Exigir token siempre (como ya hace la app de escritorio) y validar `Host` en el servidor web — **S-04/S-05**.
4. Clasificar lectura de ficheros sensibles (`.env`, `~/.ssh`, `*.pem`, credenciales cloud) como *confirmación requerida* — **S-02**.
5. Corregir el bug del contador `REVIEW` en el resumen de sesión — **F-01**.
6. Trocear `_run_llm_loop`, `_propose_action_impl`, `execute_node`, `evaluate_action` — **A-01**.
7. Extraer una base común para SQLite (conexión reutilizada, WAL una vez, `PRAGMA user_version` + migraciones) — **A-04**.
8. Ampliar ruff (`E`, `B`, `UP`, `SIM`, `PERF`, `S`) y añadir `mypy` al núcleo (`domain/`, `policy/`, `runtime/`) — **Q-01**.

---

## 2. Inventario y métricas del proyecto

### 2.1 Tamaño por paquete (líneas Python, `praxeon/`)

| Paquete | Líneas | Responsabilidad |
|---|---:|---|
| `server/` | 9 797 | API FastAPI, servicios, coordinadores, WebSocket, pipeline de decisión y misiones |
| `evaluation/` | 6 370 | Benchmarks, escenarios, ablación, afinado (fine-tune) |
| `runtime/` | 6 035 | Ejecutor, sandbox, nonces, estado, eventos, runtime adaptativo |
| `workflows/` | 4 668 | Motor de workflows (grafo), editor, semántica |
| `providers/` | 3 204 | Proveedores supervisores (LAYA, TypeSafe, KEV, mock, router) |
| `reasoning/` | 2 659 | Clasificador de comandos, evaluador, tree-of-thoughts, evidencia |
| `context/` | 2 173 | ContextManager (presupuesto, caché L1/L2, delta) |
| `agents/` | 2 172 | Registro, bus, gobernanza, distribuido |
| `policy/` | 1 772 | PolicyEngine, riesgo, egress, permisos, failsafe |
| `core/` | 1 695 | Motor JEV, grafo de estado, cliente TypeSafe |
| `domain/` | 1 630 | Modelos puros (acciones, decisiones, recibos) |
| `routing/` | 1 503 | Router de modelos/estrategias |
| `interceptor/` | 1 400 | Proxy MCP / middleware |
| `live_agent/` | 1 281 | Runner de misiones en vivo |
| `integrations/` | 1 239 | Servidor y cliente MCP |
| Resto (`persistence`, `web`, `desktop`, `models`, `telemetry`) | 2 562 | |
| Módulos raíz (`agent_llm`, `dashboard`, `cli`, `config`…) | 4 500 | |

### 2.2 Métricas de complejidad [V]

- **1 903 funciones**; **144** superan 60 líneas y **74** superan 100.
- Mayores funciones (líneas / puntos de ramificación):

| Función | Líneas | Ramas |
|---|---:|---:|
| `server/runtime_service/mission_runtime.py::_run_llm_loop` | 756 | 156 |
| `server/runtime_service/decision_pipeline.py::_propose_action_impl` | 522 | 109 |
| `workflows/engine.py::execute_node` | 403 | 101 |
| `policy/engine.py::evaluate_action` | 356 | 89 |
| `live_agent/mission_runner.py::run_live_agent` | 356 | 64 |
| `dashboard.py::_execute_single_live_task` | 418 | 63 |
| `reasoning/classifier.py::_classify_atomic` | 303 | 38 |
| `runtime/executor.py::execute` | 244 | 32 |

- Ficheros más grandes: `mission_runtime.py` 1 551, `workflows/engine.py` 1 457, `decision_pipeline.py` 1 324, `agent_llm.py` 1 150, `dashboard.py` 1 127, `server/dependencies.py` 1 070.
- **216** `except Exception` en `praxeon/`; **43** de ellos terminan en `pass` [V, grep].
- **~1 004** apariciones de `Any` (tipado laxo en las fronteras) [V, grep].
- Tests: 184 ficheros, ~33 800 líneas (ratio test/código ≈ 0,55) — saludable.

### 2.3 Higiene del repositorio

- Historial: 210 commits entre 2026-09-22 y 2026-11-01 (desarrollo muy rápido, ~5 commits/día).
- Artefactos compilados versionados: `praxeon/server/static/` (658 KB de JS minificado).
- `openapi.json` duplicado (raíz y `packages/sdk/`).
- `scratch/` con 2 scripts ajenos al producto (`inspect_marq_tasks.py`, `populate_post_roadmap_tasks.py`) [L].
- Nomenclatura heredada **JEV** todavía en ~48 ficheros (docstrings "JEV Reasoning Navigator v0.2", alias CLI `jev-*`, variable `JEV_SANDBOX_ACTIVE`, `JEV_API_KEY`, directorio `.jev_cache`).

---

## 3. Funcionalidades: qué hacen y cómo están implementadas

Valoración: ✅ sólida · ⚠️ funciona con reservas · ❓ no evaluada en profundidad.

| # | Funcionalidad | Implementación (dónde) | Estado | Comentario |
|---|---|---|---|---|
| 1 | **Motor de políticas** | `policy/engine.py::PolicyEngine.evaluate_action` — cadena de reglas ordenada: herramienta prohibida → privilegio/destructivo (BLOCK determinista) → evidencia requerida → completitud → proveedor caído (failsafe) → juicio semántico (LAYA/KEV/TypeSafe, con modo *advisory*) → riesgo reconciliado → confirmación humana | ✅ | Diseño claro y prioridades razonables. La función concentra demasiada lógica (356 líneas); ver A-01 |
| 2 | **Capability firmado (recibo)** | `domain/decision.py::compute_receipt_signature` (HMAC-SHA256 sobre `decision_id:session:action_hash:state_hash:nonce:status:expires:mode`) + verificación en `runtime/executor.py::execute` | ✅ | Bien ligado a acción, estado, sesión, modo y expiración (TTL 60 s). Ver S-07/S-08 para matices |
| 3 | **Anti-replay** | `runtime/nonce_store.py` — `InMemoryNonceStore` y `SqliteNonceStore` (persistente, WAL) | ✅ | Consumo atómico. Detalles menores en S-08 |
| 4 | **Clasificador de comandos** | `reasoning/classifier.py` (749 líneas, regex + división de comandos compuestos) | ⚠️ | Falla a favor de la seguridad (ofuscación ⇒ `UNKNOWN` ⇒ revisión), pero ver §4.2 y S-02 |
| 5 | **Ejecución en sandbox** | `runtime/sandbox.py`: `LocalProcessSandbox`, `ContainerSandboxAdapter`, `DryRunSandbox`; `full_access.py` para modo host | ⚠️ | Contención de rutas con comprobación de symlinks y salida acotada: bien. Aislamiento de red/proceso: débil (S-01) |
| 6 | **Egress / SSRF** | `policy/egress.py` — modos, bloqueo de metadatos cloud, loopback, IP privadas | ⚠️ | Extracción de hosts por regex de cadenas de comando; no cubre construcción dinámica de URLs |
| 7 | **Redacción de secretos** | `policy/sanitizer.py` aplicada a la salida del ejecutor (`executor.py:292`) | ✅/⚠️ | Cubre claves privadas PEM, `sk-…`, asignaciones `clave=valor`. Basada en patrones: no detecta secretos sin prefijo reconocible |
| 8 | **Autenticación** | `auth_service.py` (JWT HS256 propio, rotación de refresh, revocación persistente SQLite, tickets WS) + `dependencies.py` | ⚠️ | Funcional y con defensas CSRF; ver S-03…S-06 |
| 9 | **API REST + WebSocket** | `server/routes/*` (12 routers), `websocket.py` (CSWSH, tickets, recuperación de huecos) | ✅ | Superficie amplia y cuidada. Sincronía/threading mezclado (A-05) |
| 10 | **Misiones del agente** | `server/runtime_service/mission_runtime.py` (`_run_llm_loop`), `live_agent/`, `dashboard.py` | ⚠️ | Funcional pero con 3 implementaciones solapadas del bucle de agente (A-02) |
| 11 | **Workflows en grafo** | `workflows/engine.py`, `semantics.py`, `mission_driver.py` | ❓ | Condiciones tipadas sin `eval` (bien). Motor de 1 457 líneas con `execute_node` de 403 |
| 12 | **Gestión de contexto** | `context/manager.py` (presupuesto, caché L1/L2, delta, fingerprint) | ❓ | Arquitectura modular razonable (≈10 módulos pequeños) |
| 13 | **Agentes / gobernanza / bus** | `agents/` (registro, bus, router, `distributed.py`) | ❓ | `distributed.py` (408 líneas) no evaluado; el sistema es de proceso único (A-05) |
| 14 | **Cliente y servidor MCP** | `integrations/mcp_client/`, `integrations/mcp/server.py`, `routes/mcp.py` | ✅ | Buen modelo de confianza: un servidor MCP nuevo/cambiado queda *desactivado y sin aprobar*; aprobación por huella de herramientas |
| 15 | **Recuperación tras caída** | `state_store.reconcile_interrupted_missions` (invocado en el arranque) | ✅ | Buena práctica |
| 16 | **Editor/visor de workspace** | `workspace_files.py` — `resolve_inside` rechaza `..`, absolutas, `\0`, symlinks externos; ETag para conflictos (409) | ✅ | Notas: el editor humano **no pasa por PolicyEngine** (decisión explícita documentada) |
| 17 | **App de escritorio** | `desktop/app.py` — servidor solo en 127.0.0.1 con token aleatorio por ejecución | ✅ | El modelo de seguridad correcto; debería ser el único modo local (S-05) |
| 18 | **Benchmarks y fine-tuning** | `evaluation/`, `scripts/`, `benchmark_results/` | ❓ | Se retiraron veredictos hard-coded (commits recientes: bien). Falta revisar tamaño muestral e intervalos de confianza de las cifras del README (78 % vs 63 %) |
| 19 | **Telemetría** | `telemetry/prometheus.py`, `/metrics` protegido por permiso | ✅ | |
| 20 | **Frontend React + SDK TS** | `web/`, `packages/sdk/` | ❓ | 9 vistas con carga perezosa (chunks por vista). No auditado |

---

## 4. Hallazgos de seguridad

### 4.1 Hallazgos

#### 🔴 S-01 — `LocalProcessSandbox` no aísla la red ni el proceso [V]
- **Dónde:** `runtime/sandbox.py:233-244` (`_is_network_command`), `:165-173` (proxies).
- **Qué ocurre:** el bloqueo de red se hace tokenizando el texto del comando contra una lista (`curl`, `wget`, `nc`…) y buscando `http://`. Sondas sobre `_is_network_command`: `python -c "…urlopen('http://example.com')…"` → `True` (solo porque aparece `http://` literal), pero `python -c "…urlopen('ht'+'tp://example.com')…"` → **`False`** y `python -c "import socket; socket.create_connection(('1.2.3.4',80))"` → **`False`**. La detección es puramente léxica: cualquier cliente que construya la URL en runtime o use sockets directos pasa el filtro. Las variables `HTTP_PROXY=127.0.0.1:0` son una convención que cualquier cliente puede ignorar (sockets directos, `--noproxy`, `NO_PROXY`).
- **Impacto:** con `local_restricted` un comando que el clasificador deje pasar como `ALLOW` puede abrir conexiones salientes y exfiltrar datos del workspace. La barrera real pasa a ser el clasificador (capa 1) y el humano (capa 2).
- **Recomendación:**
  1. Renombrar y documentar el modo como *"mitigación de errores, no frontera de seguridad"*.
  2. Para ejecución con modelos no confiables, hacer `container` el predeterminado, con `--network none` y sistema de ficheros de solo lectura salvo el workspace.
  3. En Linux, valorar `bubblewrap`/`unshare -n`; en Windows, Job Objects + AppContainer o ejecutar en WSL/Docker.
  4. Eliminar las afirmaciones del README/docstrings que impliquen aislamiento ("sandbox aislado") para este adaptador.

#### 🔴 S-02 — Lectura de ficheros sensibles clasificada como solo-lectura sin confirmación [V]
- **Dónde:** `reasoning/classifier.py`.
- **Sonda:** `cat ~/.ssh/id_rsa` → `INSPECTION`, `read_only=True`; `cat .env` → ídem. `env` y `printenv` → `UNKNOWN` (se revisan, bien).
- **Impacto:** un comando de lectura sobre credenciales se autoriza sin intervención y su salida se devuelve al proveedor LLM (potencialmente en la nube). El sanitizador mitiga solo parcialmente (claves PEM y asignaciones `clave=valor`; no secretos sin patrón). Además `LocalProcessSandbox` solo valida el `cwd`, **no las rutas absolutas dentro del comando**, así que `cat /ruta/absoluta` no queda contenido en el workspace.
- **Recomendación:** lista de rutas/patrones sensibles (`.env*`, `*.pem`, `id_rsa*`, `~/.aws`, `~/.config/gcloud`, `~/.npmrc`, `*.kdbx`, perfiles de navegador, `credentials`, `secrets*`) ⇒ categoría `SENSITIVE_READ` ⇒ `ABSTAIN`/confirmación, también cuando el acceso es por `read_file` en modo `full_access`.

#### 🔴 S-03 — Una sola clave para API estática, JWT y firma de recibos [L]
- **Dónde:** `dependencies.py:952, 1009`; `websocket.py` (`expected_key = PRAXEON_API_KEY or PRAXEON_SECRET_KEY`); `auth_service.py:203`; `dependencies.py:117-130`; `app.py:164-166`.
- **Qué ocurre:** `PRAXEON_SECRET_KEY` actúa simultáneamente como (a) clave HMAC de los recibos de capability, (b) secreto de firma JWT y (c) API key estática aceptada en cabeceras si no existe `PRAXEON_API_KEY`. Un cliente que "es" la API key conoce la clave con la que se forjan JWT y recibos. `validate_network_binding` además considera `SECRET_KEY` suficiente como "autenticación" para exponerse en red.
- **Recomendación:** tres variables independientes (`PRAXEON_API_KEY`, `PRAXEON_JWT_SECRET`, `PRAXEON_RECEIPT_KEY`); eliminar el *fallback* de API key a SECRET_KEY; derivar subclaves con HKDF si se quiere un único secreto maestro de despliegue.

#### 🟠 S-04 — RBAC decorativo: el solicitante elige su rol [L]
- **Dónde:** `routes/auth.py` (`LoginRequest.role` por defecto `ADMIN`), `dependencies.py:954` (API key ⇒ `ADMIN`), `:926` (sin clave ⇒ `ADMIN`).
- **Impacto:** con clave válida (o sin clave en loopback) cualquiera obtiene `ADMIN`. Los roles *operator/auditor/viewer* y `tenant_id` no aportan seguridad; el docstring lo admite ("uso personal de un único usuario") pero el código y la documentación exhiben RBAC multi-tenant.
- **Recomendación:** o bien (a) eliminar el campo `role` del login y emitir siempre el rol que corresponde a la credencial (claves distintas por rol), o (b) retirar el RBAC y documentar claramente que es mono-usuario. Evitar la falsa sensación de control.

#### 🟠 S-05 — Modo dev sin autenticación en loopback y sin defensa frente a DNS-rebinding [L]
- **Dónde:** `dependencies.py:856-874`, `:920-931`; `app.py` (no hay `TrustedHostMiddleware`).
- **Impacto:** sin `PRAXEON_API_KEY`, cualquier petición desde loopback es `ADMIN` (incluye ejecutar misiones y editar ficheros). CORS limita lecturas cross-origin, pero **no impide DNS-rebinding**: una web maliciosa abierta en el navegador del usuario puede resolver un dominio propio a 127.0.0.1 y hablar con la API local con `Host` arbitrario. Se mitiga parcialmente porque `enforce_cookie_csrf` solo cubre peticiones con cookie.
- **Recomendación:** (1) validar `Host` contra una lista (`localhost`, `127.0.0.1`, `[::1]`) con `TrustedHostMiddleware`; (2) generar siempre un token aleatorio por arranque, como ya hace `praxeon app`; (3) dejar el modo sin token solo bajo `PRAXEON_ALLOW_INSECURE_DEV=1`.

#### 🟠 S-06 — Modo `full_access` con superficie amplia [L]
- **Dónde:** `dependencies.py:135` (`allow_full_access=True` fijo), `full_access.py:239-262`.
- **Qué ocurre:** el servicio habilita siempre `full_access`; este modo usa `shell=True` como *fallback* y hereda **todo** `os.environ` (incluye claves de API del propio PRAXEON). Al vencer el timeout `subprocess.run` mata el shell pero no necesariamente el árbol de procesos hijo.
- **Mitigación existente:** el recibo va ligado al `execution_mode`, y pasa por política/confirmación.
- **Recomendación:** hacer `allow_full_access` configurable (por defecto `False` salvo opt-in explícito), pasar entorno filtrado también en este modo (con lista blanca ampliada), usar grupos de procesos (`CREATE_NEW_PROCESS_GROUP`/`start_new_session`) para terminar el árbol completo.

#### 🟠 S-07 — Rutas *fail-open* silenciosas [L]
- `executor.py:206`: `if self.secret_key:` — si el ejecutor se construye sin clave (valor por defecto `None`) **no se verifica la firma**. El servicio sí pasa clave, pero el valor por defecto es inseguro.
- `auth_service.py:255-262`: `verify_api_key` devuelve `True` si no hay clave configurada.
- `auth_service.py:227-228, 248-249`: errores de SQLite al cargar/guardar revocaciones se ignoran (`except sqlite3.Error: pass`): un token revocado puede volver a ser válido tras reinicio sin que nadie se entere.
- **Recomendación:** que `SecureExecutor` exija clave (o un flag explícito `insecure_skip_signature=True` solo para tests); registrar (`logger.error`) los fallos de persistencia de revocaciones y, en producción, fallar cerrado.

#### 🟡 Hallazgos de severidad baja

| ID | Hallazgo | Dónde | Evidencia | Recomendación |
|---|---|---|---|---|
| S-08 | El nonce se **consume antes** de comprobar modo de ejecución y *gate* de `full_access`: una denegación posterior "quema" el recibo. Además `_consumed_receipts` (set en memoria) crece sin límite en procesos de larga vida | `executor.py:219 vs 232-253`, `:98` | [L] | Mover el consumo del nonce al final de todas las validaciones; acotar el set con TTL (o eliminarlo, el `NonceStore` ya es la fuente de verdad) |
| S-09 | Purga de entorno por subcadena: la lista contiene `SECRET_KEY`, `TOKEN`, `PASSWORD`… pero no `SECRET` a secas, por lo que variables como `AZURE_CLIENT_SECRET` no se purgarían; tampoco `AUTH`, `COOKIE`, `SESSION` | `sandbox.py:90-107, 156-159` | [L] | Ampliar a `SECRET`, `AUTH`, `COOKIE`; o mejor **lista blanca** de variables heredadas (`PATH`, `HOME`, `LANG`, `SystemRoot`…) |
| S-10 | JWT implementado a mano. Sin `nbf`/`aud`; el rol por defecto de un refresh sin rol es `OPERATOR` mientras que el de un access es `VIEWER` (inconsistencia) | `auth_service.py:136-178, 332, 362` | [L] | Usar `PyJWT`; unificar el rol por defecto al mínimo privilegio |
| S-11 | Firma de recibo por concatenación con `:` sin codificación inequívoca (campos con `:` podrían generar ambigüedad entre campos adyacentes) | `domain/decision.py:191` | [L] | Serializar con JSON canónico o prefijo de longitud |
| S-12 | `urllib.request.urlopen(...)  # nosec B310` en proveedores/LLM: sin validar esquema (`file://`) si la `base_url` es configurable | `agent_llm.py:281,376,485`, `providers/kev.py:128`, `laya.py:278` | [L] | Validar `scheme in {http, https}` en la configuración; ver P-02 (migrar a `httpx`) |
| S-13 | Los mensajes de error 401 devuelven el texto interno de la excepción (`Token JWT inválido: {e}`) | `dependencies.py:947` | [L] | Mensaje genérico al cliente; detalle sólo al log |

### 4.2 Robustez del clasificador de comandos [V]

Sondas ejecutadas contra `CommandClassifier.classify` (resultado ⇒ qué hace la política):

| Comando | Categoría | Resultado de política esperado |
|---|---|---|
| `rm -rf /`, `rm -rf ~`, `dd if=/dev/zero of=/dev/sda`, `mkfs…`, `Remove-Item -Recurse -Force C:\`, `del /s /q C:\*`, `bash -c 'rm -rf /'` | DESTRUCTIVE | BLOCK ✅ |
| `python -c "import shutil;shutil.rmtree('/')"` | DESTRUCTIVE | BLOCK ✅ |
| `sudo ls`, `chmod -R 777 /` | PRIVILEGE | BLOCK ✅ |
| `git push --force origin main`, `curl … http://e.com` | REMOTE_MUTATION / NETWORK | ABSTAIN (confirmación) ✅ |
| `r''m -rf /`, `$(echo rm) -rf /` (ofuscación) | UNKNOWN | ABSTAIN ✅ (falla seguro) |
| `find . -delete`, `git clean -fdx`, `truncate -s 0 db`, `perl -e 'unlink glob q(*)'` | UNKNOWN | ABSTAIN (revisión, no BLOCK) ⚠️ |
| `echo hi > /etc/passwd`, `mv src /dev/null` | LOCAL_MUTATION | **No verificado** si exige confirmación al estar fuera del workspace ❓ |
| `cat ~/.ssh/id_rsa`, `cat .env` | INSPECTION (read-only) | ALLOW ❌ (S-02) |

**Conclusión:** el principio *desconocido ⇒ revisión humana* funciona y cubre la ofuscación trivial. Debilidades: (a) lo destructivo no trivial (`find -delete`, `git clean`, `truncate`) depende de la revisión humana, lo que genera fatiga de aprobaciones; (b) las escrituras fuera del workspace (`> /etc/passwd`) están clasificadas como mutación local, y habría que confirmar con un test que la política las trate con riesgo alto.
**Recomendación:** reemplazar/complementar las regex por un análisis sintáctico real del shell (p. ej. `bashlex` para POSIX y el AST de PowerShell vía `System.Management.Automation.Language`), y evaluar **rutas objetivo** contra el workspace (destino fuera ⇒ riesgo alto), no solo el verbo.

### 4.3 Aspectos positivos de seguridad (a mantener)

- Capability firmado con ligadura a acción, estado, sesión, modo y expiración corta; nonce persistente (SQLite) que sobrevive a reinicios.
- Contención de rutas con resolución de symlinks en cada ancestro (`sandbox.py:182-231`) y en el editor (`workspace_files.resolve_inside`).
- Salida de procesos acotada (512 KB), timeout forzado, `shell=False` en el sandbox Windows.
- Defensa CSRF por `Origin` en cookies, `SameSite=Strict`, `HttpOnly`, CSWSH en WebSocket, tickets WS de un solo uso, clave maestra no admitida en la URL desde clientes remotos.
- Validación de perfil `production` (clave ≥32 caracteres, CORS sin comodines, docs OpenAPI desactivadas).
- Servidores MCP nuevos o modificados quedan desactivados y sin aprobar; aprobación por huella de herramientas.
- `bandit` bloqueante en CI y `pip-audit` informativo; Dependabot configurado.

---

## 5. Errores funcionales y de lógica

| ID | Sev. | Hallazgo | Evidencia | Corrección |
|---|---|---|---|---|
| **F-01** | 🟠 | **Contador `review` siempre 0.** `get_session_summary` cuenta decisiones con `"REVIEW" in status`, pero `DecisionStatus` solo define `allow/block/replan/abstain`; el estado "revisión humana" es `abstain`. El panel de resumen subestima las revisiones y no cuenta `replan` | `server/dependencies.py:533-538`, `domain/decision.py:28-33` — [L] (confirmar con un test sobre una sesión con ABSTAIN) | Contar `abstain` (y `replan`) comparando con `DecisionStatus`, no con subcadenas de texto |
| F-02 | 🟡 | Contadores por **subcadena** de texto (`"ALLOW" in str(...)`) en lugar de comparación exacta con el enum; frágil ante estados nuevos | `dependencies.py:534-537` [L] | Comparar con `DecisionStatus.X.value` |
| F-03 | 🟡 | `app = create_app()` se ejecuta **al importar** `praxeon.server.app`: efectos secundarios globales (`set_active_security_profile`) y, con `PRAXEON_PROFILE=production` mal configurado, **el simple `import` lanza `ValueError`** (rompe herramientas, tests y generación de OpenAPI) | `server/app.py:156` [L] | Fábrica diferida (`uvicorn --factory`) y no crear la app a nivel de módulo |
| F-04 | 🟡 | `create_app` calcula `prof` dos veces (líneas 67 y 84), duplicado inocuo pero indicio de código acumulado | `app.py:67,84` [L] | Eliminar duplicado |
| F-05 | 🟡 | Registro silencioso de agentes por defecto: `except Exception: pass` — si falla la creación de una plantilla, no hay rastro | `dependencies.py:182-183` [L] | Registrar con `logger.warning` |
| F-06 | 🟡 | Rutas de sesión/decisión mantienen estado paralelo en memoria (`_sessions_meta`, `_decisions`, `_session_runtimes`) y en SQLite: riesgo de divergencia tras reinicio parcial | `dependencies.py:165-170` [L] | Fuente única (SQLite) con caché de lectura |

---

## 6. Arquitectura

### 6.1 Visión general

Capas observadas:

```
 UI (React, web/)  ──  SDK TS (packages/sdk)
        │ REST / WebSocket
 server/ (FastAPI)  ─ routes → services → RuntimeApplicationService (mixins) → coordinators
        │
 runtime/  (executor, sandbox, state, events, nonces)   policy/ (engine, risk, egress)
        │                                               reasoning/ (classifier, evaluator)
 providers/ (LAYA, TypeSafe, KEV, router, resilience)   context/ (budget, cache L1/L2)
        │
 domain/  (modelos puros e inmutables)
```

El **dominio puro** (`domain/`, modelos Pydantic congelados) y la separación política/ejecutor son aciertos arquitectónicos.

### 6.2 Hallazgos

| ID | Sev. | Hallazgo | Evidencia | Recomendación |
|---|---|---|---|---|
| **A-01** | 🟠 | **Funciones monolíticas.** `_run_llm_loop` (756 líneas, 156 ramas), `_propose_action_impl` (522/109), `execute_node` (403/101), `evaluate_action` (356/89). Imposibles de testear por unidades, con alto riesgo de regresión | §2.2 [V] | Descomponer en *pipeline* de pasos con contrato común (`Step.run(ctx) -> Outcome`). Para `evaluate_action`: una *cadena de reglas* declarativa (`Rule.apply(ctx) -> Optional[Decision]`), una clase por regla, con orden explícito y tests por regla |
| **A-02** | 🟠 | **Duplicación y solapamiento de módulos.** Tres bucles de agente (`server/.../_run_llm_loop`, `live_agent/mission_runner.py::run_live_agent`, `dashboard.py::_execute_single_live_task`); dos `trajectory_controller` (`live_agent/` 208 líneas, `runtime/adaptive/` 107); dos `loop_detector` (`policy/` 97, `reasoning/` 141); `policy/risk.py` (9 líneas, *shim*) vs `reasoning/risk.py` (176); dos clientes TypeSafe (`core/typesafe_client.py` 647 y `providers/typesafe.py` 266); dos CLI (`cli.py`, `legacy_cli.py`) | tamaños medidos [V] | Elegir una implementación canónica por concepto, eliminar el resto o dejar *deprecation shims* con fecha. Empezar por los bucles de agente: es donde un bug de seguridad se corregiría en un sitio y quedaría en otro |
| **A-03** | 🟠 | **Objeto "dios" con mixins.** `RuntimeApplicationService(DecisionPipelineMixin, MissionRuntimeMixin)` instancia ~25 colaboradores en el constructor, y `dependencies.py` (1 070 líneas) mezcla inyección de dependencias, autenticación, perfil de seguridad y el propio servicio. Estado global a nivel de módulo (`_ACTIVE_SECURITY_PROFILE`, `_warned_dev_auth`, `EventBus.get_default()`) | `dependencies.py:78-253, 807-822` [L] | Composición en vez de mixins; un *composition root* (`bootstrap.py`) que construya el grafo; mover auth a `server/auth_deps.py`; suprimir globales usando `app.state` |
| **A-04** | 🟠 | **Persistencia repetida y sin migraciones.** Al menos 6 almacenes SQLite (`events`, `state`, `nonces`, `decisions`, `persistence`, `auth_revocations`) con el mismo código (`_get_connection`, `_close_conn`, `:memory:` especial, `PRAGMA journal_mode=WAL` en **cada** apertura). No hay `PRAGMA user_version` ni migraciones: cualquier cambio de esquema rompe instalaciones existentes | `nonce_store.py`, `sqlite_store.py`, `event_bus.py` [L]; sin coincidencias de `user_version` [V, grep] | Clase base `SqliteStore` con conexión por hilo, WAL una sola vez, `user_version` y una lista de migraciones ordenadas; unificar en 1–2 bases de datos |
| **A-05** | 🟡 | **Modelo de concurrencia mixto y mono-proceso.** 58 usos de `threading`/`asyncio.*`; rutas síncronas dentro de FastAPI; tickets WS, misiones en ejecución y clave de recibos por defecto (`token_hex` por proceso) viven en memoria ⇒ **no es seguro con varios workers** de Uvicorn y no hay guarda que lo impida | `auth_service.py:197-203`, `dependencies.py:165-170`, `policy/engine.py:64` [L] | Documentar y forzar `workers=1`; o externalizar estado compartido. Definir claramente qué es `async` y qué corre en *thread pool* |
| A-06 | 🟡 | **Dependencias de capa invertidas y nombres confusos.** `runtime/executor.py` importa `praxeon.web.context` (la capa de ejecución depende del paquete de herramientas web); coexisten `praxeon/web/` (herramientas de búsqueda), `web/` (frontend) y `praxeon/server/`; `domain/models.py` solo reexporta | `executor.py:37` [L] | Renombrar `praxeon/web/` → `praxeon/webtools/`; inyectar `CALL_CONTEXT` al ejecutor en lugar de importarlo |
| A-07 | 🟡 | **Dependencias del paquete.** `fastapi`, `uvicorn` y `websockets` están en las dependencias **base** y de nuevo en el extra `[server]` (redundante); `google-genai`, `networkx`, `rich` son obligatorias aunque sólo algunas rutas las usan. No hay cotas superiores. CI instala con `pip install -e` (ignorando `uv.lock`) | `pyproject.toml`, `.github/workflows/test.yml` [L] | Mover a extras lo opcional; usar `uv sync --frozen` en CI para reproducibilidad; fijar rangos compatibles |
| A-08 | 🟡 | **Artefactos y fuentes de verdad duplicadas.** Bundle JS compilado versionado (`server/static/`); `app.py` prefiere `web/dist` sobre `static/` empaquetado (puede servir un frontend obsoleto en un checkout); `openapi.json` duplicado raíz/SDK; `scratch/` versionado; el README fija a mano "1457 tests" | `app.py:122-128` [L] | Generar `static/` en el build del wheel (hook de hatch) y no versionarlo; un solo `openapi.json` generado en CI con *diff check*; badge de tests dinámico |
| A-09 | 🟡 | **Renombrado inacabado (JEV → PRAXEON).** ~48 ficheros con nomenclatura vieja (docstrings v0.2, `JEV_API_KEY`, `.jev_cache`, `JEV_SANDBOX_ACTIVE`) y carpeta del repo `jev-reasoning-navigator` | grep [V] | Terminar el renombrado; mantener alias con aviso de obsolescencia fechado (los `jev-*` "se eliminarán en v2.0" ya están anunciados) |

---

## 7. Rendimiento y optimización

| ID | Sev. | Hallazgo | Evidencia | Mejora propuesta |
|---|---|---|---|---|
| P-01 | 🟡 | **Una conexión SQLite nueva por operación** (+ `PRAGMA journal_mode=WAL` y `synchronous` cada vez) en nonces, eventos, estado y decisiones. En el camino caliente (cada paso del agente emite varios eventos y una decisión) multiplica la latencia de E/S | `nonce_store.py` `_get_connection`, `event_bus.py:37`, `sqlite_store.py:42-60` [L] | Conexión persistente por hilo (o un hilo escritor con cola), WAL configurado una vez, inserciones por lotes para eventos |
| P-02 | 🟡 | **HTTP síncrono sin reutilización de conexión**: `urllib.request.urlopen` en 3 puntos de `agent_llm.py` y en proveedores; cada llamada abre TCP/TLS nuevo | `agent_llm.py:281,376,485`, `kev.py:128`, `laya.py:278` [L] | `httpx.Client` compartido (ya es dependencia de desarrollo) con *keep-alive*, timeouts por fase y reintentos con *backoff* |
| P-03 | 🟡 | **Hash de estado repetido y O(tamaño del estado).** Por cada ejecución se serializa a JSON el *snapshot* completo hasta 3 veces (`snapshot_hash`, `canonical_hash`, hash con checkpoints previos). El estado crece con el historial ⇒ coste acumulado cuadrático por misión | `executor.py:143-158`, `domain/decision.py:252-264` [L] | Hash incremental (encadenado: `H(n)=H(n-1‖delta)`) o hash de un resumen acotado; cachear el hash mientras el estado no cambie |
| P-04 | 🟡 | **Resumen de sesión carga todos los eventos** para contar (5 recorridos de la lista + reducción a árbol) en cada petición | `dependencies.py:533-541` [L] | `SELECT type, COUNT(*) … GROUP BY` en SQL, o contadores materializados al emitir el evento |
| P-05 | ℹ️ | **Arranque de PowerShell por comando** en Windows (`-NoProfile` ya ayuda): coste fijo por llamada | `sandbox.py:348` [H] | Medir; si pesa, sesión de shell persistente por misión con delimitadores |
| P-06 | ℹ️ | Cada petición protegida compara, decodifica y valida JWT sin caché de verificación; coste pequeño | `dependencies.py:892-965` [H] | Sin acción salvo que el perfil lo exija |
| P-07 | ℹ️ | `monaco-editor` es la dependencia frontend más pesada; las vistas ya se dividen en *chunks* | `web/package.json`, `server/static/assets` [L] | Verificar que Monaco solo se carga en la vista del editor (`import()` dinámico) |

---

## 8. Calidad de código, pruebas y proceso

| ID | Sev. | Hallazgo | Detalle | Recomendación |
|---|---|---|---|---|
| Q-01 | 🟡 | **Linting mínimo y sin tipado estático.** Ruff solo activa `F, E9, B904, B033, B007, B905`. No hay `mypy`/`pyright` y hay ~1 004 `Any`. No hay formateador. (Además, `ruff` no está instalado en el `.venv` del repo, por lo que no pudo ejecutarse localmente) | `pyproject.toml` [L] | Añadir progresivamente `E, W, UP, SIM, PERF, C90, S` (con `per-file-ignores` para tests) y `mypy --strict` empezando por `domain/`, `policy/`, `runtime/`. Incluir `ruff` en el extra `dev` ya existente y `ruff format` |
| Q-02 | 🟡 | **Manejo de excepciones demasiado amplio:** 216 `except Exception`, 43 acaban en `pass`. En código de seguridad (revocaciones, registro de agentes) los fallos silenciosos esconden problemas reales | grep [V] | Prohibir `except Exception: pass` sin `logger` (regla `BLE001`/`S110`/`SIM105`); capturar excepciones concretas |
| Q-03 | 🟡 | **Cobertura:** umbral del 70 % solo en una celda de la matriz (Ubuntu/3.11). 144 funciones >60 líneas dificultan la cobertura de ramas | `test.yml` [L] | Subir gradualmente el umbral y exigir cobertura más alta (≥90 %) en `policy/`, `runtime/executor.py`, `auth_service.py`; añadir *mutation testing* (mutmut) al núcleo de políticas |
| Q-04 | 🟡 | **Falta de pruebas de adversario sobre el clasificador y el sandbox** como regresión permanente (las sondas de §4.2 deberían ser una tabla parametrizada de test) | §4.2 [V] | Crear `tests/security/test_command_corpus.py` con corpus (comando ⇒ categoría/estado esperados) y *fuzzing* (hypothesis) de ofuscaciones |
| Q-05 | ℹ️ | **Mezcla de idiomas** (docstrings y mensajes en español, identificadores en inglés) y mensajes de error al cliente con detalles internos | revisión general | Decidir idioma de la API pública (mensajes) vs interno; códigos de error estables (`error.code`) para el frontend |
| Q-06 | ℹ️ | **Documentación con cifras fijadas a mano** (tests, resultados de benchmarks). Las cifras de la cabecera (78 % vs 63 %, 21/21 vs 0/21) deben ir acompañadas de *n*, semillas e intervalos de confianza | README [L] | Generar el README desde `benchmark_results/` y publicar IC (Wilson) y tamaño muestral por brazo |
| Q-07 | ✅ | **Aspectos positivos de proceso:** CI en Ubuntu y Windows × Python 3.11/3.12, `bandit` bloqueante, `pip-audit`, Dependabot, tests de release, de seguridad, de recuperación ante caídas y de empaquetado; commits recientes eliminan resultados *hard-coded* en benchmarks | `.github/` [L] | Mantener |

---

## 9. Resultado de la suite de tests

Ejecutado `pytest -x -q -m "not laya_neural"` (Windows, `.venv` del repo): **1454 passed, 4 skipped, 3 deselected, 1 warning en 384 s**. Sin fallos. Las 3 deseleccionadas son las de `laya_neural` (descargan pesos), lo que cuadra con los 1457 del README. Único aviso: `HTTP_413_REQUEST_ENTITY_TOO_LARGE` está obsoleto en Starlette (`routes/workspace.py:213`; usar `HTTP_413_CONTENT_TOO_LARGE`). Tests más lentos: interoperabilidad MCP/SSE (17 s) y bucle interactivo del dashboard (10 s).

---

## 10. Hoja de ruta propuesta

### Fase 1 — Seguridad inmediata (1–2 semanas)
| Tarea | ID | Esfuerzo |
|---|---|---|
| Separar claves API / JWT / recibos y retirar el *fallback* a `SECRET_KEY` | S-03 | S |
| Validar cabecera `Host` y token obligatorio también en modo dev | S-05 | S |
| Categoría `SENSITIVE_READ` en el clasificador (+ tests de corpus) | S-02, Q-04 | M |
| Documentar los límites reales de `LocalProcessSandbox`; `container` como valor por defecto recomendado | S-01 | S |
| `SecureExecutor` sin clave ⇒ error; log de fallos de revocación | S-07 | S |
| Corregir el contador `REVIEW`/`abstain` y comparar con el enum | F-01, F-02 | XS |

### Fase 2 — Robustez y deuda técnica (2–6 semanas)
| Tarea | ID | Esfuerzo |
|---|---|---|
| Decidir RBAC real vs. mono-usuario y simplificar | S-04 | M |
| `SqliteStore` base + migraciones (`user_version`) | A-04, P-01 | M |
| Orden correcto de validaciones/consumo de nonce; acotar caches en memoria | S-08 | S |
| App factory (sin `create_app()` en import) y bootstrap explícito | F-03, A-03 | M |
| Ampliar ruff + mypy en el núcleo; prohibir `except: pass` | Q-01, Q-02 | M |
| Sustituir `urllib` por `httpx.Client` con pool y reintentos | P-02, S-12 | M |
| `allow_full_access` configurable + árbol de procesos terminable | S-06 | S |

### Fase 3 — Refactor estructural (6–12 semanas)
| Tarea | ID | Esfuerzo |
|---|---|---|
| Descomponer `_run_llm_loop`, `_propose_action_impl`, `execute_node`, `evaluate_action` en pipelines/reglas con tests por unidad | A-01 | L |
| Unificar los 3 bucles de agente y los módulos duplicados | A-02 | L |
| Análisis sintáctico real del shell y evaluación de rutas objetivo | §4.2 | L |
| Aislamiento fuerte (contenedor sin red / bubblewrap / AppContainer) | S-01 | L |
| Hash de estado incremental y resúmenes por SQL | P-03, P-04 | M |
| Terminar renombrado JEV → PRAXEON y limpieza de artefactos versionados | A-08, A-09 | S |

*Esfuerzo orientativo: XS < 1 h · S ≈ 1 día · M ≈ 2–5 días · L > 1 semana.*

---

## 11. Conclusión

PRAXEON demuestra un diseño de seguridad **por capas bien razonado** y un nivel de pruebas superior al habitual en proyectos de esta edad (≈5 semanas de historial). La capa de políticas, el modelo de capability firmado y la política "desconocido ⇒ humano" son su valor diferencial y están bien implementados.

Lo que debe corregirse antes de afirmar un modelo de amenazas fuerte es **alinear lo que se promete con lo que se garantiza**: el sandbox local es una mitigación, no una frontera; el RBAC es una etiqueta; y la clave maestra hace demasiados trabajos. Ninguno de los hallazgos de severidad alta requiere rediseñar el sistema: son correcciones acotadas de configuración, clasificación y documentación.

A medio plazo, la prioridad es **estructural**: las funciones de 400–750 líneas y los tres bucles de agente solapados son el principal riesgo de regresión de seguridad futura (un arreglo aplicado en un camino y no en otro). Reducir esa complejidad, unificar la persistencia y elevar el tipado estático del núcleo dará más seguridad real que añadir nuevas funcionalidades.

---

### Anexo A — Comandos utilizados (reproducibilidad)

- Métricas de funciones: análisis `ast` sobre `praxeon/**/*.py` (longitud y nº de nodos `If/For/While/Try/BoolOp/ExceptHandler`).
- Sondas de clasificador/sandbox: `CommandClassifier().classify(cmd)` y `LocalProcessSandbox._is_network_command(cmd)` (script desechable, no incluido en el repo).
- Recuentos: `grep -rn "except Exception"`, `grep -rn "sqlite3.connect"`, `grep -rn "user_version"`, `wc -l` por paquete.
- Suite de tests: `pytest -x -q -m "not laya_neural"`.

### Anexo B — Convenciones de severidad

- 🔴 **Alta:** permite eludir una garantía de seguridad declarada o exponer secretos con condiciones realistas.
- 🟠 **Media:** debilita una defensa, causa error funcional visible o genera deuda que bloquea evolución.
- 🟡 **Baja:** higiene, robustez o rendimiento con impacto acotado.
- ℹ️ **Informativa:** observación sin acción obligatoria.
