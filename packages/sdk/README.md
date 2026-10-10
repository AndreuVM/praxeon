# @praxeon/sdk

**Cliente TypeScript y SDK oficial para PRAXEON: Runtime Supervision for Autonomous AI Agents.**

[![npm version](https://img.shields.io/badge/version-1.1.0-blue.svg)](https://github.com/AndreuVM/praxeon)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-green.svg)](https://opensource.org/licenses/Apache-2.0)

PRAXEON proporciona supervisión formal determinista para agentes de IA autónomos (ReAct, Tool-use, Tree-of-Thought). El modelo propone acciones; el runtime PRAXEON verifica evidencias, audita riesgos, emite tokens criptográficos HMAC-SHA256 y confina la ejecución física.

---

## 📦 Instalación

```bash
npm install @praxeon/sdk
# o con pnpm / bun / yarn
pnpm add @praxeon/sdk
```

---

## 🚀 Inicio Rápido

### 1. Inicializar el Cliente

```typescript
import { PraxeonClient } from '@praxeon/sdk';

const client = new PraxeonClient({
  baseUrl: 'http://127.0.0.1:8000',
  apiKey: process.env.PRAXEON_API_KEY, // Opcional en modo dev
});
```

### 2. Proponer y Supervisar una Acción de un Agente

```typescript
// 1. Iniciar sesión
const session = await client.createSession({
  goal: 'Auditar archivos de configuración del proyecto',
  executionMode: 'local_restricted',
});

// 2. El agente propone una acción (read_file)
const receipt = await client.proposeAction(session.id, {
  actionId: 'act_1',
  tool: 'read_file',
  arguments: { path: 'pyproject.toml' },
  thoughtRationale: 'Examinar dependencias declaradas en el proyecto',
});

// 3. Inspeccionar el veredicto del supervisor
if (receipt.status === 'ALLOW') {
  // Ejecutar con el token criptográfico otorgado
  const result = await client.executeDecision(receipt.decisionId);
  console.log('Resultado de ejecución:', result.output);
} else if (receipt.status === 'REVIEW') {
  console.log('Acción requiere confirmación humana del operador.');
} else {
  console.warn('Acción bloqueada o podada:', receipt.policy.reasonCodes);
}
```

### 3. Conexión en Tiempo Real vía WebSocket

```typescript
const wsUrl = client.createEventStreamUrl(session.id);
const ws = new WebSocket(wsUrl);

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log(`[EVENT ${data.type}]:`, data.payload);
};
```

---

## 🛠️ Contratos de Tipos

El SDK incluye tipos completos para:
- `ExecutionMode`: `'container' | 'local_restricted' | 'full_access'`
- `DecisionStatus`: `'ALLOW' | 'BLOCK' | 'REVIEW' | 'REPLAN'`
- `CapabilityToken`: Tokens firmados mediante HMAC-SHA256
- `WorkflowDefinition`, `WorkflowNode`, `WorkflowEdge`

---

## 📄 Licencia

Apache-2.0 © AndreuVM.
