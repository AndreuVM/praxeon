// Estado del chat (razonamiento, hitos, agente en control, respuesta final) derivado del flujo de eventos de una sesión.
// Lo usa el chat en vivo y también la reconstrucción de sesiones antiguas que se vuelven a abrir.

/** @param {Array} eventsOldestFirst eventos ordenados del más antiguo al más reciente */
export function deriveChatState(eventsOldestFirst) {
  const thoughts = [];
  const milestones = [];
  const running = new Map(); // nodos de workflow empezados y aún sin terminar
  let currentThought = '';
  let finalAnswer = null;
  let completed = false;

  for (const ev of eventsOldestFirst) {
    const payload = ev.payload || {};
    if (ev.type === 'action.proposed') {
      const stepNum = payload.step || thoughts.length + 1;
      const rationale = payload.thought_rationale || payload.detail || '';
      if (rationale) currentThought = rationale;
      thoughts.push({
        step: stepNum,
        tool: payload.tool && payload.tool !== 'action' ? payload.tool : payload.operation || 'acción',
        operation: payload.operation || `${stepNum}. ${payload.tool || 'acción'}`,
        thought: rationale || (payload.tool ? `Paso ${stepNum}: Proponiendo ${payload.tool}` : `Paso ${stepNum}: Proponiendo acción`),
        verdict: 'PENDING',
        source: payload.source || 'LLM',
        agentName: payload.workflow_node?.agent_name || null,
        nodeName: payload.workflow_node?.node_name || null,
      });
    } else if (ev.type === 'workflow.node_started') {
      running.set(`${payload.node_id}#${payload.iteration}`, payload);
    } else if (ev.type === 'workflow.node_completed') {
      running.delete(`${payload.node_id}#${payload.iteration}`);
      milestones.push({
        key: ev.event_id || `${payload.node_id}-${payload.iteration}`,
        nodeName: payload.node_name || payload.node_id,
        agentName: payload.agent_name,
        agentRole: payload.agent_role,
        iteration: payload.iteration,
        steps: payload.steps,
        summary: payload.summary || '',
      });
    } else if (ev.type === 'provider.evaluated' && thoughts.length > 0) {
      const last = thoughts[thoughts.length - 1];
      last.score = payload.score;
      last.verdict = payload.verdict || last.verdict;
      last.provider = payload.provider_name;
    } else if (ev.type === 'policy.decided' && thoughts.length > 0) {
      const last = thoughts[thoughts.length - 1];
      last.verdict = payload.status || last.verdict;
      last.reason = payload.reason_code;
    } else if (ev.type === 'execution.completed' && thoughts.length > 0) {
      const last = thoughts[thoughts.length - 1];
      last.executionOutput = payload.output;
      last.executionTimeMs = payload.execution_time_ms;
    } else if (ev.type === 'intervention.applied' && thoughts.length > 0) {
      const last = thoughts[thoughts.length - 1];
      last.verdict = payload.intervention || 'BACKTRACK';
      last.interventionMessage = payload.message;
    } else if (ev.type === 'session.completed') {
      completed = true;
      if (payload.summary) finalAnswer = payload.summary;
    }
  }

  return {
    thoughts,
    milestones,
    activeNode: [...running.values()].pop() || null,
    currentThought,
    finalAnswer,
    completed,
  };
}

/** Mensajes del chat de una sesión reconstruidos desde el backend: la tarea y la respuesta del agente. */
export function rebuildChatFromSession({ goal, events, finalAnswer, createdAt }) {
  const state = deriveChatState(events);
  const time = createdAt ? new Date(createdAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : 'Histórico';
  const messages = [];
  if (goal) messages.push({ id: `rebuilt-user-${createdAt || goal.length}`, role: 'user', text: goal, time });
  if (state.thoughts.length > 0 || state.finalAnswer || finalAnswer) {
    messages.push({
      id: `rebuilt-assistant-${createdAt || goal?.length || 0}`,
      role: 'assistant',
      status: 'completed',
      time,
      thoughts: state.thoughts,
      milestones: state.milestones,
      currentThought: state.currentThought,
      finalAnswer: state.finalAnswer || finalAnswer || null,
    });
  }
  return messages;
}
