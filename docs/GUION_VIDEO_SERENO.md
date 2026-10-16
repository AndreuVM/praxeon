# PRAXEON — Guión del vídeo promocional (UI «Sereno»)

**Duración:** ~1:25 · **Formato:** 16:9, 1920×1080, 30 fps · **Narración:** inglés (voz masculina, ritmo rápido) · **Textos en pantalla:** inglés
**Material:** capturas y grabaciones reales de la interfaz «Sereno» (1440×810 a 2×, con nitidez para planos cercanos). No se simula nada que la app no haga hoy.
**Lenguaje visual:** las pantallas de la UI flotan como planos en un espacio 3D oscuro con la rejilla del suelo y el brillo azul de marca (`#7aa2f7`). La cámara se desliza sobre ellas en picado, contrapicado y laterales. Tarjetas reales de la interfaz «se despegan» del plano y flotan hacia cámara. Etiquetas en Geist Mono flotan en profundidad. Titulares cinéticos en Geist con la palabra clave en azul.
**Transiciones:** whip (barrido con desenfoque), whip vertical, flip 3D, iris, corte diagonal (slice), zoom-through y destello de marca.

> **Mensaje central:** *The model proposes. The runtime decides.*

| # | Escena | Voz en off (EN) | Visual | Entrada |
|---|--------|-----------------|--------|---------|
| 1 | Gancho | AI agents don't just answer questions anymore. They act on your real systems. | Comandos peligrosos (`rm -rf`, `DROP TABLE`, `terraform destroy`…) vuelan hacia cámara en profundidad. Titular: *AI agents don't just answer. **They act.*** | Fundido |
| 2 | El riesgo | They run commands, edit files and push to production. One wrong step is enough. | Terminal en ángulo; se teclea `rm -rf logs/ && git push origin main`, aviso rojo, glitch y viñeta roja. | Destello |
| 3 | Logo | Meet PRAXEON. Runtime supervision for autonomous AI agents. | Se construye el isotipo (barra + cuenco azul), entra el logotipo y la línea de luz. Caída musical. | Zoom-through |
| 4 | Espacio de trabajo | One calm workspace, where every agent mission is supervised, end to end. | La pantalla de inicio llega desde el fondo tumbada y se endereza; el compositor se despega; flotan «Simulado (offline)», «LAYA», «Local restringido». | Iris |
| 5 | Misión | Give an agent a mission in plain language, and it plans its own steps. | Grabación real: se teclea la misión. Cámara en contrapicado siguiendo el texto. | Whip |
| 6 | Supervisión en vivo | Each action it proposes is checked live, before it touches your files or your data. | Grabación real: aparecen los pasos; un escáner recorre la tarjeta; etiquetas «LAYA · evaluating», «risk assessed». | Whip vertical |
| 7 | Bloqueo | Destructive commands are stopped on the spot, and the agent is forced to replan. | Primer plano de `SUPERVISOR_LAYA_DESTRUCTIVE_BLOCK`; la tarjeta se despega, sello **BLOCKED**, sacudida y viñeta roja. | Destello |
| 8 | Aprobación humana | High-risk moves, like a push to production, wait for a human to approve them. | Grabación real: tarjeta «Necesita tu aprobación» (`git push … riesgo high`), el cursor pulsa **Aprobar**, onda verde y «Aprobado». | Slice |
| 9 | Mapa de decisiones | The decision map shows every branch the agent tried, and every path it pruned. | Órbita alrededor del árbol; los nodos se elevan uno a uno; «pruned branch», «replanned path». | Flip 3D |
| 10 | Inspector | Open any step to see its risk score, both supervisors' verdicts, and the evidence. | Grabación real del panel «Paso»; la cámara baja por él; se resaltan Riesgo, LAYA 0.05 y TypeSafe 0.82. | Whip vertical |
| 11 | Auditoría | Every decision is logged and signed with an HMAC receipt, ready for audit. | Dos planos: tabla de Decisiones (columna «Recibo HMAC» resaltada) y flujo de Eventos detrás. | Zoom-through |
| 12 | Workflows | Design multi-agent workflows from canonical templates, with human gates and bounded loops. | Grabación real: modal de plantillas canónicas; cámara cenital que baja; «WHILE ≤ 3», «human gate». | Slice |
| 13 | Validación | Validate the graph first, so nothing runs by surprise. | Grabación real: clic en «Validar DAG», banner verde «DAG válido, sin ciclos». | Whip |
| 14 | Agentes | Every agent has its own model, tools and risk limits, versioned and immutable. | Las tarjetas de agentes se despegan en abanico; «claude_cli / sonnet», «risk ceiling», «SHA-256». | Flip 3D |
| 15 | Proveedores y MCP | Use any model, local or cloud, and plug in MCP tools under the same supervision. | Dos planos en libro abierto (Proveedores + Herramientas MCP); tarjetas de supervisores elevadas. | Zoom-through |
| 16 | Seguridad | Workspace isolation, command vetoes and anti-replay nonces are always on. | Planeo descendente por las barreras activas; las métricas se elevan; pulsos verdes «Activa y verificada». | Whip vertical |
| 17 | Muro | One runtime, watching every single step. | Muro 3×3 de todas las pantallas; la cámara se aleja y gira. | Zoom-through |
| 18 | Cierre | PRAXEON. The model proposes. The runtime decides. | Logotipo, *The model proposes. The runtime **decides.*** y lema. | Fundido |

## Notas

- El motor de workflows aún no ejecuta los flujos de extremo a extremo, así que el vídeo solo muestra diseño, plantillas y validación del DAG. No aparece ninguna ejecución simulada.
- Las misiones se grabaron con el agente simulado offline. La misión de ejemplo es *"Delete the old logs folder with rm -rf logs/ and push the fix to production"*.
- El proyecto HyperFrames está en `videos/praxeon-sereno-promo/`. Las escenas se generan con `node build.mjs`.
