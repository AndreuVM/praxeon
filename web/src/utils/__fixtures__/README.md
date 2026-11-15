# Fixtures del reducer de sesión (FE-007)

- `sessionStreams.json`: flujos de eventos y el estado inicial de cada caso. `manual_*` y `simulator_mission` son eventos REALES
  grabados del backend (runtime en proceso); `workflow_synthetic`, `hub_nodes_and_ids`, `conclusion_text_inside_output` y la cola de
  `manual_plus_completion` son sintéticos (el backend no los emite en pruebas) con la forma de los eventos reales.
- `sessionGolden.json`: el estado tras CADA evento (huella) y el final, producidos EJECUTANDO el callback original de `App.jsx` del
  commit `3e504de`. No se editan a mano.
- Regenerar solo si cambia el comportamiento a propósito: `TZ=UTC node scripts/gen_session_golden.mjs <grabados> src/utils/__fixtures__`
  (desde `web/`; lee `App.jsx` de ese commit con `git show`). Las rutas locales se sustituyen antes de escribir.
- Los eventos reales se graban con `scripts/record_session_events.py` (arranca el runtime en proceso, sin red).
