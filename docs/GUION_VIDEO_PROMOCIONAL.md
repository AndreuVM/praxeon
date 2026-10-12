# PRAXEON — Guion del vídeo promocional

**Duración objetivo:** ~2:40 (versión larga) · versión corta de ~90 s en la sección "Cortes" · **Formato:** 16:9 (1920×1080), con versión vertical 9:16 para redes
**Tono:** técnico pero claro, sin jerga sin explicar. Cada concepto se muestra antes de nombrarse.
**Idioma:** español (narración y textos en pantalla)

> **Mensaje central:** *El modelo propone. El runtime decide qué se ejecuta.*

---

## Resumen de escenas

| # | Escena | Duración | Qué demuestra |
|---|--------|----------|---------------|
| 1 | Gancho | 8 s | El problema en una frase |
| 2 | Qué pasa sin supervisión | 12 s | Tres fallos típicos de un agente |
| 3 | La idea: proponer ≠ ejecutar | 8 s | Separación propuesta / ejecución |
| 4 | El pipeline completo | 14 s | Las 8 etapas, animadas en cadena |
| 5 | Interfaz: Live Canvas | 8 s | El grafo de decisiones en vivo |
| 6 | Caso 1 — ALLOW con firma | 10 s | Acción grounded y autorizada |
| 7 | Caso 2 — Finalización prematura | 9 s | `CompletionVerifier` frena el `finish` |
| 8 | Caso 3 — Bucle y rollback | 10 s | Detección de bucle y vuelta atrás |
| 9 | Caso 4 — Path traversal bloqueado | 9 s | Capability HMAC y sandbox |
| **10** | **Workflows: canvas visual** | **9 s** | **Varios agentes como un grafo editable** |
| **11** | **Semántica de control declarativa** | **9 s** | **Condiciones tipadas, sin `eval()`** |
| **12** | **Triaje real: dos ejecuciones** | **9 s** | **Mismo flujo, dos rutas distintas** |
| **13** | **Code review loop con aprobación humana** | **11 s** | **Bucle acotado y pausa supervisada** |
| **14** | **Agentes dirigidos por directivas** | **10 s** | **System prompt, riesgo y herramientas por agente** |
| 15 | Inspector de decisiones | 8 s | Por qué se decidió cada cosa |
| 16 | Configurar una misión | 7 s | Modelo, sandbox, política de fallo |
| 17 | Ecosistema y uso | 6 s | MCP, CLI, 100 % offline |
| 18 | Cierre | 5 s | Llamada a la acción |

---

## Parte 1 — El problema y la idea (0:00–0:28)

### Escena 1 — Gancho (0:00–0:08)

**Visual:** Fondo oscuro. Un cursor parpadea en un prompt de terminal. Aparece una frase escrita letra a letra: *"Un agente de IA tiene permiso para ejecutar comandos en tu ordenador."* Una pausa. Después aparece en grande la pregunta: **¿Quién decide qué se ejecuta?**

**Narración:**
> Un agente de IA puede leer archivos, ejecutar comandos y modificar tu proyecto. Entonces, ¿quién decide realmente qué se ejecuta?

**Animación:** Escritura tipo máquina; la pregunta entra con un *scale-up* suave.

---

### Escena 2 — Qué pasa sin supervisión (0:08–0:20)

**Visual:** Tres tarjetas aparecen en fila. Cada una muestra un fallo real, con un icono de advertencia que pulsa al entrar:

1. **"Declara victoria antes de tiempo"** — el agente escribe `finish` sin haber ejecutado los tests.
2. **"Se atasca en bucle"** — la misma acción repetida 5 veces seguidas, con un contador que sube.
3. **"Sale de su carpeta"** — una ruta `../../etc/shadow` con una flecha roja que sale del recuadro del workspace.

**Narración:**
> Los modelos de lenguaje proponen acciones con probabilidad, no con certeza. Pueden darse por terminados sin verificar nada, quedarse dando vueltas o intentar leer lo que no deberían.

**Texto en pantalla:** *Una instrucción en el prompt no es una barrera de seguridad.*

---

### Escena 3 — La idea central (0:20–0:28)

**Visual:** Dos bloques. A la izquierda, **Agente** (un icono de robot). A la derecha, **Sistema**. Entre ambos aparece una puerta con la etiqueta **PRAXEON**. El robot lanza una burbuja de texto *"Propongo: ejecutar `read_file`"*. La puerta la examina; solo cuando recibe el sello verde pasa la acción al sistema.

**Narración:**
> PRAXEON separa dos cosas: lo que el modelo propone, y lo que el sistema ejecuta. Ninguna acción llega al sistema sin pasar antes por el supervisor.

**Texto en pantalla:** *El modelo propone. El runtime decide.*

**Animación:** La burbuja se disuelve al tocar la puerta; el sello verde aparece con un *stamp*.

---

## Parte 2 — Supervisar cada acción (0:28–1:28)

### Escena 4 — El pipeline completo (0:28–0:42)

**Visual:** Ocho nodos en una línea horizontal, cada uno con su etiqueta. Se iluminan uno a uno, de izquierda a derecha, con un trazo que los une:

1. **Evidencia** — ¿existen las precondiciones?
2. **Riesgo** — ¿el comando es de solo lectura, reversible, o destructivo?
3. **Evaluación semántica** — ¿tiene sentido la acción para el objetivo?
4. **Verificación de finalización** — ¿se cumplen los criterios de éxito?
5. **Política** — decisión vinculante: `ALLOW`, `BLOCK`, `REPLAN` o `ABSTAIN`.
6. **Recibo HMAC** — capability firmado, con caducidad.
7. **Checkpoint** — copia del estado antes de modificar nada.
8. **Sandbox** — ejecución aislada, con variables de entorno depuradas.

Al final, el nodo 8 emite una flecha de salida: **Observación**.

**Narración:**
> Cada propuesta pasa por ocho etapas. Se comprueban las pruebas, se mide el riesgo y se evalúa el sentido de la acción. Solo entonces la política decide, y la ejecución ocurre dentro de un sandbox.

**Animación:** Cada nodo entra con un *fade + slide* de 0,4 s, con un desfase de 0,3 s entre nodos. El trazo se dibuja con un *stroke-dashoffset* continuo.

---

### Escena 5 — La interfaz: Live Canvas (0:42–0:50)

**Visual:** Captura real de la vista **Live Canvas** de la web. Un árbol de nodos crece desde el nodo *Start*. Cada nodo tiene color según su decisión (verde = ALLOW, ámbar = REPLAN, rojo = BLOCK). El zoom hace un *push-in* suave hacia el último nodo añadido.

**Narración:**
> Todo lo que el agente hace aparece en un grafo en vivo. Cada decisión queda registrada.

**Texto en pantalla:** *Live Canvas · trazabilidad en tiempo real*

**Nota de producción:** usar una sesión real, no el "Demo Visual" precargado, para que el árbol sea auténtico.

---

### Escena 6 — Caso 1: ALLOW con firma (0:50–1:00)

**Visual:** Terminal o panel con la salida real de la demo:

```
Objetivo: Inspeccionar código, verificar tests y finalizar la tarea
Escenario 1 — INSPECCIÓN SEGURA Y FUNDAMENTADA
Decisión: ALLOW (GROUNDED_LOW_RISK_AUTHORIZED)
Firma HMAC del recibo: ade83b08… (HMAC-SHA256 verificado)
Observación: def run(): return 42
```

Un sello verde **ALLOW** aparece junto a la línea de decisión. Se resalta la línea de firma con un halo.

**Narración:**
> El agente quiere leer `src/main.py`. Hay evidencia de que el archivo existe y el riesgo es bajo. La política lo autoriza y emite un recibo firmado. Solo con ese recibo se ejecuta la lectura.

**Texto en pantalla:** *Acción autorizada · recibo firmado con HMAC-SHA256*

---

### Escena 7 — Caso 2: finalización prematura (1:00–1:09)

**Visual:** El agente escribe `finish` en un bloque de código. Un cuadro de criterios aparece a la derecha con dos filas: **"tests pasando" — ✗ sin evidencia**, **"código verificado" — ✓**. Un sello ámbar **REPLAN** cae sobre el `finish`.

**Narración:**
> El agente intenta darse por terminado. El verificador revisa los criterios de éxito: los tests no se han ejecutado. La finalización se bloquea y el agente tiene que conseguir la prueba antes de continuar.

**Texto en pantalla:** *Finalización bloqueada hasta que haya pruebas*

**Animación:** La fila "tests pasando" parpadea en ámbar; el `finish` se tacha con una línea que se dibuja.

---

### Escena 8 — Caso 3: bucle y rollback (1:09–1:19)

**Visual:** Una misma línea de acción se repite en filas apiladas. Un medidor de **probabilidad de bucle** sube hasta **88 %**, con la marca del umbral en 65 %. Al cruzarla, la pila de filas se desvanece y una flecha curva retrocede hasta un nodo marcado **checkpoint**. La rama degenerada aparece tachada en gris.

**Narración:**
> Si el agente se atasca repitiendo la misma acción, el supervisor lo detecta. Restaura el estado al último checkpoint seguro, descarta la rama que daba vueltas y continúa por otro camino.

**Texto en pantalla:** *Bucle detectado (88 %) → rollback a checkpoint*

**Animación:** El medidor se llena con *ease-out*; el retroceso del grafo usa una interpolación suave de posición, no un salto.

---

### Escena 9 — Caso 4: path traversal bloqueado (1:19–1:28)

**Visual:** Una ruta `../../etc/shadow` intenta salir de un recuadro etiquetado **workspace**. Antes de que cruce el borde, aparece un candado. Dos hashes se muestran en paralelo: **hash de la acción** `f598…` y **hash del capability** `086f…`. No coinciden; el cuadro se vuelve rojo.

**Narración:**
> Un atacante, o un error del modelo, intenta leer fuera del workspace. Cada acción va firmada y vinculada a su contenido. Al no coincidir la firma, la ejecución se deniega antes de tocar el sistema.

**Texto en pantalla:** *Ejecución denegada · la firma no coincide con la acción*

**Animación:** Los dos hashes se acercan y chocan con una sacudida corta al no coincidir.

---

## Parte 3 — Orquestar varios agentes (1:28–2:16)

> **Qué hay detrás:** un workflow es un grafo de nodos tipados (`START`, `END`, `TASK`, `AGENT`, `DECISION`/`IF`, `WHILE`, `DELEGATE`, `HUMAN_APPROVAL`, `PARALLEL_FORK`, `PARALLEL_JOIN`). Las plantillas canónicas (Triage Router, Code Review Loop, Research → Writer → Reviewer) están verificadas: el motor las ejecuta de principio a fin.

### Escena 10 — Workflows: canvas visual (1:28–1:37)

**Visual:** Captura real de **Workflows → Visual DAG Orchestrator**, con la plantilla **Triage Router** cargada desde *Plantillas Canónicas*. El canvas muestra nodos con colores por tipo y aristas con etiqueta. Un cursor arrastra un nodo y el trazo se recoloca con suavidad. Abajo, los botones **Validar DAG**, **Step** y **Ejecutar**.

**Narración:**
> Diseñas los flujos de tus agentes como un grafo: arrastras nodos, conectas aristas y validas el diagrama antes de ejecutarlo. Si hay un ciclo sin control, el sistema lo detecta.

**Texto en pantalla:** *Workflows · diseña, valida, ejecuta*

**Nota de producción:** la validación de ciclos está en `validate_graph()`. Mostrar el botón **Validar DAG** en acción antes de afirmarlo en la voz en off.

---

### Escena 11 — Semántica de control declarativa (1:37–1:46)

**Visual:** Un bloque de código con la condición de un nodo `IF`, escrita como datos y no como código. Primero aparece la condición sobre el juicio semántico del sistema, que se resalta con un halo:

```json
{ "all": [
    { "field": "semantic_assessment.risk", "op": "==", "value": "low" },
    { "field": "semantic_assessment.confidence", "op": ">=", "value": 0.75 }
] }
```

Debajo, la lista de namespaces que puede leer la condición (`variables`, `outputs`, `loop`, `budget`, `agent_state`, `semantic_assessment`), cada uno con un destello al resaltarse. Al final, una línea tachada: `eval()` ✗.

**Narración:**
> Las decisiones del flujo se escriben como condiciones declarativas, con AND, OR y NOT. Pueden basarse en el juicio semántico del sistema: si el juicio es de bajo riesgo y tiene suficiente confianza, el flujo continúa; si no, se detiene. No se ejecuta código arbitrario: ninguna condición puede tocar el sistema.

**Texto en pantalla:** *Condiciones como datos · decididas por el juicio semántico · sin `eval()`*

**Animación:** Los namespaces aparecen en cascada desde la condición; el halo sobre `semantic_assessment` pulsa al aparecer; la línea `eval()` se tacha con un trazo rojo.

**Requisito de implementación:** ver la lista de "Requisitos antes de grabar" al final. Esta escena no se graba hasta que el juicio semántico escriba en `semantic_assessment` antes de evaluar el `IF`.

---

### Escena 12 — Triaje real: dos ejecuciones (1:46–1:55)

**Visual:** Split en pantalla. Izquierda: `complexity = "high"`. Derecha: `complexity = "low"`. El mismo grafo, dos recorridos:

- **High:** el nodo *Specialized Deep Agent* se ilumina en verde; *Fast Lightweight Agent* se marca **SKIPPED** en gris.
- **Low:** al revés.

Ambos llegan a *Ticket Resuelto* con estado **COMPLETED**.

**Narración:**
> Un mismo flujo, dos caminos. El sistema evalúa cada ticket y decide su complejidad: si es alta, va al agente especializado; si es baja, al ligero. El camino que no se toma queda registrado como omitido.

**Texto en pantalla:** *Mismo flujo · dos rutas · cada decisión trazada*

**Animación:** Los nodos se iluminan en secuencia; el camino no tomado se atenúa con un *fade* a gris. Antes de la bifurcación aparece un pequeño cuadro "juicio semántico: complejidad alta, confianza 0,91" que alimenta el nodo *Triage Router*.

**Referencia de comportamiento esperado:** en el motor actual, el ejemplo se verificó asignando `complexity` a mano como variable inicial (`high` → heavy ejecutado y light SKIPPED; `low` → al revés; ambos `COMPLETED`). En la versión final la complejidad la asigna el juicio semántico, no el operador. Grabar solo cuando esté conectado.

---

### Escena 13 — Code review loop con aprobación humana (1:55–2:06)

**Visual:** Captura del grafo *Code Review Loop (Developer ↔ Reviewer)*. Una flecha de vuelta une *Reviewer* con *Developer* a través de un nodo **WHILE**, con una etiqueta de contador **3 / 3 iteraciones máx.**. Al salir del bucle, el flujo llega a un nodo **HUMAN_APPROVAL** que se ilumina en ámbar con el texto **WAITING_APPROVAL**, y un botón **Aprobar** pulsa. El nodo final *Finalizado* se ilumina.

**Narración:**
> El desarrollador propone un cambio y el revisor lo evalúa. El bucle tiene un límite fijo de iteraciones, nunca gira sin fin. Y antes de desplegar, el flujo se detiene hasta que una persona lo aprueba.

**Texto en pantalla:** *Bucle acotado · aprobación humana antes de desplegar*

**Animación:** El contador del bucle avanza 1, 2, 3; el nodo de aprobación pulsa en ámbar hasta el clic.

**Resultado real verificado:** `CODE_REVIEW` pausa en `human_gate` con estado `WAITING_APPROVAL`, con `end` aún `PENDING`. Para el vídeo hay que grabar también la aprobación (`approve_node`) y el paso a `COMPLETED`.

---

### Escena 14 — Agentes dirigidos por directivas (2:06–2:16)

**Visual:** Captura de **Agents**, con un agente seleccionado (plantilla *Code Reviewer*). Tres paneles se abren en secuencia:

1. **Motor de inferencia:** proveedor, modelo, temperatura (la temperatura del CodeReviewer baja a 0,0).
2. **Perfil de riesgo y HITL:** nivel máximo de riesgo, y cuándo se pide confirmación humana.
3. **Herramientas autorizadas:** lista corta de herramientas permitidas, con las demás en gris.

Un texto aparece sobre el panel de herramientas: *mínimo privilegio*.

Después, un nodo **DELEGATE** en modo **AUTOMATIC** recibe una tarea. Tres candidatos aparecen con su puntuación de capacidades (por ejemplo: *Developer 0,88 · CodeReviewer 0,41 · Researcher 0,12*). El nodo elige el de mayor puntuación y la flecha se dirige a él con una animación de *snap*.

**Narración:**
> Cada agente tiene su propia directiva: qué modelo usa, qué herramientas puede tocar y hasta qué nivel de riesgo puede actuar sin preguntar. Cuando una tarea se delega, el sistema compara las capacidades de los candidatos y elige el más adecuado.

**Texto en pantalla:** *Cada agente, con su mínimo privilegio · delegación por capacidades*

**Requisito de implementación:** la selección `AUTOMATIC` por puntuación de capacidades no existe todavía; hoy toma el primer candidato. Ver "Requisitos antes de grabar". Las puntuaciones de la animación son ilustrativas: deben salir del cálculo real, no fijarse a mano.

---

## Parte 4 — Control y ecosistema (2:16–2:42)

### Escena 15 — Inspector de decisiones (2:16–2:24)

**Visual:** Captura real del **Decision Inspector**. Al seleccionar un nodo, se despliegan en columnas: **Semantic Evaluation**, **Policy Decision**, **Capability** y **Execution Environment** (aislamiento, directorio de trabajo, postura de red). Un cursor recorre las secciones en orden.

**Narración:**
> Al seleccionar cualquier decisión, ves por qué se tomó: la evaluación semántica, la regla de política aplicada y el entorno de ejecución.

**Texto en pantalla:** *Cada decisión, explicable.*

---

### Escena 16 — Configurar una misión (2:24–2:31)

**Visual:** Captura real de **Mission Launcher**. El cursor selecciona en secuencia, con un resaltado suave:

- **Generador LLM:** Ollama local, API remota, o simulador offline.
- **Entorno:** *Local Restricted Sandbox* (por defecto) / *Container Sandbox (Docker)*.
- **Política ante fallo del LLM:** *Fail-Closed* (detener la misión).

Un badge **⚠ Full Access** aparece atenuado, con su casilla de confirmación explícita.

**Narración:**
> Eliges el modelo, el entorno de ejecución y qué pasa si el proveedor falla. Por defecto, si algo falla, la misión se detiene. El acceso total al equipo exige una confirmación expresa.

**Animación:** Los cuadros se activan uno a uno; el badge de Full Access se muestra en gris con un tooltip de aviso.

---

### Escena 17 — Ecosistema y uso (2:31–2:37)

**Visual:** Tres logos genéricos (no de marca) en fila: "Cliente MCP", "Terminal", "Panel web". Debajo, un bloque de comando que se escribe solo:

```bash
praxeon live "Refactorizar función en parser.py" --provider ollama
```

Un contador lateral marca **Coste API: 0,00 USD** en la versión offline.

**Narración:**
> Funciona como servidor MCP para los IDEs y clientes compatibles, desde la terminal o desde el panel web. Puede correr 100 % en local, sin claves de API ni conexión externa.

**Nota de producción:** verificar antes de publicar qué clientes MCP están probados de verdad; mencionar solo esos.

---

### Escena 18 — Cierre (2:37–2:42)

**Visual:** Fondo limpio. Aparece el nombre **PRAXEON** en grande, con el subtítulo *Runtime supervision for autonomous AI agents*. Debajo, la frase central se escribe de nuevo: *El modelo propone. El runtime decide.* Por último, la URL del repositorio y `pip install -e ".[dev]"`.

**Narración:**
> PRAXEON. El modelo propone. El runtime decide.

**Texto en pantalla:** URL del repositorio · *Demo offline: `python demo.py`*

---

## Cortes para la versión de ~90 s

Si hace falta una versión corta: mantener 1, 3, 4, 6, 8, 10, 12, 14 y 18. Eliminar 2, 5, 7, 9, 11, 13, 15, 16 y 17. El mensaje de orquestación (escena 12) se conserva porque es el que diferencia el producto.

---

## Notas de producción

- **Capturas reales:** las escenas 5, 10, 13, 14, 15 y 16 usan la web real. Conviene grabar una sesión propia, no el "Demo Visual" precargado.
- **Salidas de terminal:** las escenas 6–9 usan la salida real de `examples/demo_offline.py`. Si cambia el código, regenerarlas antes de grabar.
- **Funcionalidad objetivo:** el guion describe el comportamiento final del motor de workflows. Las escenas 11, 12 y 14 dependen de funcionalidad que aún no está conectada. No grabarlas hasta cumplir la lista de abajo.
- **Plantillas de workflow:** las escenas 12 y 13 se verificaron con las plantillas `create_triage_router_template()` y `create_code_review_loop_template()` ejecutadas en el motor actual. Esa verificación valida la estructura, no la versión final: repetirla tras la conexión.

### Requisitos antes de grabar

Estos puntos salen de revisar el código actual. Cada uno es una condición para grabar la escena indicada:

1. **Juicio semántico conectado** (escenas 11 y 12). Hoy el motor lee `variables._semantic_assessment` / el namespace `semantic_assessment`, pero nada en `praxeon/` lo escribe. Debe escribirlo LAYA/TypeSafe antes de evaluar cada `IF`/`DECISION`, con los campos que use la condición (`risk`, `confidence`).
2. **DELEGATE AUTOMATIC por capacidades** (escena 14). Hoy elige `candidate_agents[0]`. Debe puntuar cada candidato con sus capacidades y elegir el mayor. Las puntuaciones de la animación deben salir de ese cálculo.
3. **Aprobación humana de extremo a extremo** (escena 13). El nodo `HUMAN_APPROVAL` pausa en `WAITING_APPROVAL`; falta grabar y comprobar el paso desde la interfaz a `approve_node` y a `COMPLETED`.
4. **Ejecución desde la web** (escenas 10 y 13). Confirmar que el botón **Ejecutar** del canvas lanza el mismo motor que se ha probado.
5. **Trazas de decisión por nodo** (escenas 12 y 15). El Decision Inspector debe mostrar la decisión de cada nodo de workflow, no solo de las acciones sueltas.

Cuando estos cinco puntos estén cerrados, sustituir las referencias "verificado" de las escenas 12 y 13 por una nueva ejecución del motor final.
- **Versión:** el README indica v1.1.0, pero `pyproject.toml` y la interfaz muestran v1.0.0. Unificar antes de publicar.
- **Afirmaciones a evitar** salvo que se verifiquen: cifras de latencia y número de tests del README, y el porcentaje de eficacia del sandbox.
- **Versión vertical (9:16):** en las escenas 4, 5 y 10 el canvas pasa a vertical, con los nodos apilados.
- **Subtítulos:** incluir siempre los textos de pantalla como subtítulos; el vídeo debe entenderse en silencio.
