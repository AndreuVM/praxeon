// Aprobaciones pendientes y avisos por panel (FE-008).

/** Modelo con el que decide el agente de la sesión (el efectivo del servidor o el configurado). */
export function modelOf(session) {
  return session.decision_model_effective?.model_id || session.decisionModel?.model_id || session.llm_model || '';
}

/** Decisiones que la política retiene para que las resuelva el operador (RD-6.3.3). Solo cuentan con la misión en marcha. */
export function pendingApprovals(state) {
  if (!state.isRunning) return [];
  return Object.entries(state.decisionsMap)
    .filter(([, d]) => d.status === 'REVIEW' && !d.capability?.issued)
    .map(([nodeId, d]) => ({
      nodeId,
      decisionId: d.decisionId || `d_${nodeId}`,
      tool: d.tool,
      command: d.actionCommand,
      riskLevel: d.riskLevel,
      laya: d.semanticEvaluation?.[0]?.score,
    }));
}

/** Cola global: las aprobaciones de todos los paneles, cada una con su panel, para no perder una en un panel sin foco. */
export function approvalQueue(panels) {
  return panels.flatMap((panel) => pendingApprovals(panel.state).map((a) => ({ ...a, panelId: panel.id, panelLabel: panel.label })));
}

/** Último conflicto de escritura (FE-009) de la sesión que no se haya descartado. `events` va de más reciente a más antiguo. */
export function latestConflict(events, dismissedSeq = 0) {
  const ev = events.find((e) => e.type === 'workspace.file_conflict' && (e.seq ?? 0) > dismissedSeq);
  if (!ev) return null;
  const p = ev.payload && typeof ev.payload === 'object' ? ev.payload : {};
  return {
    seq: ev.seq ?? 0,
    path: p.path || '',
    by: p.conflicting_session || null,
    message: p.message || `Otra sesión modificó '${p.path || 'un archivo'}'`,
  };
}

/** Eventos que merecen avisar al operador cuando ocurren en un panel que no tiene el foco. */
export function noticeFor(ev) {
  const type = ev.type || ev.event_type || '';
  if (type === 'approval.requested') return { kind: 'approval', text: 'pide tu aprobación' };
  if (type === 'workspace.file_conflict') return { kind: 'conflict', text: `conflicto de escritura en ${ev.payload?.path || 'un archivo'}` };
  if (type === 'session.completed') return { kind: 'done', text: 'ha terminado' };
  return null;
}

/** Estado visible de un panel: en curso, esperando aprobación, pausado, terminado o listo. */
export function panelStatus(state) {
  if (state.isRunning && pendingApprovals(state).length > 0) return { key: 'approval', label: 'Esperando aprobación' };
  if (state.isRunning) return { key: state.isPaused ? 'paused' : 'running', label: state.isPaused ? 'Pausado' : 'En curso' };
  const status = String(state.session?.status || '').toLowerCase();
  if (status === 'completed') return { key: 'done', label: 'Terminado' };
  if (status === 'ready' || status === '') return { key: 'idle', label: 'Listo' };
  return { key: 'idle', label: 'Detenido' };
}
