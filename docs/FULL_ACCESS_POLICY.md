# Política de Gobernanza y Modelo de Confianza: FULL_ACCESS

**PRAXEON v1.0.0 — Documento de Arquitectura y Seguridad Operacional**

---

## 1. Principio Rector

> **El sandbox no define PRAXEON; es un mecanismo de contención bajo PRAXEON.**

En PRAXEON, la autoridad de ejecución está estricta y formalmente desacoplada de la generación de razonamiento del LLM. La decisión de mantener `ExecutionMode.FULL_ACCESS` es una **decisión deliberada de arquitectura y de producto** para permitir flujos de ingeniería reales (como desarrollo asistido en el host, interacción con herramientas nativas de desarrollo, git, compiladores y utilidades del sistema) sin las fricciones o limitaciones de contención artificial de un sandbox POSIX estándar.

---

## 2. Modelos de Confianza: Sandbox vs. Full Access

| Dimensión | Sandbox (`local_restricted` / `container`) | Full Access (`full_access`) |
| :--- | :--- | :--- |
| **Aislamiento en SO** | Confinamiento en directorio raíz de workspace y entorno filtrado. | **Sin confinamiento en SO:** Ejecución directa sobre el sistema operativo host. |
| **Variables de Entorno** | Sanitizadas, filtradas contra fuga de credenciales. | Heredadas del proceso anfitrión del operador. |
| **Acceso a Red** | Restringido por política de egress (`EgressPolicy`). | Acceso directo de red del host anfitrión. |
| **Modelo de Amenaza** | Asume código o comandos potencialmente adversariales que deben ser contenidos físicamente. | Asume un **modelo de confianza explícito** otorgado por el operador humano a tareas específicas. |
| **Autoridad de Ejecución** | Requiere capability HMAC emitida por `PolicyEngine`. | **Requiere capability HMAC emitida por `PolicyEngine` con `execution_mode='full_access'`.** |

---

## 3. Invariantes Inquebrantables del Runtime

Aun cuando una sesión se configure en `execution_mode="full_access"`, las fronteras de seguridad y gobernanza de PRAXEON se mantienen incondicionalmente activas:

### 3.1. `BLOCK` Siempre Gana (Zero Physical Execution)
- Si una acción propuesta viola una regla crítica de seguridad, contiene patrones destructivos no autorizados o es clasificada con riesgo crítico, `PolicyEngine` emite un veredicto `BLOCK`.
- **Invariante:** Una decisión en `BLOCK` **NUNCA** emite una capability válida y **JAMÁS** alcanza a `FullAccessExecutor`. El sistema garantiza **0 ejecuciones físicas** en el sistema operativo host.

### 3.2. `REPLAN` Permanece Inmutable
- Si el supervisor semántico (LAYA / TypeSafe) o el motor de evidencias detecta bucles, desvío de objetivo o contradicciones empíricas, la decisión se evalúa como `REPLAN` (o poda del árbol).
- **Invariante:** `REPLAN` **NUNCA** se degrada silenciosamente a ejecución; el runtime fuerza el retroceso de trayectoria o la reformulación del plan sin tocar el sistema anfitrión.

### 3.3. Contrato Estricto para Bypass de `REVIEW`
- Por defecto, toda operación con efectos secundarios en host exige confirmación humana (`status="REVIEW"` / `requires_confirmation=True`).
- Para que una acción en `REVIEW` se ejecute de forma desatendida en Full Access, deben cumplirse **simultáneamente** las tres condiciones del contrato:
  1. Sesión explícitamente configurada en `execution_mode="full_access"`.
  2. Bandera de autonomía activa (`autonomous=True` o `allow_unattended_execution=True`).
  3. **Autorización explícita y verificada del operador** (`full_access_authorized_by_operator=True`), validada en el servidor mediante token o credencial de operador (`PRAXEON_OPERATOR_KEY` / `PRAXEON_SECRET_KEY`).
- Cualquier cliente no autenticado o llamada sin verificación de operador que solicite Full Access autónomo queda retenida forzosamente en `REVIEW`.

---

## 4. Cadena de Custodia Criptográfica y Anti-Replay

Para que `SecureExecutor` despache una llamada a `FullAccessExecutor`:
1. Debe presentarse un `DecisionReceipt` / capability firmado con HMAC-SHA256 (`sign_receipt`).
2. El capability debe coincidir exactamente en `session_id`, `action_hash` y `state_hash`.
3. El campo `execution_mode` dentro del token firmado debe ser idéntico a `"full_access"` (cualquier intento de elevar privilegios desde `local_restricted` adultera la firma HMAC y produce rechazo inmediato).
4. El nonce asociado debe ser consumido de forma atómica en `NonceStore`, impidiendo ataques de repetición o carreras concurrentes.

---

## 5. Auditoría y Trazabilidad Operacional

Toda ejecución física efectuada bajo Full Access genera evidencia auditable e indeleble en el `EventStore`:
- Evento `execution.started`: Registra `execution_mode="full_access"`, `sandboxed=False` e `isolation="None (Host OS)"`.
- Evento `execution.completed`: Registra el código de salida, tiempo de ejecución y resultado físico.
- Registro en log de seguridad: Se emite advertencia operacional `[SECURITY AUDIT]` indicando la sesión y herramienta despachada en host.
