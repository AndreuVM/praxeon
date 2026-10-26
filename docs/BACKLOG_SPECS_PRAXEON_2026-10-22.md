# Praxeon: especificaciones de implementación del backlog

Fecha: 2026-10-22 · Fuente: marq, proyecto Praxeon (`3c1d9d54-d8e5-4953-9bd3-05b795ffdb39`)
Estado del proyecto: 135/139 tareas hechas; 4 en backlog.

| Código | Título | Prioridad | Depende de | Id marq |
|---|---|---|---|---|
| FE-001 | Implementar visualización de archivos | medium | n/a | c5d5a16a-735b-4dea-86db-61dc1094e7a0 |
| FE-002 | Implementar múltiples workspaces | medium | FE-001 | 7d1bc5fa-3d03-4e7e-b11d-95d483cb932e |
| FE-003 | Implementar visualizador y editor de archivos | high | n/a (recomendado: FE-001) | 9b74c96c-3592-4c3d-8040-0467a7104acd |
| SK-001 | Añadir soporte Skills | medium | n/a | df3eb7e3-8451-494c-90a2-7e35cecb5864 |

Orden recomendado: FE-001 → FE-003 → FE-002 → SK-001.

## Base existente relevante

- Backend: `praxeon/server/routes/workspace.py` (prefijo `/v1/workspace`: GET, PUT, DELETE, POST `/forget`, GET `/validate`, GET `/browse`) y `praxeon/workspace_store.py` (persistencia en `<caché>/workspace.json`, recientes, `validate_directory`, `browse_directory`, `project_hints`).
- Frontend: `web/src/components/workspace/` (`useWorkspace.js`, `WorkspaceFlyout.jsx`, `WorkspacePicker.jsx`, `WorkspaceField.jsx`), `web/src/services/api.js` (líneas ~574-600; evento `praxeon:workspace-changed`).
- Skills: solo existen como `skills: List[str]` en `AgentDefinition` (`praxeon/agents/definition.py`), plantillas (`agents/templates.py`), filtro `?skill=` en `server/routes/agents.py` y puntuación en `routing/router.py` y `routing/strategies.py`. No hay entidad Skill, carga ni activación.

---

## FE-001: Visualización de archivos del workspace en la barra lateral

**Objetivo:** listar los archivos del workspace actual en la barra lateral.

**Backend**
- Nuevo endpoint `GET /v1/workspace/tree?path=<rel>&depth=1&show_hidden=false` que devuelve entradas `{name, rel_path, type: file|dir, size, mtime}` ordenadas (carpetas primero). Carga perezosa por carpeta.
- Reutilizar `workspace_store` para resolver la raíz. Resolver con `Path.resolve()` y rechazar (400/403) cualquier ruta que salga de la raíz (path traversal, symlinks hacia fuera).
- Ignorar por defecto `.git`, `node_modules`, `.venv`, `__pycache__`, `dist`; límite de entradas por respuesta (p. ej. 1000) con `truncated: true`.
- Requiere la misma autenticación que el resto de rutas.

**Frontend**
- `api.js`: `fetchWorkspaceTree(relPath)`.
- Componente `FileTree.jsx` en `web/src/components/workspace/`, integrado en `Rail.jsx` o un panel lateral. Expandir/colapsar, iconos por tipo, estados vacío/carga/error.
- Se recarga al recibir `praxeon:workspace-changed`.

**Criterios de aceptación**
1. Al abrir la app se ve el árbol del workspace activo.
2. Cambiar de workspace actualiza el árbol sin recargar.
3. Rutas con `..` o symlinks externos son rechazadas (test).
4. Directorios grandes no bloquean la UI.

**Pruebas:** pytest para el endpoint (traversal, ocultos, truncado); prueba de componente de render y expansión.
**Estimación:** 4-6 h (hoy 1 h; se recomienda actualizar).

---

## FE-002: Múltiples workspaces con cambio rápido

**Objetivo:** poder agregar workspaces y cambiar rápidamente entre ellos.

**Estado actual:** `workspace_store` ya guarda un workspace seleccionado y una lista de recientes, y la UI tiene `WorkspaceFlyout`/`WorkspacePicker`. Falta el concepto de conjunto de workspaces abiertos/fijados.

**Backend**
- Ampliar `workspace.json` a `{active, pinned: [{path, label}], recent: []}`, con migración desde el formato actual.
- Endpoints: `POST /v1/workspace/pinned` (añadir, valida con `validate_directory`), `DELETE /v1/workspace/pinned` (quitar), `PUT /v1/workspace` ya cambia el activo.
- `describe_workspace()` incluye `pinned`.

**Frontend**
- `useWorkspace` expone `pinned`, `add`, `remove`.
- Conmutador rápido en `Header.jsx`/`WorkspaceFlyout` (lista de fijados), atajo en `CommandPalette` ("Cambiar workspace"), etiqueta editable.
- Las misiones nuevas usan el activo; las sesiones existentes conservan el suyo.

**Criterios de aceptación**
1. Se pueden añadir ≥ 2 workspaces y alternar con un clic o atajo.
2. Persisten tras reiniciar el servidor.
3. Una ruta eliminada del disco se marca como no disponible sin romper la lista.
4. Migración sin pérdida del `workspace.json` actual (test).

**Dependencia:** FE-001 (el árbol debe reaccionar al cambio).
**Estimación:** 6-8 h (hoy 0 h).

---

## FE-003: Visualizador y editor de archivos

**Objetivo:** editar código y previsualizar HTML, PDF y Markdown.

**Backend**
- `GET /v1/workspace/file?path=<rel>`: contenido (texto UTF-8) o bytes con `Content-Type` correcto; límite de tamaño (p. ej. 2 MB texto) y detección de binarios.
- `PUT /v1/workspace/file`: guardar con control de concurrencia (`etag`/`mtime` esperado → 409 si cambió). Escritura atómica (tmp + rename).
- Mismo confinamiento al workspace que FE-001. Las escrituras deben pasar por la política de seguridad del proyecto (PolicyEngine / Full Access, ver `docs/FULL_ACCESS_POLICY.md`) y registrarse en auditoría. **Decisión pendiente:** confirmar con el responsable si el editor humano queda fuera del flujo de capabilities del agente.

**Frontend**
- Componente `FileViewer.jsx` con pestañas.
- Editor de código: CodeMirror 6 (ligero) con resaltado por extensión; indicador de cambios sin guardar; Ctrl+S.
- Markdown: reutilizar `Markdown.jsx`.
- HTML: `<iframe sandbox>` sin `allow-same-origin` y con `srcdoc`/blob, para evitar ejecución con privilegios de la app.
- PDF: `<iframe>`/`<object>` con blob, o pdf.js si se requiere control.
- Mantener code splitting (`React.lazy`) como en AUDIT-P2, para no superar ~250 kB por chunk.

**Criterios de aceptación**
1. Abrir un archivo desde el árbol (FE-001) lo muestra en el visor.
2. Editar y guardar persiste el cambio; conflicto → aviso 409.
3. HTML renderiza aislado; scripts no acceden a la API de la app.
4. Binarios/archivos grandes no se cargan como texto.

**Estimación:** 12-16 h (hoy 0 h).
**Nota de datos:** el evento de creación en marq lleva `reference_code: R-079` mientras la tarea se muestra como FE-003; conviene unificar.

---

## SK-001: Soporte de Skills (habilitar/deshabilitar)

**Objetivo:** gestionar Skills y activarlas o desactivarlas a voluntad.

**Diseño**
- Entidad `Skill`: `id`, `name`, `description`, `version`, `source` (carpeta/archivo `SKILL.md` + metadatos), `enabled`, `scope` (global | workspace), `allowed_tools` opcionales.
- Registro `praxeon/skills/registry.py`: descubre skills en `<caché>/skills/` y `<workspace>/.praxeon/skills/`, valida el esquema, persiste el estado `enabled`.
- Integración: las skills habilitadas se inyectan en el prompt de la misión mediante el `ContextBuilder` (`praxeon/context/`) respetando el token budget; el campo `AgentDefinition.skills` referencia ids de skills existentes (validar y avisar de ids desconocidos; las plantillas actuales usan strings sueltos como `pytest`).
- Seguridad: una skill no concede capabilities nuevas; las herramientas que declare siguen sujetas a PolicyEngine/SecureExecutor. Contenido tratado como no confiable.

**API**
- `GET /v1/skills`, `GET /v1/skills/{id}`, `POST /v1/skills/{id}/enable`, `POST /v1/skills/{id}/disable`, `POST /v1/skills/reload`.
- Esquemas en `praxeon/server/schemas/skill.py`; SDK (`packages/sdk`) y `openapi.json` actualizados.

**Frontend**
- `SkillsView.jsx` (lista, interruptor on/off, detalle) y entrada en `Rail.jsx`.

**Criterios de aceptación**
1. Se listan las skills detectadas con su estado.
2. Desactivar una skill la excluye del contexto de nuevas misiones.
3. El estado persiste tras reinicio.
4. Skill con manifiesto inválido se reporta sin tumbar el registro.
5. Skills referenciadas por un agente y no encontradas generan advertencia.

**Estimación:** 14-18 h (hoy 0 h).

---

## Riesgos y preguntas abiertas

1. Confinamiento de rutas en FE-001/FE-003 (traversal, symlinks) es el principal riesgo de seguridad.
2. Alcance del editor frente a la gobernanza de escritura (FE-003).
3. Formato de skill: ¿compatible con `SKILL.md` de Claude u otro propio? (SK-001).
4. Las estimaciones de horas en marq (1, 0, 0, 0) no reflejan el alcance real.
