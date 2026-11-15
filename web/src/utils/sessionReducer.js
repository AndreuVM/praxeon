// Estado en vivo de UNA sesión de agente y su reducción a partir de eventos (FE-007, nota N-001).
//
// Antes esta lógica vivía dentro de un `useEffect` de App.jsx (~600 líneas) que escribía en seis `useState` sueltos y solo sabía
// de UNA sesión. Aquí es una función pura: `reduceEvent(estado, evento, contexto)` devuelve el estado nuevo sin tocar el anterior,
// así que se prueba sin navegador y cada panel puede tener el suyo. Está fijada por pruebas diferenciales contra lo que producía
// el código original (`sessionReducer.test.js` y sus fixtures).
import { computeTreeLayout } from './treeLayout';

export const READY_SESSION = {
  sessionId: 'ready',
  status: 'Ready',
  agent: 'CodingAgent',
  goal: 'Listo para iniciar misión supervisada. Introduce un prompt y selecciona modelos.',
  metrics: { totalDecisions: 0, allowed: 0, blocked: 0, review: 0 },
  runtime: { provider: 'JEV + LAYA', version: 'v1.0.0', latencyP50: '—', executionTime: '—' },
};

export const START_NODE = { id: 'start', label: 'Start', type: 'start', status: 'SYSTEM', x: 420, y: 30, parentId: null };
export const READY_NODES = [START_NODE];

/** Hora local «HH:MM:SS» con la que se rotulan eventos y registros. */
export const clockOf = (when) => new Date(when).toTimeString().split(' ')[0];

/** Estado vacío de una sesión. `logs` empieza vacío: quien lo crea decide el mensaje inicial. */
export function createSessionState(overrides = {}) {
  return {
    session: READY_SESSION,
    nodes: READY_NODES,
    decisionsMap: {},
    selectedNodeId: 'start',
    logs: [],
    events: [],
    isRunning: false,
    maxSeq: 0,
    ...overrides,
  };
}

/** Texto del registro de terminal para un evento. `executionMode` decide el rótulo de `execution.started`. */
export function describeEvent(ev, executionMode) {
  const evType = ev.type || ev.event_type || '';
  const p = ev.payload;
  switch (evType) {
    case 'session.started': return `Sesión #${ev.session_id} iniciada (agente: ${p?.agent_name || 'CodingAgent'})`;
    case 'goal.created': return `Objetivo de la misión: ${p?.goal}`;
    case 'action.proposed': return `Paso ${p?.step || 1}: propone la acción «${p?.tool}» (${p?.operation || ''})`;
    case 'operation.classified':
      return `Operación clasificada: ${p?.category?.toUpperCase()} (riesgo: ${p?.risk_level?.toUpperCase()}, confianza: ${Math.round((p?.confidence || 0) * 100)}%)`;
    case 'evidence.evaluated':
      return `Evaluadas ${p?.evidence_count || 0} afirmaciones empíricas (fundamentación: ${Math.round((p?.grounding_score || 0.85) * 100)}%)`;
    case 'risk.assessed': return `Riesgo evaluado: ${p?.level || 'LOW'} (puntuación: ${p?.score || 0.15})`;
    case 'provider.evaluated': return `Evaluación semántica (${p?.provider_name}): puntuación ${p?.score} -> ${p?.verdict}`;
    case 'policy.decided': return `Política decidida: ${p?.status} (${p?.reason_code || 'STANDARD_POLICY'})`;
    case 'approval.requested': return `La política requiere confirmación humana (riesgo: ${p?.risk_level || 'HIGH'})`;
    case 'approval.completed': return `El operador decidió: ${p?.approved ? 'APROBADO' : 'RECHAZADO'}`;
    case 'capability.issued': return 'Token de capacidad emitido y firmado con HMAC';
    case 'execution.started':
      return executionMode === 'full_access'
        ? `Executing action on Host OS (Full Access): ${p?.tool}...`
        : `Executing action in local sandbox: ${p?.tool}...`;
    case 'execution.completed':
      return `Acción ejecutada ${p?.success ? 'correctamente' : 'con error'} en ${p?.execution_time_ms || 12} ms`;
    case 'observation.recorded': return `Observación: ${(p?.output || '').slice(0, 100)}…`;
    case 'decision.pruned': return `Decisión podada: ${p?.reason}`;
    case 'intervention.applied': return `[INTERVENCIÓN] ${p?.intervention || 'Supervisor'}: ${p?.message || ''}`;
    case 'workflow.node_started': return `[FLUJO] Nodo «${p?.node_name || p?.node_id}»: ${p?.agent_name || 'agente'} toma el control`;
    case 'workflow.node_completed': return `[FLUJO] Nodo «${p?.node_name || p?.node_id}» completado por ${p?.agent_name || 'agente'}`;
    case 'session.completed': return `Misión completada: ${p?.summary || 'terminó correctamente'}`;
    default: return `[${evType}]`;
  }
}

function levelOf(evType, ev) {
  if (evType.includes('error') || evType.includes('pruned') || evType.includes('blocked')) return 'ERROR';
  if (evType.includes('warn') || evType === 'approval.requested' || (evType === 'policy.decided' && ev.payload?.status === 'REVIEW')) return 'WARN';
  return 'INFO';
}

/**
 * Aplica UN evento del servidor al estado de la sesión y devuelve el estado nuevo.
 * `ctx.customModel`: modelo configurado por el operador, último recurso para rotular el modelo de una decisión.
 * `ctx.clock`: reloj inyectable para las pruebas (por defecto la hora local del evento).
 */
export function reduceEvent(state, ev, ctx = {}) {
  const evType = ev.type || ev.event_type || '';
  const timeStr = (ctx.clock || clockOf)(ev.timestamp || Date.now());
  let { session, nodes, decisionsMap, selectedNodeId, isRunning } = state;
  const sess0 = state.session;                                  // valores que el evento NO modifica: modo, workspace, modelo…
  const execMode = sess0.execution_mode;
  const maxSeq = typeof ev.sequence === 'number' && ev.sequence ? Math.max(state.maxSeq, ev.sequence) : state.maxSeq;

  const events = [
    {
      seq: ev.sequence,
      type: evType,
      time: timeStr,
      payload: ev.payload,
      detail: typeof ev.payload === 'string' ? ev.payload : JSON.stringify(ev.payload || {}).slice(0, 120),
    },
    ...state.events,
  ];
  const logs = [...state.logs, { time: timeStr, level: levelOf(evType, ev), message: describeEvent(ev, execMode) }];

  const patchDecision = (actId, fn) => {
    decisionsMap = { ...decisionsMap, [actId]: fn(decisionsMap[actId] || {}) };
  };
  const mapNodes = (fn) => { nodes = nodes.map(fn); };

  // 3a. Un nodo de workflow empieza: el agente toma el control ya, sin esperar a su primera acción
  if (evType === 'workflow.node_started') {
    const w = ev.payload || {};
    const pendingId = `${w.node_id}_i${w.iteration}_pending`;
    const prevNodes = nodes.map((n) => (n.wfNodeId === w.node_id && n.wfIteration < w.iteration && !n.blockDone ? { ...n, blockFailed: true } : n));
    if (prevNodes.some((n) => n.id === pendingId)) {
      nodes = prevNodes;
    } else {
      const parent = prevNodes.find((n) => n.id === w.parent_id) || [...prevNodes].reverse().find((n) => n.type !== 'start') || prevNodes[0];
      const handoff = Boolean(parent && parent.wfNodeId && (parent.wfNodeId !== w.node_id || parent.wfIteration !== w.iteration));
      const pending = {
        id: pendingId,
        label: 'Trabajando…',
        subtitle: `${w.node_name || w.node_id}${w.iteration > 1 ? ` · pasada ${w.iteration}` : ''}`,
        agentName: w.agent_name || null,
        agentRole: w.agent_role || null,
        wfNodeId: w.node_id,
        wfNodeName: w.node_name,
        wfIteration: w.iteration,
        handoff,
        handoffFrom: handoff ? parent.agentName : null,
        isPending: true,
        type: 'step',
        status: 'EXECUTING',
        x: 420,
        y: 105,
        parentId: parent ? parent.id : 'start',
        isNew: true,
      };
      nodes = computeTreeLayout([...prevNodes, pending]);
    }
  }

  // 3b. Procesamiento dinámico del árbol de decisiones en tiempo real
  if (evType === 'action.proposed') {
    const actId = ev.node_id;
    const tool = ev.payload?.tool || 'action';
    const op = ev.payload?.operation || tool;
    const stepNum = ev.payload?.step || 1;

    if (!nodes.some((n) => n.id === actId)) {
      // Calcular parentId canónico y posicionamiento jerárquico
      const rawParent = ev.parent_id || ev.payload?.parent_id;
      let resolvedParentId = 'start';
      if (rawParent) {
        if (rawParent.startsWith('root') || rawParent === 'start') resolvedParentId = 'start';
        else if (nodes.some((n) => n.id === rawParent)) resolvedParentId = rawParent;
        else if (nodes.length > 0) resolvedParentId = nodes[nodes.length - 1].id;
      }
      const wf = ev.payload?.workflow_node || null;               // nodo/agente del workflow que ejecuta este paso
      const parentNode = nodes.find((n) => n.id === resolvedParentId);
      const handoff = Boolean(wf && parentNode && parentNode.wfNodeId && (parentNode.wfNodeId !== wf.node_id || parentNode.wfIteration !== wf.iteration));
      const isHub = !wf && (op.includes('Propose') || stepNum === 5);
      const cleanOp = String(op).replace(/^\s*\d+\.\s*/, '');
      const newNode = {
        id: actId,
        label: `${stepNum}. ${cleanOp}`,
        subtitle: wf
          ? `${wf.node_name || wf.node_id}${wf.iteration > 1 ? ` · pasada ${wf.iteration}` : ''}`
          : `${timeStr} · LLM (${ev.payload?.source?.replace('LLM (', '').replace(')', '') || 'JEV'})`,
        agentName: wf?.agent_name || null,
        agentRole: wf?.agent_role || null,
        wfNodeId: wf?.node_id || null,
        wfNodeName: wf?.node_name || null,
        wfIteration: wf?.iteration || null,
        handoff,
        handoffFrom: handoff ? parentNode.agentName : null,
        type: isHub ? 'hub' : 'step',
        status: 'PENDING',
        action: op,
        x: 420,
        y: 105,
        parentId: resolvedParentId,
        isNew: true,
      };
      nodes = computeTreeLayout([
        ...nodes.filter((n) => !(n.isPending && wf && n.wfNodeId === wf.node_id && n.wfIteration === wf.iteration)),
        newNode,
      ]);
    }

    // Auto-seleccionar el nodo actual para que el inspector se actualice en tiempo real
    selectedNodeId = actId;

    // Estado de la sesión en el encabezado
    session = { ...session, status: `Active (Paso ${stepNum})` };

    // Borrador de decisión para inspección inmediata en las pestañas
    const effMode = sess0.execution_mode || 'local_restricted';
    const effIso = effMode === 'full_access' ? 'None (Host OS)' : 'Active';
    const effBack = effMode === 'full_access' ? 'FullAccessExecutor' : (effMode === 'container' ? 'IsolatedSandbox' : 'LocalProcessSandbox');
    const effNet = effMode === 'full_access' ? 'Host Direct' : 'Isolated (Restricted)';
    const effWs = sess0.workspace_root || sess0.working_directory || (effMode === 'full_access' ? '.' : '/workspace');
    const effModel = sess0.decision_model_effective?.model_id || sess0.decisionModel?.model_id || sess0.llm_model || ctx.customModel || 'qwen2.5-coder:7b';
    const effProvider = ev.payload?.source || sess0?.decisionModel?.provider || sess0?.runtime?.provider || 'JEV';

    decisionsMap = {
      ...decisionsMap,
      [actId]: {
        decisionId: ev.decision_id || `d_${actId}`,
        sequence: stepNum,
        status: 'PENDING',
        actionCommand: `${tool} ${JSON.stringify(ev.payload?.arguments || {})}`,
        tool,
        provider: effProvider,
        model: effModel,
        execution_mode: effMode,
        isolation: effIso,
        execution_backend: effBack,
        network_mode: effNet,
        working_directory: effWs,
        riskLevel: 'LOW',
        riskScore: 0.15,
        semanticEvaluation: [
          { provider: 'LAYA', score: 0.85, verdict: 'ALLOW' },
          { provider: 'TypeSafe', score: 0.78, verdict: 'ALLOW' },
        ],
        policyDecision: {
          status: 'Evaluating',
          requiresConfirmation: false,
          rulesActivated: ['PathContainment'],
          reasonCodes: ['EVALUATING'],
          precedence: 'Deterministic Safety Precedence',
        },
        capability: { issued: false, statusText: 'Evaluating...', token: null },
        reason: ev.payload?.thought_rationale || 'Evaluando paso en el pipeline...',
        evidenceTab: { groundingScore: 0.85, claims: ['Analyzing repository structure and code contracts.'], claimCount: 1, freshness: 'live' },
        receiptTab: {
          decisionId: ev.decision_id || `d_${actId}`,
          actionHash: `sha256:${actId}`,
          stateHash: `sha256:state_${actId}`,
          nonce: `non_${actId}`,
        },
        decisionTab: {
          decision_id: ev.decision_id || `d_${actId}`,
          session_id: sess0.sessionId,
          action_id: actId,
          tool,
          arguments: ev.payload?.arguments || {},
          status: 'PENDING',
          provider: effProvider,
          model: effModel,
          execution_mode: effMode,
          isolation: effIso,
          execution_backend: effBack,
          network_mode: effNet,
          working_directory: effWs,
          risk_level: 'LOW',
          risk_score: 0.15,
          reason: ev.payload?.thought_rationale || 'Evaluando paso en el pipeline...',
        },
        relatedDecisions: [],
      },
    };
  }

  if (evType === 'operation.classified') {
    const opAssessment = ev.payload || {};
    const opCategory = (opAssessment.category || 'inspection').toLowerCase();
    patchDecision(ev.node_id, (current) => ({
      ...current,
      operation_category: opCategory,
      operation_assessment: opAssessment,
      commandClassification: {
        category: opCategory,
        confidence: opAssessment.confidence ?? 1.0,
        readOnly: !!opAssessment.is_read_only,
        reversible: !!opAssessment.is_reversible,
        networkAccess: !!opAssessment.requires_network,
        matchedRule: opAssessment.matched_rule || 'deterministic',
        explanation: opAssessment.explanation || '',
      },
    }));
  }

  if (evType === 'evidence.evaluated') {
    const groundingScore = ev.payload?.grounding_score ?? 0.85;
    const claims = ev.payload?.claims_evaluated || [];
    const claimCount = ev.payload?.evidence_count ?? claims.length;
    patchDecision(ev.node_id, (current) => ({
      ...current,
      evidenceTab: {
        ...current.evidenceTab,
        groundingScore,
        claims: claims.length > 0 ? claims : ['Empirical grounding validated against current state.'],
        claimCount,
        freshness: 'live',
      },
    }));
  }

  if (evType === 'provider.evaluated') {
    patchDecision(ev.node_id, (current) => ({
      ...current,
      semanticEvaluation: [
        { provider: ev.payload?.provider_name || 'LAYA', score: ev.payload?.score ?? 0.88, verdict: ev.payload?.verdict || 'ALLOW' },
        { provider: 'TypeSafe', score: 0.82, verdict: 'ALLOW' },
      ],
    }));
  }

  if (evType === 'risk.assessed') {
    const rLevel = ev.payload?.level || 'LOW';
    const rScore = ev.payload?.score ?? 0.15;
    patchDecision(ev.node_id, (current) => ({ ...current, riskLevel: rLevel, riskScore: rScore, reason: ev.payload?.reasons?.[0] || current.reason }));
  }

  if (evType === 'policy.decided') {
    const actId = ev.parent_id || ev.node_id;
    const statusStr = ev.payload?.status || 'ALLOW';
    const requiresConf = !!ev.payload?.requires_confirmation;

    // Estado visual del nodo en el árbol
    mapNodes((n) => (n.id === actId ? { ...n, status: statusStr === 'REVIEW' ? 'REVIEW' : statusStr === 'BLOCK' ? 'BLOCK' : 'ALLOW' } : n));

    // Contadores de métricas de la sesión
    const m = session.metrics || { totalDecisions: 0, allowed: 0, blocked: 0, review: 0 };
    session = {
      ...session,
      status: statusStr === 'REVIEW' ? 'Review Required' : session.status,
      metrics: {
        totalDecisions: m.totalDecisions + 1,
        allowed: statusStr === 'ALLOW' ? m.allowed + 1 : m.allowed,
        blocked: statusStr === 'BLOCK' ? m.blocked + 1 : m.blocked,
        review: statusStr === 'REVIEW' ? m.review + 1 : m.review,
      },
    };

    // Datos de la decisión en el mapa
    patchDecision(actId, (current) => {
      const curTab = current.decisionTab || {};
      const effMode = ev.payload?.execution_mode || current.execution_mode || sess0.execution_mode || 'local_restricted';
      const effIso = effMode === 'full_access' ? 'None (Host OS)' : (current.isolation || 'Active');
      const effBack = effMode === 'full_access' ? 'FullAccessExecutor' : (current.execution_backend || 'LocalProcessSandbox');
      return {
        ...current,
        status: statusStr,
        execution_mode: effMode,
        isolation: effIso,
        execution_backend: effBack,
        policyDecision: {
          status: requiresConf ? 'Requires confirmation' : 'Authorized by policy',
          requiresConfirmation: requiresConf,
          rulesActivated: requiresConf ? ['EgressPolicy', 'PathContainment'] : ['StandardPolicy'],
          reasonCodes: [ev.payload?.reason_code || 'POLICY_EVALUATED'],
          precedence: 'Deterministic Safety Precedence',
        },
        decisionTab: { ...curTab, status: statusStr, execution_mode: effMode, isolation: effIso, execution_backend: effBack },
      };
    });

    // Si requiere revisión o es bloqueado, se enfoca ese nodo en el inspector
    if (statusStr === 'REVIEW' || statusStr === 'BLOCK') selectedNodeId = actId;
  }

  if (evType === 'capability.issued') {
    const actId = ev.node_id;
    const cap = ev.payload || {};
    patchDecision(actId, (current) => ({
      ...current,
      capability: { issued: true, statusText: 'Issued & HMAC Signed', token: cap.capability_id || cap.signature || 'cap_hmac_verified' },
      receiptTab: {
        decisionId: cap.decision_id || current.decisionId,
        actionHash: cap.action_hash || current.receiptTab?.actionHash || `sha256:${actId}`,
        stateHash: cap.state_hash || current.receiptTab?.stateHash || `sha256:state_${actId}`,
        nonce: cap.nonce || current.receiptTab?.nonce || `non_${actId}`,
      },
    }));
  }

  if (evType === 'approval.completed' && ev.payload?.approved) {
    const actId = ev.node_id;
    mapNodes((n) => (n.id === actId ? { ...n, status: 'ALLOW' } : n));
    const m = session.metrics;
    session = { ...session, metrics: { ...m, allowed: m.allowed + 1, review: Math.max(0, m.review - 1) } };
  }

  if (evType === 'execution.started') {
    const actId = ev.node_id;
    const isFull = execMode === 'full_access';
    mapNodes((n) => (n.id === actId ? { ...n, status: 'EXECUTING' } : n));
    patchDecision(actId, (current) => {
      const curTab = current.decisionTab || {};
      return {
        ...current,
        execution_backend: isFull ? 'FullAccessExecutor' : (current.execution_backend || 'LocalProcessSandbox'),
        isolation: isFull ? 'None (Host OS)' : (current.isolation || 'Active'),
        decisionTab: {
          ...curTab,
          execution_backend: isFull ? 'FullAccessExecutor' : (curTab.execution_backend || 'LocalProcessSandbox'),
          isolation: isFull ? 'None (Host OS)' : (curTab.isolation || 'Active'),
        },
      };
    });
  }

  if (evType === 'execution.completed') {
    const actId = ev.node_id;
    const success = ev.payload?.success;
    const timeMs = ev.payload?.execution_time_ms;
    nodes = computeTreeLayout(nodes.map((n) => (n.id === actId ? { ...n, status: success ? 'ALLOW' : 'BLOCK' } : n)));
    if (timeMs) session = { ...session, runtime: { ...session.runtime, latencyP50: `${timeMs}ms` } };
  }

  if (evType === 'decision.pruned') {
    const actId = ev.node_id;
    nodes = computeTreeLayout(nodes.map((n) => (n.id === actId ? { ...n, status: 'BLOCK', subtitle: 'Podado (Pruned)' } : n)));
    patchDecision(actId, (current) => ({ ...current, status: 'BLOCK', reason: ev.payload?.reason || current.reason }));
  }

  if (evType === 'intervention.applied') {
    const failedNode = ev.payload?.failed_node;
    if (failedNode) nodes = computeTreeLayout(nodes.map((n) => (n.id === failedNode ? { ...n, status: 'BLOCK' } : n)));
  }

  if (evType === 'approval.requested' && ev.payload?.kind === 'node_failure') {
    mapNodes((n) => (n.wfNodeId === ev.node_id && !n.blockDone ? { ...n, blockFailed: true } : n));
  }

  if (evType === 'workflow.node_completed') {
    const w = ev.payload || {};
    mapNodes((n) => {
      if (n.wfNodeId !== w.node_id || n.wfIteration !== w.iteration) return n;
      return n.isPending ? { ...n, label: 'Completado', status: 'ALLOW', isPending: false, blockDone: true } : { ...n, blockDone: true };
    });
  }

  if (evType === 'observation.recorded') {
    const actId = ev.node_id;
    const output = ev.payload?.output || '';
    patchDecision(actId, (current) => ({ ...current, observationOutput: output }));
    // El finish de un nodo de workflow (ids «nodo_iN_act_M») no cierra la misión: es un hito del nodo
    const isWorkflowStep = /_i\d+_act_\d+$/.test(actId || '');
    // Solo el cierre explícito del agente («Tarea concluida: …», lo que devuelve `finish`) es la respuesta de la misión:
    // buscar «conclu»/«finaliz» en cualquier salida tomaba por respuesta el contenido de un archivo leído.
    if (!isWorkflowStep && /^\s*Tarea concluida:/.test(output)) {
      session = { ...session, status: 'Completed', finalAnswer: output.replace(/^\s*Tarea concluida:/, '').trim() };
    }
  }

  if (evType === 'session.completed') {
    session = {
      ...session,
      status: 'Completed',
      finalAnswer: ev.payload?.summary || session.finalAnswer,
      runtime: { ...session.runtime, executionTime: 'Completed' },
    };
    isRunning = false;
  }

  return { ...state, session, nodes, decisionsMap, selectedNodeId, isRunning, events, logs, maxSeq };
}
