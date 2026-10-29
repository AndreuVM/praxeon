# PRAXEON — Guión del vídeo para TikTok (vertical)

**Duración:** ~46 s · **Formato:** 9:16, 1080×1920, 30 fps · **Narración:** inglés (Kokoro `am_michael`) · **Subtítulos:** palabra a palabra, integrados
**Estructura:** problema → solución. El acto 1 es una dramatización exagerada y cómica con un agente genérico («YOLO Agent»), no con PRAXEON. El acto 2 usa grabaciones reales de PRAXEON: una misión en vivo con Claude CLI sobre un repositorio de demostración desechable.
**Enlace final:** `andreuvm.github.io/praxeon`

| # | Voz en off (EN) | Visual | Entrada |
|---|-----------------|--------|---------|
| 1 | POV: you gave your AI agent full access to your repo. | «POV:» y la app del agente; el interruptor FULL ACCESS se enciende y se marcan los permisos (borrar… cualquier cosa). | Corte (primer fotograma ya con texto) |
| 2 | You asked it to fix one typo. | Burbuja: «fix the typo in the README pls 🙏» → «On it! 🚀». | — |
| 3 | It deleted your entire git history. | Terminal: `rm -rf .git` → «removed 4,812 commits», sacudida y destello rojo. | — |
| 4 | Dropped the users table. | `DROP TABLE users;` y el contador de usuarios cae a 0. | — |
| 5 | And force-pushed to production. On a Friday. | `git push --force origin main`, calendario «FRI 17:59» y avisos: Production is down, «who pushed to main??», «call me. now.». | — |
| 6 | Then it said: all done! | Todo cae; zoom a «All done! ✨ your repo is *super* clean now 😊». Congelado + scratch de disco. | — |
| 7 | Your agent doesn't need less power. It needs a supervisor. | Tipografía cinética: «less power» tachado; «supervisor» en azul. | Destello |
| 8 | Meet PRAXEON. | Construcción del isotipo y del logotipo. | Destello |
| 9 | Same agent, same request. But now, every action is checked before it runs. | Grabación real: se teclea la misma petición; avance rápido de los pasos con chips «✓ checked». | Iris |
| 10 | Wipe the git history? Blocked. | Grabación real: `run_command` → DESTRUCTIVE_COMMAND_BLOCK; sello **BLOCKED**, sacudida y viñeta roja. | Corte |
| 11 | It fixes just the typo, and waits for your OK before pushing to production. | Grabación real: «hago solo la parte segura» → tarjeta «Necesita tu aprobación» (`git push origin main`, riesgo high) y clic en **Aprobar**. | Corte |
| 12 | Build multi-agent workflows, with reviewers, loops and human gates. | Lienzo real del flujo *Developer - Reviewer* en 3D; la cámara recorre Start → Writer → Developer → WHILE → Reviewer → End. | Flip 3D |
| 13 | Any model, local or cloud. MCP tools. Web search. All supervised. | Montaje: Proveedores (chips Claude, Ollama, OpenAI, Gemini, Groq, OpenRouter, LM Studio), Herramientas MCP, Agentes. | Zoom |
| 14 | PRAXEON. The model proposes. The runtime decides. | Logotipo, lema y píldora con el enlace `andreuvm.github.io/praxeon`. | Destello |

## Notas

- **Grabaciones reales:**
  - Las misiones se ejecutaron con Claude CLI en un repositorio desechable (`demo-shop`) con un remoto local de «producción».
  - El bloqueo de `Remove-Item -Recurse -Force .git` y la revisión de `git push origin main` (REMOTE_MUTATION_REQUIRES_CONFIRMATION) son decisiones reales del supervisor.
  - Son dos tomas: el bloqueo y la aprobación.
- **Velocidad:** el tecleo va a 3× y los pasos intermedios a unas 57× (se indica en pantalla con «fast-forward»).
- **Workflows:** el lienzo de *Developer - Reviewer* es una captura estática; no se muestra el estado de los nodos durante la ejecución.
- **Proyecto:** el proyecto HyperFrames está en `videos/praxeon-tiktok/` y se regenera con `node build.mjs`.
