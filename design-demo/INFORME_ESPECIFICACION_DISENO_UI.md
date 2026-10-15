# PRAXEON · Especificación de requisitos de diseño de la interfaz

**Versión del documento:** 1.2
**Fecha:** 14 de octubre de 2026
**Estado:** Propuesta para aprobación (decisiones de dirección incorporadas)
**Dirección visual:** «Sereno, conversacional»
**Prototipo de referencia:** `design-demo/6-sereno-chat.html` (con `shared.css` y `shared.js`)
**Producto afectado:** PRAXEON 1.1.0, interfaz web (`web/`)

**Cambios de la versión 1.2:** se añade la sección 15 con el estado de la implementación en `web/` (rama `redesign/sereno`) y las diferencias respecto a este documento.

**Cambios de la versión 1.1:** el selector de workspace pasa al riel de navegación; la aprobación humana se muestra como ventana emergente sobre el compositor; las misiones muy largas se aplazan; todas las pantallas que el prototipo no cubre deben adaptarse al nuevo estilo conservando el 100 % de su funcionalidad (nueva sección 6.10).

---

## 1. Objeto y alcance

Este documento define los requisitos de diseño de la nueva interfaz de PRAXEON: paleta de color, tipografía, estructura de página, paneles, componentes, estados, interacción, accesibilidad y adaptación a pantallas. Sirve de referencia única para el rediseño visual y de criterio de aceptación para su implementación.

**Dentro del alcance:** la vista principal (Live, renombrada «Conversación»), la navegación global, el cajón de detalle, la paleta de comandos, el estilo común de las vistas secundarias (Sesiones, Decisiones, Agentes, Flujos, Proveedores, Herramientas MCP, Seguridad, Ajustes) y los modales existentes.

**Fuera del alcance:** cambios en el backend, en el protocolo de eventos, en las políticas de seguridad o en la lógica del runtime. El rediseño es de presentación y de organización de la información. El contenido y los datos que se muestran no cambian.

**Aviso sobre el prototipo.** El prototipo usa datos simulados y un único recorrido de ejemplo. Los valores de este informe (colores, medidas, tiempos) están tomados de él. Donde el prototipo no cubre un caso (modales, errores, estados vacíos), el informe fija el requisito y indica que debe resolverse con el mismo sistema.

---

## 2. Situación de partida

El análisis de la interfaz actual, realizado sobre el código de `web/src` y sobre capturas de la aplicación, identifica estos problemas:

| Nº | Problema | Evidencia |
|---|---|---|
| P1 | Demasiada información compitiendo a la vez en la vista principal | Cabecera, 9 pestañas, KPIs, árbol, consola e inspector con 5 pestañas, todo visible a la vez |
| P2 | La conversación con el agente queda relegada a una pestaña del inspector | El chat es una de las cinco pestañas del panel derecho, junto a Decisión, Evidencia, Política y Recibo |
| P3 | Navegación recargada | Pestañas con insignias («Visual», «Hardened», contadores) y brillo cian en la activa |
| P4 | Sistema visual disperso | Unas 1.300 líneas con estilos en línea y 164 colores hexadecimales distintos en los componentes, aunque existe `index.css` con tokens |
| P5 | Tipografía diminuta y fragmentada | Tamaños de 9 a 18 px, con 12 variantes entre 9 y 12,5 px |
| P6 | El color se usa como decoración | Cian, verde, ámbar, rojo y morado conviven con brillos y sombras |
| P7 | Idioma mezclado | «Live Canvas», «Sessions», «Tools (MCP)» junto a «Decisión», «Evidencia», «Recibo» |

---

## 3. Principios de diseño

1. **La conversación manda.** La interacción principal con el agente es un diálogo. Todo lo demás es consulta bajo demanda.
2. **Divulgación progresiva.** Se muestra primero lo que necesita una decisión; el detalle técnico (decisión, evidencia, política, recibo) está a un clic, nunca en pantalla por defecto.
3. **Un solo acento.** El color señala estado o acción principal. Si todo destaca, nada destaca.
4. **Silencio visual.** Sin brillos, sin sombras decorativas, sin insignias permanentes. Bordes de 1 px y mucho espacio.
5. **Una pantalla, una tarea.** Un único flujo central y un único panel auxiliar a la vez.
6. **Seguridad siempre visible cuando es excepción.** Lo normal es discreto; lo anómalo (Full Access, modo degradado, aprobación pendiente) se hace visible de inmediato.
7. **Coherente por construcción.** Todo valor visual sale de un token. No se admiten colores, tamaños ni radios sueltos.

---

## 4. Sistema visual

### 4.1 Paleta de color

La interfaz es de tema oscuro único. Los neutros llevan un ligero sesgo azul hacia el acento para que no resulten grises planos.

#### 4.1.1 Tokens base

| Token | Valor | Rol | Uso |
|---|---|---|---|
| `--bg` | `#0e1013` | Fondo de la aplicación | Fondo general, cajón, texto sobre el acento |
| `--surface` | `#15181c` | Superficie elevada | Tarjetas, compositor, burbuja del usuario, paleta, tarjeta de aprobación |
| `--line` | `#22262c` | Líneas y bordes | Divisores, bordes de 1 px, pistas de interruptor |
| `--text` | `#e4e6e9` | Texto principal | Contenido, títulos, valores |
| `--muted` | `#80888f` | Texto atenuado | Etiquetas, metadatos, texto secundario, iconos inactivos |
| `--accent` | `#7aa2f7` | Acento | Acción principal, selección, paso en curso, foco |
| `--on-accent` | `#0e1013` | Texto sobre el acento | Etiqueta del botón principal |
| `--ok` | `#6fbf8b` | Estado correcto | Paso permitido, riesgo bajo, «Aprobado» |
| `--bad` | `#e07a76` | Estado de bloqueo o error | Paso bloqueado, riesgo alto, advertencias de seguridad |

#### 4.1.2 Valores derivados

| Valor | Uso |
|---|---|
| `#20242a` | Fondo de la opción seleccionada en la paleta de comandos |
| `rgba(8, 9, 11, 0.6)` | Velo detrás de la paleta de comandos |
| `rgba(0, 0, 0, 0.4)` con desplazamiento lateral de 16 px y desenfoque de 40 px | Única sombra permitida: cajón superpuesto en pantallas estrechas |
| `rgba(0, 0, 0, 0.55)` con desenfoque de 60 px | Única sombra permitida: paleta de comandos |

#### 4.1.3 Reglas de uso del color

- **RD-C1.** Verde (`--ok`) y rojo (`--bad`) significan solo estado. No se usan como decoración ni como acento.
- **RD-C2.** El acento (`--accent`) aparece en una sola acción principal por pantalla (botón «Enviar» o «Aprobar»), en la selección activa y en el paso en curso.
- **RD-C3.** No se introducen más colores semánticos. El ámbar de aviso y el morado del diseño actual se retiran. Si se necesita un tercer nivel de severidad, se resuelve con texto y forma (icono o etiqueta), no con un color nuevo.
- **RD-C4.** Se prohíben los degradados, los brillos (`box-shadow` de color) y las sombras de relieve.
- **RD-C5.** El estado nunca se comunica solo con color. Siempre va acompañado de texto («Bloqueado por política»), de forma (borde discontinuo en los nodos podados) o de ambos.

#### 4.1.4 Contraste verificado

Relaciones calculadas según WCAG 2.1 con los valores de la tabla 4.1.1:

| Combinación | Relación | Resultado |
|---|---|---|
| Texto sobre fondo | 15,2 : 1 | Cumple AAA |
| Texto sobre superficie | 14,2 : 1 | Cumple AAA |
| Atenuado sobre fondo | 5,3 : 1 | Cumple AA |
| Atenuado sobre superficie | 5,0 : 1 | Cumple AA |
| Acento sobre fondo | 7,6 : 1 | Cumple AAA |
| Acento sobre superficie | 7,1 : 1 | Cumple AAA |
| Verde sobre fondo / superficie | 8,6 : 1 / 8,1 : 1 | Cumple AAA |
| Rojo sobre fondo / superficie | 6,6 : 1 / 6,1 : 1 | Cumple AA |
| Texto sobre acento (botón principal) | 7,6 : 1 | Cumple AAA |
| Línea sobre fondo | 1,25 : 1 | Solo decorativa; no transmite información por sí sola |

**RD-C6.** El texto atenuado sobre `#20242a` da 4,33 : 1, por debajo de 4,5 : 1. En la opción seleccionada de la paleta se debe usar `--text`, no `--muted`.

### 4.2 Tipografía

| Rol | Familia | Pesos | Alternativas |
|---|---|---|---|
| Interfaz y contenido | Geist | 400, 500, 600 | `system-ui`, `sans-serif` |
| Código, identificadores, hashes, cifras | Geist Mono | 400, 500 | `ui-monospace`, `monospace` |

**Escala tipográfica**

| Nivel | Tamaño | Interlineado | Uso |
|---|---|---|---|
| Título de vista | 20 px, peso 600 | 1,3 | Encabezado de cada vista secundaria |
| Mensaje de la IA y del usuario, compositor | 14,5 px | 1,65 (IA) / 1,5 | Contenido de la conversación |
| Texto de interfaz | 13 px | 1,5 | Tarjetas, tablas, paneles, botones |
| Texto secundario | 12,5 px | 1,5 | Resumen de misión, selectores del compositor, ayudas |
| Código y metadatos | 11,5 px (mono) | 1,4 | Comandos, eventos, herramienta |
| Etiqueta de columna / grupo | 11 px, mayúsculas, espaciado de 0,06 a 0,07 em | 1,4 | Cabeceras de tabla, grupos de la paleta |
| Texto interno del árbol | 10,5 px y 9,5 px (secundario) | n/a | Etiquetas de nodos (se escalan con el lienzo) |

**RD-T1.** Ningún texto de lectura baja de 11 px. Los tamaños de 9 y 10 px del diseño actual quedan eliminados salvo dentro del lienzo del árbol, que se escala.
**RD-T2.** Las cifras alineadas en columnas usan `font-variant-numeric: tabular-nums` o la fuente mono.
**RD-T3.** Los títulos usan `text-wrap: balance`. Los párrafos de la IA se limitan a 64 caracteres de ancho.

### 4.3 Espaciado, forma y elevación

| Elemento | Valor |
|---|---|
| Unidad base de espaciado | 4 px; los valores habituales son 8, 12, 16, 18, 24 |
| Radio estándar (tarjetas de paso, botones, bloques de código) | 9 px |
| Radio de burbuja y tarjetas de aprobación | 11 a 16 px |
| Radio del compositor | 18 px |
| Radio de chips y sugerencias | 99 px (píldora) |
| Borde | Siempre 1 px sólido `--line`. En foco o selección, `--muted` o `--accent` |
| Elevación | Ninguna en reposo. Solo velo y sombra en elementos superpuestos (ver 4.1.2) |

### 4.4 Iconografía

- Iconos de trazo (outline) de 16 px, trazo de 1,6 px, extremos y uniones redondeados, color `currentColor`.
- Conjunto necesario: Conversación (pulso), Sesiones (carpeta), Decisiones (ramificación), Agentes (robot), Flujos (bloques), Proveedores (chip), Herramientas MCP (enchufe), Seguridad (escudo), Ajustes (engranaje), Buscar, Cerrar, Menú, Desplegar.
- Los iconos nunca llevan color propio: heredan el del texto (`--muted` en reposo, `--text` activo).

### 4.5 Movimiento

| Efecto | Duración | Uso |
|---|---|---|
| Aparición de mensaje o tarjeta (desplazamiento vertical de 6 px y fundido) | 300 ms | Cada elemento nuevo del hilo, una sola vez |
| Apertura del cajón (anchura de 0 a 390 px) | 220 ms | Abrir y cerrar el cajón |
| Latido del punto de estado en curso | 1,4 s, en bucle | Paso en ejecución y misión activa |
| Indicador de «escribiendo» (tres puntos) | 1,2 s, en bucle | Respuesta de la IA en preparación |
| Cambio de borde en hover | 150 ms | Nodos del árbol |

**RD-M1.** Se respeta `prefers-reduced-motion`: con esa preferencia se desactivan todas las animaciones y transiciones.
**RD-M2.** No se admiten animaciones decorativas ni efectos de brillo.

---

## 5. Estructura de la página

### 5.1 Disposición general

La aplicación ocupa toda la ventana (sin scroll global) y se compone de tres zonas:

```
┌────┬─────────────────────────────────────────────┬────────────┐
│    │ CABECERA  (46 px)                           │            │
│ R  ├─────────────────────────────────────────────┤  CAJÓN     │
│ I  │                                             │  (390 px,  │
│ E  │   ÁREA CENTRAL                              │  opcional) │
│ L  │   Hilo de conversación (máx. 720 px)        │            │
│    │                                             │  Mapa      │
│ 52 │                                             │  Paso      │
│ px │   ┌───────────────────────────────────┐     │  Eventos   │
│    │   │ COMPOSITOR                        │     │            │
│    │   └───────────────────────────────────┘     │            │
└────┴─────────────────────────────────────────────┴────────────┘
```

| Zona | Dimensión | Comportamiento |
|---|---|---|
| Riel de navegación | 52 px de ancho fijo | Siempre visible en escritorio. Contiene el selector de workspace y todas las vistas |
| Cabecera | 46 px de alto | Siempre visible |
| Área central | Resto del espacio | Contiene el hilo y el compositor, o una vista secundaria |
| Cajón de detalle | 390 px de ancho, oculto por defecto | Se abre a demanda y empuja al área central |

**RD-E1.** Solo hay un cajón y solo un contenido auxiliar visible a la vez. No se permiten paneles apilados ni múltiples cajones.
**RD-E2.** El área central nunca se reduce por debajo de 480 px útiles; si el cajón no cabe, se superpone en vez de empujar (ver 9).

### 5.2 Mapa de vistas

| Vista | Acceso | Contenido |
|---|---|---|
| Conversación | Riel (primer icono) | Hilo con la IA, tarjetas de paso, aprobaciones, compositor |
| Sesiones | Riel | Tabla de sesiones |
| Decisiones | Riel | Tabla de pasos de la sesión activa |
| Agentes | Riel | Tarjetas de agentes |
| Flujos | Riel | Cadena de agentes del flujo |
| Proveedores | Riel | Tabla de proveedores y modelos |
| Herramientas (MCP) | Riel | Tabla de servidores MCP |
| Seguridad | Riel | Lista de políticas con interruptores |
| Ajustes | Riel (parte inferior) | Tabla de valores por defecto |

El acceso a todas las vistas es directo desde el riel. La paleta de comandos (`Ctrl K`) es un atajo adicional, no la única vía.

---

## 6. Paneles y componentes

### 6.1 Riel de navegación

- Columna de 52 px con borde derecho de 1 px. Botones de 36 × 36 px, radio 9 px, centrados, separados 4 px.
- **Orden, de arriba abajo:** Workspace, un separador de 22 px, Conversación, Sesiones, Decisiones, Agentes, Flujos, Proveedores, Herramientas (MCP), Seguridad. Un espaciador flexible. Buscar. Ajustes.
- **Selector de workspace:** primer botón del riel, con icono de carpeta abierta. Su etiqueta indica el workspace activo («Workspace: praxeon»). Al pulsarlo se abre un panel flotante anclado a la derecha del riel (300 px, fondo `--surface`, borde, radio 14 px) con el título «Workspace del proyecto» y la lista de workspaces recientes (nombre y ruta en mono). El actual se marca con «· actual». Elegir uno cierra el panel y actualiza la etiqueta. `Esc` y pulsar fuera lo cierran.
- **Evolución prevista:** el panel crecerá para guardar proyectos y explorar sus archivos de código. Hasta entonces solo ofrece el cambio de workspace, con una nota que lo indica. El diseño del riel debe dejar sitio para añadir en ese punto, sin reorganizar la navegación, un acceso de explorador de archivos.
- **Funcionalidad que se conserva del selector actual:** exploración de carpetas del servidor, lista de recientes, acción de olvidar un workspace reciente y aviso cuando la carpeta de la sesión activa difiere del workspace elegido (se muestra como punto de aviso en `--bad` sobre el icono, con el detalle en el panel).
- **Estados:** reposo (icono `--muted`), hover (fondo `--surface`, icono `--text`), activo (igual que hover, de forma permanente).
- **Etiqueta:** al pasar el cursor o recibir foco aparece a la derecha una etiqueta con el nombre de la vista (fondo `--surface`, borde, 12 px). No hay etiquetas permanentes.
- **Requisitos:** sin insignias ni contadores. Cada botón lleva `aria-label`. Si la altura no alcanza, el riel hace scroll vertical.

### 6.2 Cabecera

Barra de 46 px con borde inferior. Contenido, de izquierda a derecha:

1. Indicador de estado de la sesión (punto de 7 px): verde si está inactiva o completada, azul con latido si hay un paso en curso.
2. Objetivo de la misión, peso 500, una línea con elipsis.
3. Identificador de sesión en mono, atenuado.
4. Espaciador.
5. Etiqueta «Aprobación pendiente» (solo cuando la hay; contorno y punto en `--accent`).
6. Botón «Mapa» (abre el cajón en la pestaña Mapa).
7. Botón de búsqueda con icono y atajo `Ctrl K`.

**RD-H1 (seguridad).** El modo de ejecución normal (local restringido) no se muestra en la cabecera; se consulta en el selector del compositor. Cuando el modo es **Full Access (manual o autónomo)**, la cabecera debe mostrar de forma permanente una etiqueta en `--bad` («FULL ACCESS · AUTÓNOMO» o «FULL ACCESS · MANUAL») con icono de aviso. Esta etiqueta no puede ocultarse ni colapsarse.
**RD-H2 (seguridad).** Si la sesión opera en modo degradado o con proveedor de reserva, se muestra una etiqueta equivalente con el motivo.
**RD-H3.** El estado «sin autorización» abre el modal de autenticación existente, con el estilo de este sistema.

### 6.3 Hilo de conversación

Columna centrada de ancho máximo 720 px, con 24 px de margen lateral, 30 px superior y 18 px de separación entre elementos. El scroll es interno al hilo.

#### 6.3.1 Tipos de elemento

| Elemento | Aspecto | Contenido |
|---|---|---|
| **Mensaje del usuario** | Burbuja alineada a la derecha, máx. 82 % de ancho, fondo `--surface`, borde `--line`, radio 16 / 16 / 4 / 16, texto de 14 px | Objetivo de la misión o mensaje posterior |
| **Mensaje de la IA** | Texto sin burbuja, 14,5 px, interlineado 1,65, ancho máx. 64 caracteres | Narración de cada paso, explicaciones, respuestas, resumen final |
| **Tarjeta de paso** | Fila de una línea, ancho máx. 540 px, borde 1 px, radio 11 px | Ver 6.3.2 |
| **Resumen de misión** | Línea de 12,5 px atenuada con separadores «·» | «6 pasos · 1 bloqueado · 0 regresiones · 27 eventos». El último dato es un enlace que abre la pestaña Eventos |
| **Sugerencias** | Píldoras con borde, 13 px, atenuadas | Acciones de seguimiento propuestas por la IA. Desaparecen tras el primer mensaje del usuario |
| **Mensaje de decisión del operador** | Burbuja del usuario | «Aprobado» o «Rechazado», registrado en el hilo tras decidir en la ventana emergente (6.3.3) |
| **Indicador de escritura** | Tres puntos con latido | Respuesta de la IA en preparación |

#### 6.3.2 Tarjeta de paso

Estructura de izquierda a derecha: punto de estado (7 px), nombre del paso (se recorta con elipsis), herramienta en mono atenuada, texto «Ver ›» que solo aparece en hover o selección.

| Estado | Punto | Texto adicional | Borde |
|---|---|---|---|
| Permitido | `--ok` | Ninguno | `--line` |
| En curso | `--accent` con latido | «· en curso» atenuado | `--line` |
| Bloqueado | `--bad` | Segunda línea en `--bad`: «Bloqueado por política · motivo» | `--line`; nombre atenuado |
| Seleccionado | Sin cambio | Sin cambio | `--muted`, fondo `--surface` |

**RD-P1.** La tarjeta de paso no muestra veredicto, puntuaciones, evidencia, política ni recibo. Esa información está exclusivamente en el cajón.
**RD-P2.** Pulsar la tarjeta abre el cajón en la pestaña Paso con ese paso seleccionado.
**RD-P3.** Cada paso va precedido de una frase de narración de la IA que explica qué intenta y por qué.

#### 6.3.3 Ventana emergente de aprobación (control humano en línea)

Se muestra cuando una política exige confirmación del operador. **No es un elemento del hilo: aparece como ventana emergente flotante justo encima del compositor**, alineada con su ancho (máx. 720 px).

- Aspecto: fondo `--surface`, borde de 1 px en `--muted` (más marcado que el de las tarjetas para destacar), radio 14 px, sombra superior discreta, entrada con la animación de 250 ms.
- Distribución en una fila: a la izquierda el título «Necesita tu aprobación» (peso 600) y la descripción atenuada (comando, nivel de riesgo y puntuación de LAYA); a la derecha los botones **Rechazar** (secundario, borde) y **Aprobar** (acción principal, acento).
- Mientras está abierta: el hilo recibe un margen inferior extra para que el contenido no quede tapado, la cabecera muestra la etiqueta «Aprobación pendiente» y el compositor sigue disponible.
- Al decidir: la ventana se cierra, se registra en el hilo el mensaje del operador («Aprobado» o «Rechazado») y la IA responde con la confirmación o el resultado de la acción.
- Solo puede haber una ventana de aprobación a la vez. Si llegan varias solicitudes, se encolan y se muestran de una en una, con el contador «1 de N» en el título.

**RD-A1.** La aprobación pendiente no puede perderse: persiste al cambiar de vista, al abrir o cerrar el cajón y al recargar la sesión mientras la solicitud siga vigente en el runtime. Si el usuario está en otra vista, la etiqueta de la cabecera es visible y pulsarla devuelve a la conversación.
**RD-A2.** La ventana no se cierra con `Esc` ni al pulsar fuera: exige una decisión explícita.
**RD-A3.** Aparece como diálogo de alerta para tecnologías de apoyo (`role="alertdialog"`), recibe el foco en el botón «Rechazar» (la opción segura) y devuelve el foco al compositor al cerrarse.
**RD-A4.** Se conserva toda la información de la solicitud actual (herramienta, comando, riesgo, supervisor) y el detalle completo sigue accesible desde el cajón.

#### 6.3.4 Comportamiento del hilo

- **RD-H4.** Al llegar un elemento nuevo, el hilo se desplaza al final solo si el usuario ya estaba cerca del final (a menos de 140 px). Si ha subido a leer, no se mueve.
- **RD-H5.** Cada elemento se anima una sola vez, al aparecer. Los repintados por cambios de estado no repiten la animación.
- **RD-H6.** Si el usuario escribe mientras hay un paso en curso, la IA responde que lo atenderá al terminar el paso actual; el mensaje no se descarta.
- **RD-H7.** El historial de cada sesión se conserva al cambiar de sesión y al reiniciar la aplicación (comportamiento actual que debe mantenerse).

### 6.4 Compositor (entrada de mensajes)

Bloque único centrado bajo el hilo, ancho máx. 720 px, fondo `--surface`, borde 1 px, radio 18 px, relleno 13 px.

- **Campo de texto:** área de varias líneas que crece desde 24 px hasta 140 px; después hace scroll. `Enter` envía, `Mayús + Enter` inserta salto de línea. Texto de 14,5 px.
- **Fila inferior:** selector de **modelo**, selector de **modo de ejecución** (ambos en texto atenuado sin borde, con desplegable nativo) y, a la derecha, el botón **Enviar** (acento, radio 11 px).
- **Foco:** el borde pasa a `--muted`.

**RD-K1.** Los ajustes de ejecución (modelo, supervisor, modo, pasos máximos, flujo asociado, workspace) viven en el compositor o en Ajustes; no en la cabecera.
**RD-K2.** Elegir un modo de ejecución de riesgo (Full Access) exige la confirmación que ya exige el producto, con el estilo de modal de este sistema.
**RD-K3.** El selector de workspace no está en el compositor: vive en el riel de navegación (6.1).

### 6.5 Cajón de detalle

Panel derecho de 390 px, fondo `--bg`, borde izquierdo de 1 px. Oculto por defecto. Tiene cabecera de 46 px con tres pestañas (texto con subrayado de 1,5 px en `--accent` en la activa) y botón de cierre.

| Pestaña | Contenido | Cómo se abre |
|---|---|---|
| **Mapa** | Árbol de decisiones de la sesión (ver 6.6), alto de 420 px | Botón «Mapa» de la cabecera, o paleta |
| **Paso** | Detalle del paso seleccionado (ver 6.5.1) | Pulsar una tarjeta de paso, o un nodo del mapa |
| **Eventos** | Registro cronológico de eventos del runtime | Enlace «N eventos» del resumen, o paleta |

#### 6.5.1 Pestaña Paso

Orden fijo, de arriba abajo:

1. Título: «Paso N · nombre».
2. Veredicto en 17 px, peso 600: «Permitido» (`--ok`), «Bloqueado» (`--bad`) o «Evaluando» (`--accent`).
3. Bloque de código con el comando o la acción propuesta (mono, 11,5 px).
4. Justificación en texto atenuado.
5. Lista de pares etiqueta / valor: Herramienta, Riesgo (con puntuación), LAYA, TypeSafe, Modelo.
6. **Acordeón cerrado por defecto** con tres secciones: **Evidencia**, **Política**, **Recibo**. Cada cabecera muestra «+» o «–».

Contenido de cada sección del acordeón:

| Sección | Datos |
|---|---|
| Evidencia | Puntuación de grounding y lista de afirmaciones evaluadas |
| Política | Resultado, precedencia, reglas activadas |
| Recibo | Identificador de decisión, hash de acción, hash de estado, nonce, capacidad firmada (HMAC) |

**RD-D1.** Evidencia, política y recibo no son pestañas ni paneles permanentes. Solo se muestran al desplegar el acordeón.
**RD-D2.** Los hashes y los nonces se muestran truncados con elipsis y deben poder copiarse completos.
**RD-D3.** Toda la información que ofrece hoy el inspector (decisión, evidencia, política, recibo) debe seguir accesible; este rediseño cambia su visibilidad, no su disponibilidad.

#### 6.5.2 Pestaña Eventos

Lista en mono de 11,5 px: hora atenuada, tipo de evento y, debajo, el detalle atenuado con sangría. Los eventos de error (por ejemplo, `decision.pruned`) se muestran en `--bad`. La lista se desplaza al último evento al abrirse.

#### 6.5.3 Comportamiento del cajón

- **RD-W1.** `Esc` cierra el cajón.
- **RD-W2.** Seleccionar un nodo del mapa abre la pestaña Paso.
- **RD-W3.** La tarjeta seleccionada del hilo se resalta mientras su paso está abierto en el cajón.
- **RD-W4.** El cajón mantiene su contenido en tiempo real: si llegan eventos o cambia el estado del paso, se actualiza sin cerrarse.

### 6.6 Mapa de decisiones (árbol)

Representación en SVG que se escala al ancho disponible, con un ancho máximo de 440 px y centrada.

| Elemento | Especificación |
|---|---|
| Nodo de inicio | Rectángulo de 100 × 24 px con el texto «Inicio» |
| Nodo de paso | Rectángulo de 160 × 38 px, radio 9 px. Punto de estado, «N. nombre» y herramienta en segunda línea |
| Conexiones | Curvas de Bézier de 1,3 px en `--line` |
| Nodo seleccionado | Borde `--accent` |
| Nodo en curso | Punto `--accent` con latido |
| Nodo bloqueado o podado | Borde discontinuo (3 px / 3 px) y punto `--bad`; segunda línea «podado» |
| Nodo nuevo | Aparece con la animación de 350 ms |

**RD-M3.** El mapa es una vista de apoyo, no el centro de la pantalla. No se muestra salvo que el usuario lo abra.
**RD-M4.** Debe soportar árboles de gran tamaño con desplazamiento, zoom, diseño automático y exportación, conservando la selección. Son capacidades que `DecisionTree` ofrece hoy (controles de zoom, «Auto layout» y descarga) y que deben mantenerse, con sus controles restyleados como botones discretos en la parte superior de la pestaña Mapa. El prototipo no los incluye.

### 6.7 Paleta de comandos

Se abre con `Ctrl K` (o `⌘ K`), con el botón de la cabecera o con el icono de búsqueda del riel.

- Velo en `rgba(8, 9, 11, 0.6)` y diálogo centrado de 520 px de ancho máximo, 14 vh desde arriba, radio 16 px.
- Campo de búsqueda con icono y foco automático; lista agrupada con cabeceras de 11 px en mayúsculas.
- **Grupos y acciones:** *Conversación* (Volver a la conversación, Nueva misión, Abrir mapa de decisiones, Ver eventos de la sesión) y *Vistas* (todas las vistas secundarias).
- **Teclado:** `↑` y `↓` para moverse, `Enter` para ejecutar, `Esc` para cerrar. El filtrado se hace al escribir y reinicia la selección en el primer resultado.
- **Estado vacío:** «Sin resultados».

### 6.8 Vistas secundarias

Se muestran en el área central, sustituyendo al hilo, con margen lateral de `clamp(20px, 5vw, 64px)` y ancho máximo de 1.000 px. Tienen una estructura común: título de 20 px, descripción atenuada de una línea y contenido.

| Componente | Especificación |
|---|---|
| **Tabla** | Cabeceras de 11 px en mayúsculas y atenuadas; filas con borde inferior de 1 px; sin fondo por defecto; ligero resalte en hover |
| **Tarjetas** (Agentes) | Rejilla adaptable (mín. 230 px), borde 1 px, radio 9 px, relleno 14 px, título en peso 600, resto atenuado |
| **Cadena** (Flujos) | Bloques de al menos 150 px unidos por líneas de 1 px; scroll horizontal si no caben |
| **Interruptores** (Seguridad) | Fila con título, descripción atenuada y control de 30 × 17 px; activo en `--accent` |

**RD-V1.** Las vistas del prototipo son representaciones de lectura simplificadas. La implementación real de cada vista debe cumplir la sección 6.10: estilo nuevo al 100 % y funcionalidad completa.
**RD-V3.** `Esc` desde una vista secundaria vuelve a la conversación.

### 6.9 Modales y diálogos existentes

Los modales actuales (autenticación, selector de workspace, sesiones, propuesta de acción, confirmación de Full Access) se rediseñan con el mismo sistema: superficie `--surface`, borde de 1 px, radio 16 px, velo de 60 %, un único botón principal en acento y botón secundario con borde.

**RD-X1.** El diálogo de confirmación de Full Access debe mantener su texto de advertencia íntegro y usar `--bad` para el aviso, sin otros colores de alerta.

### 6.10 Pantallas no cubiertas por el prototipo

**Regla general (RD-F1).** Toda pantalla, formulario, modal y componente de la aplicación que el prototipo no muestra se adapta al nuevo estilo **al 100 %**, sin excepciones ni versiones antiguas conviviendo, y **conserva todas sus funcionalidades actuales**. El rediseño no elimina campos, acciones, validaciones, estados ni atajos. Si una función no tiene sitio evidente en el nuevo diseño, se reubica, no se retira.

**RD-F2.** Antes de implementar cada pantalla se elabora un inventario funcional (todas las acciones, campos, estados y mensajes) a partir del código actual. La pantalla se da por terminada solo cuando una revisión punto por punto contra ese inventario no encuentra diferencias funcionales.

**RD-F3.** Las funciones que hoy se distinguen por color (por ejemplo, los tipos de nodo de un flujo o los niveles de riesgo) se distinguen en el nuevo sistema por icono, etiqueta y forma, dentro de la paleta de la sección 4.1.

#### 6.10.1 Componentes de formulario (base común)

| Componente | Especificación |
|---|---|
| Campo de texto | Alto de 34 px, fondo `--bg`, borde 1 px `--line`, radio 9 px, texto de 13 px. Foco: borde `--muted` y anillo de foco de `--accent` |
| Área de texto | Igual que el campo; crece hasta un máximo y después hace scroll |
| Selector (desplegable) | Mismo aspecto que el campo de texto, con indicador de despliegue. Las opciones usan `--surface` y `--text` |
| Casilla y botón de opción | 16 px, borde `--line`; marcado en `--accent` con la marca en `--on-accent` |
| Interruptor | 30 × 17 px, activo en `--accent` (ya definido en 6.8) |
| Etiqueta del campo | 12,5 px, `--muted`, encima del campo. La ayuda va debajo, atenuada |
| Error de validación | Borde del campo en `--bad` y mensaje de 12,5 px en `--bad` bajo el campo. El mensaje explica qué corregir |
| Campo deshabilitado | Opacidad reducida al 50 %, sin cambio de forma |
| Campo secreto (claves de API) | Se muestra enmascarado; nunca se revela el valor guardado |

| Botón | Aspecto | Uso |
|---|---|---|
| Principal | Fondo `--accent`, texto `--on-accent` | Una sola vez por pantalla o diálogo: Guardar, Enviar, Aprobar |
| Secundario | Borde `--line`, texto `--muted` (`--text` en hover) | Cancelar, Rechazar, acciones alternativas |
| Destructivo | Borde y texto `--bad` | Eliminar, olvidar. Pide confirmación en línea («¿Eliminar? Sí / No») o en diálogo |
| Icono | 32 × 32 px, sin borde, `--muted` | Acciones de fila: editar, reiniciar, actualizar |

Todos los estados de carga, éxito y error de las acciones se comunican en el propio componente (texto y forma), y los éxitos transitorios («Configuración aplicada») usan un aviso breve de 12,5 px, no un color nuevo.

#### 6.10.2 Inventario funcional que debe conservarse

Lista de las capacidades que existen hoy y que la implementación debe mantener. No es exhaustiva: la completa el inventario de RD-F2.

| Pantalla | Funcionalidades que se conservan |
|---|---|
| **Sesiones** | Listado de sesiones, apertura de una sesión anterior, creación de nueva sesión con elección de agente (CodingAgent, SecurityAuditAgent, ExplorerAgent), modo de ejecución (local confinado, contenedor Docker, directo en el host con confirmación), carpeta de trabajo (por defecto la del proyecto), proveedor de modelo (simulador, Ollama, Groq, OpenAI, Gemini, Claude API, Claude CLI) y supervisor (LAYA, TypeSafe AI). Modal «Nueva sesión limpia» |
| **Decisiones** | Filtro por sesión, por veredicto (ALLOW, REVIEW, REPLAN, BLOCKED) y por riesgo (bajo, medio, alto, crítico); tabla con ID/secuencia, estado, herramienta, acción, riesgo, supervisor y recibo HMAC (o «Sin firma»); detalle de decisión con firma HMAC, hash de acción y modo de sandbox |
| **Agentes** | Registrar agente, refrescar, ver estado y versiones, versionar, editar; configuración del motor de inferencia (Gemini, OpenAI, Anthropic, Claude CLI, Ollama, proveedor simulado) y del perfil de riesgo y revisión humana |
| **Flujos** | Editor visual completo: nuevo flujo, plantillas canónicas, validar DAG, centrar, paleta de nodos (Agente, Herramienta o comando, Decisión o IF, Bifurcación, Bucle WHILE, Router, Delegación, Aprobación, Supervisión, Paralelo, Sincronización, Terminal), unión de nodos con nombre de camino y condición, edición de propiedades de cada nodo (nombre, descripción, agente que lo ejecuta, herramienta fija), movimiento libre de nodos, modo cableado, backtrack, guardado y panel de instrucciones rápidas |
| **Proveedores** | Estado de proveedores y backends, latencia observada y fallbacks |
| **Herramientas (MCP)** | Añadir, editar y eliminar servidores; transporte (estándar, SSE, HTTP *streamable*); comando, argumentos, carpeta de trabajo, tiempo máximo por llamada, URL y credenciales protegidas; activar tras revisión, desactivar, reiniciar, actualizar; indicadores de herramientas de solo lectura o destructivas y nivel de riesgo; selector de herramientas por agente |
| **Seguridad** | Estado de las políticas y del endurecimiento de la aplicación, con los mismos controles y datos que hoy |
| **Ajustes** | Clave de API (PRAXEON_API_KEY) con inyección automática en cabeceras, token efímero de WebSocket y almacenamiento seguro; selección de supervisores y proveedores; modo de ejecución por defecto; workspace del proyecto y confinamiento de ruta física; confirmación de que la configuración se aplicó al runtime |
| **Modales** | Autenticación (clave maestra), nueva sesión, sesiones, proponer acción manual (run_command, read_file, edit_file, search_web, git_push), selector de workspace, confirmación de Full Access |

**RD-F4.** El modal «Proponer acción» existe hoy pero su acceso no es evidente en la cabecera actual. En el nuevo diseño debe tener un punto de entrada explícito: una acción en la paleta de comandos («Proponer una acción manual»), sin añadir botones al riel.

#### 6.10.3 Editor de flujos (requisitos específicos)

- El lienzo conserva fondo de puntos (`--line` al 40 %), desplazamiento, zoom y centrado.
- Cada nodo es una tarjeta de 160 px de ancho mínimo, borde 1 px, radio 9 px: icono y nombre del tipo en la primera línea, nombre del nodo en la segunda y agente o detalle atenuado en la tercera. Los tipos se distinguen por icono y etiqueta, no por color (RD-F3).
- Estado de ejecución del nodo (pendiente, en curso, completado, fallido) con el punto de 7 px y los colores de estado habituales.
- Las conexiones son curvas de 1,3 px en `--line`; la conexión seleccionada pasa a `--accent`. El nombre del camino y la condición se muestran como etiqueta sobre la conexión.
- Las propiedades del nodo seleccionado se editan en el cajón lateral (pestaña «Nodo»), con el mismo estilo de formulario de 6.10.1. La paleta de nodos se muestra como lista con icono y nombre en una columna a la izquierda del lienzo.
- Las acciones «Validar DAG», «Guardar» y «Nuevo flujo» están en una barra superior del lienzo: «Guardar» es el único botón principal.

---

## 7. Estados del sistema

| Estado | Representación |
|---|---|
| Listo (sin misión) | Hilo con mensaje de bienvenida y compositor activo. Punto de la cabecera en `--ok` |
| Misión en curso | Punto de la cabecera en `--accent` con latido. Último paso «en curso». Se aceptan mensajes del usuario |
| Misión completada | Mensaje final de la IA, resumen de misión y sugerencias. Punto en `--ok` |
| Paso bloqueado | Tarjeta con motivo en `--bad`; la IA lo explica en su narración |
| Aprobación pendiente | Ventana emergente sobre el compositor y etiqueta «Aprobación pendiente» en la cabecera (6.3.3) |
| Sin conexión con el runtime | Etiqueta en la cabecera con el estado y mensaje en el hilo. Compositor deshabilitado |
| Sin autorización | Modal de autenticación |
| Modo Full Access | Etiqueta permanente en `--bad` (RD-H1) |
| Modo degradado | Etiqueta con motivo (RD-H2) |
| Cargando una vista | Texto «Cargando…» centrado y atenuado |
| Vista sin datos | Mensaje que explica qué aparecerá y cómo crear el primer elemento |

**RD-S1.** Cada estado de error explica qué ha fallado y cómo corregirlo, sin disculpas ni mensajes genéricos.

---

## 8. Contenido y nomenclatura

La interfaz se presenta íntegramente en español.

| Nombre actual | Nombre nuevo |
|---|---|
| Live Canvas | Conversación |
| Sessions | Sesiones |
| Decisions | Decisiones |
| Agents | Agentes |
| Workflows | Flujos |
| Providers | Proveedores |
| Tools (MCP) | Herramientas (MCP) |
| Security | Seguridad |
| Settings | Ajustes |
| Enviar | Enviar |
| Blocked / Review / Allowed | Bloqueados / En revisión / Permitidos |

**Tono de los textos:** directo, en segunda persona del singular para el usuario («Necesita tu aprobación»), voz activa, sin adornos. Los botones nombran la acción que realizan («Aprobar», «Rechazar», «Enviar»). Los términos técnicos que identifican componentes del sistema (LAYA, TypeSafe, MCP, sandbox) se mantienen sin traducir.

**RD-N1.** No se mezclan idiomas en una misma pantalla, salvo los términos técnicos citados.
**RD-N2.** Las insignias de marketing («Visual», «Hardened») se eliminan de la navegación.

---

## 9. Adaptación a distintos tamaños

| Ancho de ventana | Comportamiento |
|---|---|
| Más de 980 px | Disposición completa. El cajón empuja al área central |
| De 701 a 980 px | El cajón se superpone al área central, anclado a la derecha, con sombra lateral |
| 700 px o menos | El riel se oculta y se accede a las vistas mediante la paleta de comandos y un menú en la cabecera. El atajo `Ctrl K` desaparece de la cabecera. El cajón ocupa la pantalla |

**RD-R1.** El hilo y el compositor mantienen un margen lateral mínimo de 16 px en cualquier ancho.
**RD-R2.** Las tablas y las cadenas de flujo hacen scroll horizontal dentro de su contenedor; la página no hace scroll horizontal.
**RD-R3.** El diseño está pensado para escritorio. La adaptación a móvil debe garantizar legibilidad y uso básico (leer el hilo, aprobar, enviar), no la edición compleja de flujos.

---

## 10. Accesibilidad

- **RD-AC1.** Todos los pares de color de texto cumplen un contraste mínimo de 4,5 : 1 (ver 4.1.4).
- **RD-AC2.** Todo elemento interactivo es accesible por teclado y muestra un anillo de foco visible: 2 px sólidos en `--accent` con separación de 2 px.
- **RD-AC3.** Los botones solo con icono tienen `aria-label`. Las etiquetas del riel se anuncian aunque no estén visibles.
- **RD-AC4.** El hilo es una región con anuncio educado de nuevos mensajes de la IA (`aria-live="polite"`); los bloqueos y las aprobaciones pendientes se anuncian con prioridad alta.
- **RD-AC5.** La paleta y los modales se comportan como diálogos: foco atrapado, `Esc` para cerrar y devolución del foco al elemento que los abrió.
- **RD-AC6.** El estado se comunica siempre con texto o forma además del color (RD-C5).
- **RD-AC7.** Se respeta `prefers-reduced-motion` (RD-M1).
- **RD-AC8.** El tamaño de letra respeta el escalado del navegador: los tamaños se definen de forma que la interfaz siga siendo utilizable al 200 %.

### Atajos de teclado

| Atajo | Acción |
|---|---|
| `Ctrl K` / `⌘ K` | Abrir o cerrar la paleta de comandos |
| `Enter` | Enviar mensaje (en el compositor) |
| `Mayús + Enter` | Salto de línea (en el compositor) |
| `Esc` | Cerrar paleta, cerrar cajón o volver a la conversación |
| `↑` / `↓` | Navegar por la paleta |

---

## 11. Requisitos de implementación

### 11.1 Sistema de tokens

- **RD-I1.** Todos los colores, tamaños de letra, radios y duraciones se definen como variables CSS en un único archivo (`index.css`) y los componentes los consumen. Se eliminan los colores hexadecimales sueltos y la mayoría de estilos en línea.
- **RD-I2.** Correspondencia propuesta con los tokens que ya existen en `index.css`:

| Token actual | Valor actual | Token nuevo | Valor nuevo |
|---|---|---|---|
| `--bg-app` | `#0b0e14` | `--bg` | `#0e1013` |
| `--bg-surface` | `#141922` | `--surface` | `#15181c` |
| `--border-subtle` | `#202734` | `--line` | `#22262c` |
| `--text-primary` | `#f0f6fc` | `--text` | `#e4e6e9` |
| `--text-muted` | `#8b949e` | `--muted` | `#80888f` |
| (varios cian) | `#38bdf8` y derivados | `--accent` | `#7aa2f7` |
| `--color-success` | `#3fb950` | `--ok` | `#6fbf8b` |
| `--color-danger` | `#f85149` | `--bad` | `#e07a76` |
| `--color-warning`, `--color-purple` | n/a | (se retiran) | n/a |
| `--shadow-clay-*` | sombras de relieve | (se retiran) | n/a |

- **RD-I3.** Se mantiene Geist y Geist Mono cargadas desde una fuente local o de confianza; el diseño debe verse correcto con las fuentes de reserva si no cargan.

### 11.2 Correspondencia con los componentes actuales

Propuesta de reubicación de lo que existe hoy en `web/src/components`:

| Componente actual | Papel en el nuevo diseño |
|---|---|
| `TopNav.jsx` | Sustituido por el riel de navegación |
| `Header.jsx` | Cabecera simplificada. Conserva las etiquetas de seguridad (Full Access, degradado). El selector de workspace sale de aquí |
| `SessionKPIs.jsx` | Se elimina como banda. Sus contadores pasan al resumen de misión y a la vista Decisiones |
| `DecisionTree.jsx` y `TreeCanvas.jsx` | Se mueven a la pestaña Mapa del cajón |
| `ConsolePanel.jsx` | Se convierte en la pestaña Eventos del cajón |
| `DecisionInspector.jsx` | Se convierte en la pestaña Paso (decisión visible, resto en acordeón) |
| `MissionChat.jsx` | Pasa a ser el hilo central |
| `MissionLauncher.jsx` | Se integra en el compositor |
| `WorkspacePicker` y `useWorkspace` | Se integran en el panel de workspace del riel (6.1), conservando su lógica |
| `SessionsModal.jsx`, `ProposeActionModal.jsx`, `UnauthorizedModal.jsx` | Se mantienen con el estilo de modal de 6.9 |
| `views/*` | Se adaptan al 100 % al estilo nuevo con funcionalidad íntegra (6.8 y 6.10) |

### 11.3 Migración por fases

1. **Fase 1: tokens.** Unificar variables y sustituir colores y tamaños sueltos. Sin cambios de estructura.
2. **Fase 2: navegación y cabecera.** Riel, cabecera simplificada, renombrado de vistas.
3. **Fase 3: conversación.** Hilo con narración, tarjetas de paso, compositor y aprobación en línea.
4. **Fase 4: cajón.** Mapa, Paso y Eventos; acordeón de evidencia, política y recibo.
5. **Fase 5: paleta, workspace y componentes de formulario.** Paleta de comandos, selector de workspace en el riel y biblioteca de componentes de 6.10.1.
6. **Fase 6: vistas secundarias y modales, una a una.** Cada pantalla con su inventario funcional (RD-F2): Sesiones, Decisiones, Agentes, Herramientas MCP, Proveedores, Seguridad, Ajustes, Flujos y modales. Flujos, por su complejidad, se aborda al final.
7. **Fase 7: accesibilidad y ajustes finos.** Foco, lectores de pantalla, movimiento reducido, pruebas en distintos anchos.

**RD-I4.** La narración de la IA por paso (RD-P3) depende de que el runtime proporcione, o permita derivar, una frase de intención para cada acción propuesta. Si no está disponible desde el principio, la tarjeta de paso se muestra sin frase previa y la narración se añade cuando el backend la emita.

---

## 12. Decisiones

### 12.1 Decisiones adoptadas

| Nº | Cuestión | Decisión |
|---|---|---|
| D1 | Ubicación del selector de workspace | En el riel de navegación, como primer botón (6.1). Más adelante incorporará guardado de proyectos y visor de archivos de código |
| D2 | Presentación de la aprobación humana | Ventana emergente encima de la barra de texto del chat (6.3.3). No forma parte del hilo |
| D4 | Misiones muy largas | Aplazado. Se resolverá en una fase posterior |
| D6 | Pantallas no cubiertas por la demo | Se adaptan al 100 % al nuevo estilo con todas sus funcionalidades (6.10) |

### 12.2 Pendientes

| Nº | Cuestión | Opciones |
|---|---|---|
| D3 | Contadores de decisiones (permitidas, bloqueadas, en revisión) | Solo en el resumen de misión; o también como pastillas discretas en la vista Decisiones |
| D5 | Tema claro | No se contempla en esta versión. Si se requiere, se derivaría de los mismos tokens |
| D7 | Diseño del futuro explorador de archivos y de guardado de proyectos | A definir cuando se aborde. Debe respetar el espacio reservado en el riel (6.1) |
| D8 | Varias aprobaciones simultáneas | Cola con contador «1 de N» (propuesta en 6.3.3); confirmar con el equipo de backend si el runtime puede generar más de una a la vez |

---

## 13. Criterios de aceptación

El rediseño se considera completo cuando se cumple todo lo siguiente:

1. La vista Conversación muestra, en el estado inicial, únicamente: riel, cabecera, hilo y compositor. Decisión, evidencia, política y recibo no están visibles.
2. Desde cualquier tarjeta de paso se llega al detalle completo de decisión, evidencia, política y recibo en no más de dos clics.
3. Todas las vistas del producto son accesibles desde el riel, y la paleta de comandos las lista también.
4. No quedan colores hexadecimales fuera del archivo de tokens. El número de tamaños de letra distintos en el producto es de siete o menos.
5. Solo se usan seis colores funcionales: fondo, superficie, línea, texto, atenuado y acento, más los dos de estado (`--ok`, `--bad`).
6. El modo Full Access y el modo degradado son visibles de forma permanente en la cabecera cuando están activos.
7. Una aprobación pendiente se resuelve desde la ventana emergente sobre el compositor, sin abrir ningún otro panel, y deja constancia en el hilo.
8. Todos los pares de texto cumplen un contraste de 4,5 : 1 o superior. Todo es operable con teclado con el foco visible.
9. Con `prefers-reduced-motion` activo no hay animaciones.
10. No hay scroll horizontal de página entre 360 y 1920 px de ancho.
11. No se pierde ninguna funcionalidad respecto a la versión actual de PRAXEON 1.1.0. Cada pantalla pasa su revisión contra el inventario funcional (RD-F2).
12. Ninguna pantalla conserva el estilo anterior: no hay vistas con la paleta, tipografía o componentes antiguos.
13. El selector de workspace está en el riel y conserva exploración de carpetas, recientes y aviso de discrepancia con la sesión.

---

## 14. Anexo: material de referencia

| Archivo | Contenido |
|---|---|
| `design-demo/6-sereno-chat.html` | Prototipo navegable de la dirección aprobada |
| `design-demo/shared.css` y `shared.js` | Estilos base y datos simulados del prototipo |
| `design-demo/index.html` | Índice de las cinco propuestas evaluadas |
| `design-demo/1-sereno.html` a `5-terminal.html` | Propuestas alternativas, descartadas o parcialmente aprovechadas |

**Cómo ver el prototipo:** abrir `design-demo/index.html` en un navegador y entrar en «Sereno, conversacional». Pulsar «Enviar» repite la misión. `Ctrl K` abre la paleta.

**Limitaciones conocidas del prototipo:** los datos y las respuestas del agente son guiones fijos; la lista de workspaces es de ejemplo y no explora carpetas; no cubre el editor de flujos, los formularios de configuración ni los modales de la aplicación real (ver 6.10); las fuentes se cargan desde Google Fonts y requieren conexión.


---

## 15. Estado de la implementación

El rediseño está implementado en `web/` (rama `redesign/sereno`) y compilado en `praxeon/server/static`. Se han verificado con el servidor real: la conversación y sus pasos, el cajón (Mapa, Paso, Eventos), la ventana de aprobación (aprobar y la reaparición para el siguiente paso), el panel de workspace, la paleta, las nueve vistas y los modales, a 1360, 860 y 420 px de ancho.

### 15.1 Qué cubre

| Requisito | Dónde |
|---|---|
| Sistema de tokens, tipografía, movimiento reducido (4.x) | `web/src/index.css` |
| Riel con workspace y todas las vistas (6.1) | `Rail.jsx`, `workspace/WorkspaceFlyout.jsx` |
| Cabecera y etiquetas permanentes de seguridad (6.2) | `Header.jsx` |
| Hilo, tarjetas de paso y compositor (6.3, 6.4) | `Conversation.jsx` (conserva la lógica del chat anterior) |
| Ventana de aprobación (6.3.3) | `ApprovalPopup.jsx` |
| Cajón con Mapa, Paso y Eventos (6.5, 6.6) | `Drawer.jsx`, `StepDetail.jsx`, `EventsPanel.jsx`, `DecisionTree.jsx` |
| Paleta de comandos (6.7) | `CommandPalette.jsx` |
| Vistas secundarias, formularios y modales (6.8 a 6.10) | `views/*`, `ui.jsx`, `SessionsModal.jsx`, `ProposeActionModal.jsx`, `UnauthorizedModal.jsx` |
| Avisos y confirmaciones propios (RD-S1) | `Dialogs.jsx` (sustituye a `alert()` y `confirm()`) |

### 15.2 Diferencias y decisiones tomadas al implementar

1. **Cajón ampliable.** Se añade un botón para ampliar el cajón (de 390 px a un máximo de 760 px). El mapa de decisiones y los bloques de flujo necesitan más ancho que el resto de pestañas. El mapa se reencuadra solo al cambiar de tamaño.
2. **Botón de operador en el riel.** El perfil del operador y la clave de API (antes en la cabecera) pasan a un botón en la parte inferior del riel, junto a Buscar y Ajustes.
3. **Acciones de la antigua cabecera.** «Sesiones registradas» se abre desde el identificador de sesión de la cabecera; «Nueva sesión», con el botón «+». «Proponer acción manual» (RD-F4), «Cargar la sesión de demostración», pausar, reanudar y detener la misión, y cambiar de workspace están también en la paleta.
4. **Contadores de decisiones (D3).** Se muestran en el resumen de la misión (bloqueadas y en revisión) y como tarjetas en la vista Decisiones.
5. **Colores de estado.** Amarillo, naranja y morado desaparecen: «en revisión» y «aprobación pendiente» usan el acento; «replanificado» y los tipos de nodo de un flujo se distinguen con texto e icono (RD-C3, RD-F3). Los agentes de un flujo se distinguen por su nombre, no por color.
6. **La conversación permanece montada** al cambiar de vista, de modo que el texto escrito y la configuración no se pierden. El hilo y el historial por sesión conservan su comportamiento.
7. **Aprobación pendiente.** Solo se ofrece mientras la misión está en curso. El rechazo exige un motivo, con el error en línea. Las decisiones del operador quedan en el hilo como mensajes de «Operador» y no se envían al modelo como historial.
8. **Corrección incidental.** El modal «Sesiones registradas» entregaba solo el objetivo a la función que inicia misiones, que espera un objeto. Ahora se completa con la configuración de la conversación.
9. **Eliminado.** `MissionLauncher.jsx` y `Sidebar.jsx` (no se importaban), `TopNav.jsx`, `SessionKPIs.jsx`, `ConsolePanel.jsx`, `DecisionInspector.jsx` y `MissionChat.jsx` (sustituidos) y `App.css` (plantilla de Vite sin uso).

### 15.3 Pendiente

- **Misiones largas (D4):** aplazado por decisión de dirección.
- **Pruebas de interfaz automatizadas:** no existen en el proyecto; la verificación fue manual con capturas del servidor real. Recomendable añadir pruebas de extremo a extremo para la conversación, la aprobación y la paleta.
- **Editor de flujos:** se ha adaptado al sistema (barra, paleta, tipos de nodo por icono, conexiones y modales) y se han traducido todos los textos visibles. Su estructura de tres columnas y la barra de registro inferior se mantienen. Un segundo repaso, con un flujo real ejecutándose, puede ajustar espaciados y etiquetas de los nodos.
- **Texto del backend:** algunos mensajes que genera el servidor (razonamientos del agente, salidas de comandos, códigos de motivo) llegan sin traducir y se muestran tal cual.
- **Tema claro (D5)** y **explorador de archivos (D7):** fuera del alcance de esta versión.
