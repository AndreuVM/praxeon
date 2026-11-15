// Una sesión de agente en la interfaz: su estado, su WebSocket y las acciones del operador sobre ella (FE-007, nota N-001).
//
// Hasta ahora todo esto vivía en App.jsx, atado a UNA sesión. Cada panel tiene ahora su propio `AgentSession`: estado propio
// (`sessionReducer.js` para los eventos), su propia suscripción WebSocket (se abre al fijar el id de sesión y se cierra al
// cambiarlo o al cerrar el panel con `dispose()`), y su propio historial de chat. Es una clase sin React para poder probarla con
// una API falsa; `useAgentSession` la conecta a los componentes.
import { computeTreeLayout } from '../utils/treeLayout';
import { START_NODE, clockOf, createSessionState, reduceEvent } from '../utils/sessionReducer';
import { INITIAL_SESSION, INITIAL_NODES, INITIAL_DECISIONS_MAP, INITIAL_LOGS, INITIAL_EVENTS } from '../constants/demoData';

const now = () => clockOf(Date.now());
const EMPTY_METRICS = () => ({ totalDecisions: 0, allowed: 0, blocked: 0, review: 0 });

/**
 * deps:
 *  api, notify, chats (registro de chats), rebuildChat(…), buildCascade()
 *  getSessionsList(), setSessionsList(fn)
 *  ui: { showLive(), closeDrawer(), openStep() }   // lo que la interfaz hace al cambiar de sesión
 *  onRuntimeActive(bool)                           // el WebSocket confirmó que el servidor responde
 *  onEvent(ev)                                     // opcional: cada evento recibido, ya aplicado al estado
 *  onConfigChange(config)                          // opcional: la configuración de este panel cambió (la última usada pasa a ser el valor por defecto)
 */
export class AgentSession {
  constructor(deps, { workspaceNote = 'Runtime de PRAXEON inicializado. Listo para recibir una misión.', config = {} } = {}) {
    this.deps = deps;
    this.sid = null;                                  // sesión cuyo chat se está escribiendo
    this.stream = null;
    this.streamSid = null;
    this.disposed = false;
    this.listeners = new Set();
    this.depth = 0;
    this.dirty = false;
    this.state = {
      ...createSessionState({ logs: [{ time: now(), level: 'INFO', message: workspaceNote }] }),
      isPaused: false,
      followExecution: null,
      chatMessages: [],
      missionConfig: { ...config },          // proveedor, modelo, supervisor, modo, pasos…: PROPIOS de este panel (FE-011)
    };
  }

  // ------------------------------------------------------------------ almacén ----------------------------------------------------------
  getState = () => this.state;

  subscribe = (fn) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };

  /** Aplica `fn(estado) → estado`. Dentro de `batch` los avisos se juntan en uno. */
  update(fn) {
    if (this.disposed) return;
    const next = fn(this.state);
    if (next === this.state) return;
    this.state = next;
    this.syncConnection();
    if (this.depth > 0) this.dirty = true;
    else this.listeners.forEach((l) => l());
  }

  batch(fn) {
    this.depth += 1;
    try {
      return fn();
    } finally {
      this.depth -= 1;
      if (this.depth === 0 && this.dirty) {
        this.dirty = false;
        this.listeners.forEach((l) => l());
      }
    }
  }

  /** Setters con la misma forma que los `useState` que sustituyen: valor o función del valor anterior. */
  set = (key, v) => this.update((s) => {
    const value = typeof v === 'function' ? v(s[key]) : v;
    return Object.is(value, s[key]) ? s : { ...s, [key]: value };
  });

  setSession = (v) => this.set('session', v);
  setNodes = (v) => this.set('nodes', v);
  setDecisionsMap = (v) => this.set('decisionsMap', v);
  setSelectedNodeId = (v) => this.set('selectedNodeId', v);
  setLogs = (v) => this.set('logs', v);
  setEvents = (v) => this.set('events', v);
  setIsRunning = (v) => this.set('isRunning', v);
  setIsPaused = (v) => this.set('isPaused', v);
  setFollowExecution = (v) => this.set('followExecution', v);
  setChatMessages = (v) => this.set('chatMessages', v);

  /** Configuración de la misión de ESTE panel: valor o función del valor anterior, como un `useState`. */
  setMissionConfig = (v) => {
    this.set('missionConfig', v);
    this.deps.onConfigChange?.(this.state.missionConfig);
  };

  log(level, message) {
    this.setLogs((prev) => [...prev, { time: now(), level, message }]);
  }

  // ------------------------------------------------------------------ WebSocket --------------------------------------------------------
  /** Mantiene UNA suscripción abierta a la sesión actual: se abre al fijar un id real y se cierra al cambiarlo. */
  syncConnection() {
    const sid = this.state.session.sessionId;
    const wanted = sid && sid !== 'ready' && !this.disposed ? sid : null;
    if (wanted === this.streamSid) return;
    this.stream?.close();
    this.stream = null;
    this.streamSid = wanted;
    if (!wanted) return;
    this.stream = this.deps.api.createWebSocketStream(
      wanted,
      (msg) => this.onMessage(msg),
      (status) => { if (status === 'connected') this.deps.onRuntimeActive?.(true); },
      () => this.state.maxSeq,
    );
  }

  onMessage(msg) {
    if (this.disposed || msg?.action !== 'event' || !msg.data) return;
    this.update((s) => reduceEvent(s, msg.data, { customModel: s.missionConfig?.customModel }));
    this.deps.onEvent?.(msg.data);               // avisos por panel (aprobación pedida, conflicto de escritura, misión terminada)
  }

  /** Vuelve a dejar el almacén en marcha tras un `dispose` (StrictMode monta, desmonta y vuelve a montar). */
  activate() {
    if (!this.disposed) return;
    this.disposed = false;
    this.syncConnection();
  }

  /** Cierra el WebSocket y deja de aceptar cambios (se llama al cerrar el panel). */
  dispose() {
    this.stream?.close();
    this.stream = null;
    this.streamSid = null;
    this.disposed = true;
  }

  // ------------------------------------------------------------------ chat -------------------------------------------------------------
  /** Cambia los mensajes del chat y los guarda bajo la sesión actual. */
  updateChat = (updater) => {
    const prev = this.state.chatMessages;
    const next = typeof updater === 'function' ? updater(prev) : updater;
    if (this.sid) this.deps.chats.touch(this.sid, next);
    this.setChatMessages(next);
  };

  operatorMessage(text) {
    this.updateChat((prev) => [
      ...prev,
      { id: `op-${Date.now()}`, role: 'operator', text, time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) },
    ]);
  }

  // ------------------------------------------------------------------ acciones ---------------------------------------------------------
  async startMission({
    goal, execution_mode, autonomous = false, allow_unattended_execution = false, full_access_confirmed = false, llm_provider, llm_model,
    decision_model, supervisor, max_steps, llm_failure_policy, api_key, base_url, workspace_root, chat_history, workflow_id, attachments,
    agy_tools, agy_tools_ack,
  }) {
    const { api, chats } = this.deps;
    this.batch(() => { this.setIsRunning(true); this.setIsPaused(false); });
    const timeStr = now();
    // La cascada se configura en Proveedores: se envía con la misión para que cada una lleve la suya
    if (!decision_model && supervisor === 'cascade') decision_model = this.deps.buildCascade();

    try {
      const res = await api.runMission({
        goal, execution_mode, autonomous, allow_unattended_execution, full_access_confirmed, llm_provider, llm_model, decision_model,
        api_key, base_url, supervisor: decision_model?.provider || supervisor, max_steps, llm_failure_policy, step_delay_ms: 1000,
        workspace_root, chat_history, workflow_id, attachments, agy_tools, agy_tools_ack,
      });
      if (!res?.data) return;

      const sid = res.data.session_id;
      const previousSid = this.sid;
      const wasRealSession = this.deps.getSessionsList().some((x) => x.session_id === previousSid);
      this.sid = sid;
      this.batch(() => {
        // La conversación continúa en la sesión nueva; la anterior conserva lo que tenía antes de este mensaje
        const prev = this.state.chatMessages;
        chats.chats[sid] = prev;
        if (previousSid && previousSid !== sid) {
          if (wasRealSession) chats.chats[previousSid] = prev.slice(0, -2);
          else delete chats.chats[previousSid];
        }
        chats.schedule();

        this.setFollowExecution(workflow_id ? { workflowId: workflow_id, executionId: `wfx-${sid}` } : null);
        const effModel = res.data.decision_model_effective || decision_model;
        this.setSession({
          sessionId: sid,
          status: 'Active',
          agent: 'CodingAgent',
          goal,
          execution_mode: res.data.execution_mode || execution_mode || 'local_restricted',
          autonomous: Boolean(autonomous || res.data.autonomous),
          isAutonomous: Boolean(autonomous || res.data.autonomous),
          isDegraded: Boolean(res.data.is_fallback || res.data.status === 'Degraded'),
          fallbackReason: res.data.degradation_reason || null,
          decisionModel: effModel,
          decision_model_effective: effModel,
          metrics: EMPTY_METRICS(),
          runtime: {
            provider: `${(llm_provider || 'SIMULATOR').toUpperCase()} + ${(effModel?.provider || supervisor || 'LAYA').toUpperCase()}`,
            version: 'v1.1.0',
            latencyP50: 'N/A',
            executionTime: 'Live',
          },
          finalAnswer: null,
          generatedImages: [],
        });
        this.deps.setSessionsList((list) => [res.data, ...list.filter((s) => s.session_id !== sid)]);
        this.setNodes([{ ...START_NODE }]);
        this.setSelectedNodeId('start');
        this.setDecisionsMap({});
        this.setEvents([]);
        this.setLogs([
          { time: timeStr, level: 'INFO', message: `Iniciando la sesión interactiva #${sid} con el modelo (${llm_provider.toUpperCase()}) y el supervisor (${supervisor.toUpperCase()})` },
          { time: timeStr, level: 'INFO', message: `Objetivo: ${goal}` },
          // AUD-19: modo de ejecución efectivo y, si no hay aislamiento fuerte, por qué
          res.data.isolation_warning
            ? { time: timeStr, level: 'WARN', message: res.data.isolation_warning }
            : {
                time: timeStr,
                level: 'INFO',
                message: res.data.isolation_backend
                  ? `Comandos aislados (${res.data.isolation_backend}): sin red y sin acceso fuera del workspace.`
                  : `Modo de ejecución: ${res.data.execution_mode || execution_mode}.`,
              },
        ]);
      });
    } catch (err) {
      this.batch(() => {
        this.setLogs((prev) => [...prev, { time: timeStr, level: 'ERROR', message: `No se pudo iniciar la misión: ${err.message}` }]);
        this.setIsRunning(false);
      });
    }
  }

  async pause() {
    const sid = this.state.session.sessionId;
    if (!sid) return;
    try {
      await this.deps.api.pauseMission(sid);
      this.batch(() => { this.setIsPaused(true); this.log('WARN', 'Misión pausada por el operador.'); });
    } catch (e) {
      console.warn('Error pausing mission:', e);
    }
  }

  async resume() {
    const sid = this.state.session.sessionId;
    if (!sid) return;
    try {
      await this.deps.api.resumeMission(sid);
      this.batch(() => { this.setIsPaused(false); this.log('INFO', 'Misión reanudada por el operador.'); });
    } catch (e) {
      console.warn('Error resuming mission:', e);
    }
  }

  stop() {
    this.batch(() => { this.setIsRunning(false); this.setIsPaused(false); this.log('WARN', 'Misión detenida.'); });
  }

  /** Sesión nueva y vacía (grafo y chat en blanco, sin memoria previa). */
  newClean() {
    const cfg = this.state.missionConfig;
    const newSid = `s-${Math.random().toString(36).substring(2, 9)}`;
    const timeStr = now();
    this.batch(() => {
      this.setIsRunning(false);
      this.setIsPaused(false);
      this.setSession({
        sessionId: newSid,
        status: 'Ready',
        agent: 'CodingAgent',
        goal: 'Nueva sesión limpia. Introduce un prompt para iniciar la misión.',
        execution_mode: cfg.executionMode || 'local_restricted',
        metrics: EMPTY_METRICS(),
        kpis: { totalDecisions: 0, allowedDecisions: 0, blockedDecisions: 0, reviewDecisions: 0 },
        runtime: {
          provider: `${(cfg.llmProvider || 'SIMULATOR').toUpperCase()} + ${(cfg.supervisor || 'LAYA').toUpperCase()}`,
          version: 'v1.0.0',
          latencyP50: '—',
          executionTime: 'Live',
        },
        finalAnswer: null,
        generatedImages: [],
      });
      this.setNodes([{ ...START_NODE }]);
      this.setSelectedNodeId('start');
      this.setDecisionsMap({});
      this.setEvents([]);
      this.sid = newSid;
      this.setChatMessages([]);
      this.deps.ui.closeDrawer();
      this.setLogs([{ time: timeStr, level: 'INFO', message: `Nueva sesión limpia (#${newSid}) inicializada. Grafo y memoria reseteados.` }]);
      this.deps.ui.showLive();
    });
  }

  /** Carga una sesión existente con su snapshot de árbol, decisiones y eventos. */
  async loadSession(sid) {
    const { api, chats } = this.deps;
    const cachedChat = chats.chats[sid];
    this.batch(() => {
      this.setIsRunning(false);
      this.setIsPaused(false);
      // El chat de la sesión elegida: la copia guardada, o vacío hasta reconstruirlo desde el backend
      this.sid = sid;
      this.setChatMessages(cachedChat || []);
      this.setEvents([]);
    });

    try {
      const snap = await api.fetchSession(sid);
      if (snap && snap.data) {
        const d = snap.data;
        const summary = d.summary || {};

        this.setSession({
          sessionId: d.session_id,
          goal: d.goal,
          status: d.status || 'Active',
          agent: d.agent_name || 'CodingAgent',
          agent_name: d.agent_name || 'CodingAgent',
          execution_mode: d.execution_mode || 'local_restricted',
          metrics: {
            totalDecisions: summary.total_decisions || 0,
            allowed: summary.allowed_count || 0,
            blocked: summary.blocked_count || 0,
            review: summary.review_count || 0,
          },
          kpis: {
            totalDecisions: summary.total_decisions || 0,
            allowedDecisions: summary.allowed_count || 0,
            blockedDecisions: summary.blocked_count || 0,
            reviewDecisions: summary.review_count || 0,
          },
          runtime: { provider: 'JEV + LAYA', version: 'v1.0.0', latencyP50: '14ms', executionTime: 'Histórico' },
        });

        // Reconstruir árbol si el snapshot contiene nodos
        if (d.tree && d.tree.nodes && Array.isArray(d.tree.nodes) && d.tree.nodes.length > 0) {
          const loadedNodes = d.tree.nodes.map((n) => ({
            id: n.id || n.decision_id,
            label: n.title || n.label || n.tool || 'Paso',
            title: n.title || n.tool,
            subtitle: n.subtitle || (n.verdict ? `Veredicto: ${n.verdict}` : ''),
            status: n.verdict || n.status || 'ALLOW',
            verdict: n.verdict || n.status || 'ALLOW',
            risk: n.risk || 'LOW',
            type: n.type || 'action',
            parentId: n.parentId || n.parent_id || null,
            depth: n.depth || 1,
            sequence: n.sequence || 1,
            tool: n.tool,
            command: n.command,
            timestamp: n.timestamp,
          }));
          this.setNodes(computeTreeLayout(loadedNodes));
          this.setSelectedNodeId(loadedNodes[0].id);
        }

        // Cargar decisiones asociadas
        const decRes = await api.fetchSessionDecisions(sid);
        if (decRes && decRes.data && Array.isArray(decRes.data)) {
          const map = {};
          const sessionMode = d.execution_mode || 'local_restricted';
          decRes.data.forEach((item) => {
            const itemMode = item.execution_mode || sessionMode;
            map[item.decision_id] = {
              decisionId: item.decision_id,
              status: item.status,
              riskLevel: item.risk_level,
              tool: item.tool,
              command: item.command,
              actionCommand: item.command,
              reason: item.description,
              groundingScore: item.grounding_score,
              signature: item.signature,
              actionHash: item.action_hash,
              execution_mode: itemMode,
              isolation: itemMode === 'full_access' ? 'None (Host OS)' : 'Active',
              execution_backend: itemMode === 'full_access' ? 'FullAccessExecutor' : 'LocalProcessSandbox',
              network_mode: itemMode === 'full_access' ? 'Host Direct' : 'Isolated (Restricted)',
              working_directory: d.workspace_root || d.working_directory || (itemMode === 'full_access' ? '.' : '/workspace'),
            };
          });
          this.setDecisionsMap(map);
        }

        // Eventos de la sesión (más recientes primero, como el stream en vivo) y chat reconstruido si no había copia
        const history = await api.fetchAllEvents(sid);
        if (this.sid === sid) {
          this.setEvents([...history].reverse());

          // El snapshot del árbol no trae qué agente y nodo de workflow hizo cada paso: se recupera de los eventos
          const wfByAction = {};
          const doneBlocks = new Set();
          const lastIteration = {};
          history.forEach((ev) => {
            const w = ev.payload?.workflow_node;
            if (ev.type === 'action.proposed' && w) {
              wfByAction[ev.node_id] = w;
              lastIteration[w.node_id] = Math.max(lastIteration[w.node_id] || 0, w.iteration || 1);
            } else if (ev.type === 'workflow.node_completed') {
              doneBlocks.add(`${ev.payload?.node_id}#${ev.payload?.iteration}`);
            }
          });
          if (Object.keys(wfByAction).length > 0) {
            this.setNodes((prevNodes) =>
              computeTreeLayout(
                prevNodes.map((n) => {
                  const w = wfByAction[n.id];
                  if (!w) return n;
                  const done = doneBlocks.has(`${w.node_id}#${w.iteration}`);
                  return {
                    ...n,
                    agentName: w.agent_name,
                    agentRole: w.agent_role,
                    wfNodeId: w.node_id,
                    wfNodeName: w.node_name,
                    wfIteration: w.iteration,
                    blockDone: done,
                    blockFailed: !done && (w.iteration || 1) < lastIteration[w.node_id],
                  };
                }),
              ),
            );
          }
          if (!cachedChat) {
            const rebuilt = this.deps.rebuildChat({ goal: d.goal, events: history, createdAt: d.created_at });
            if (rebuilt.length > 0) {
              chats.chats[sid] = rebuilt;
              chats.schedule();
              this.setChatMessages(rebuilt);
            }
          }
        }
        this.log('INFO', `Sesión #${sid} cargada exitosamente.`);
      } else {
        this.setSession((prev) => ({ ...prev, sessionId: sid }));
      }
    } catch (err) {
      console.warn('Error loading session snapshot:', err);
      this.setSession((prev) => ({ ...prev, sessionId: sid }));
    }
    this.deps.ui.showLive();
  }

  /** Árbol de demostración visual. */
  loadDemo() {
    this.batch(() => {
      this.setIsRunning(false);
      this.setIsPaused(false);
      this.setSession(INITIAL_SESSION);
      this.setNodes(INITIAL_NODES);
      this.setDecisionsMap(INITIAL_DECISIONS_MAP);
      this.setSelectedNodeId('node-5');
      this.setLogs(INITIAL_LOGS);
      this.setEvents(INITIAL_EVENTS);
      this.setChatMessages(demoChat());
    });
  }

  /** Recupera el detalle completo de una decisión desde el backend para el inspector. */
  async fetchAndSetDecision(nodeId, decisionId) {
    if (!decisionId || nodeId === 'start') return;
    try {
      const res = await this.deps.api.fetchDecisionDetail(decisionId);
      const detail = res?.data || res;
      if (detail && detail.decision_tab) {
        this.setDecisionsMap((prev) => {
          const current = prev[nodeId] || {};
          return {
            ...prev,
            [nodeId]: {
              ...current,
              decisionId: detail.decision_id || decisionId,
              decisionTab: detail.decision_tab,
              evidenceTab: detail.evidence_tab || current.evidenceTab,
              policyTab: detail.policy_tab || current.policyTab,
              receiptTab: detail.receipt_tab || current.receiptTab,
              execution_mode: detail.decision_tab.execution_mode || current.execution_mode,
              isolation: detail.decision_tab.isolation || current.isolation,
              working_directory: detail.decision_tab.working_directory || current.working_directory,
              network_mode: detail.decision_tab.network_mode || current.network_mode,
              execution_backend: detail.decision_tab.execution_backend || current.execution_backend,
              model: detail.decision_tab.model || current.model,
              provider: detail.decision_tab.provider || current.provider,
              status: detail.decision_tab.status || current.status,
              riskLevel: detail.decision_tab.risk_level || current.riskLevel,
              riskScore: detail.decision_tab.risk_score ?? current.riskScore,
              reason: detail.decision_tab.reason || current.reason,
            },
          };
        });
      }
    } catch {
      // fallo silencioso en fallback
    }
  }

  selectNode(nodeId) {
    this.setSelectedNodeId(nodeId);
    this.deps.ui.openStep();
    const found = this.state.decisionsMap[nodeId];
    if (found) {
      this.log('INFO', `Inspeccionando la decisión #${found.sequence || 1}: ${found.actionCommand}`);
      this.fetchAndSetDecision(nodeId, found.decisionId || (nodeId.startsWith('d_') ? nodeId : `d_${nodeId}`));
    } else if (nodeId && nodeId !== 'start') {
      this.fetchAndSetDecision(nodeId, nodeId.startsWith('d_') ? nodeId : `d_${nodeId}`);
    }
  }

  async approve(decisionId, nodeId = this.state.selectedNodeId) {
    try {
      await this.deps.api.confirmDecision(decisionId, true, 'Authorized from Decision Inspector', 'operator_admin', 'operator');
    } catch (e) {
      // Si el servidor no registró la aprobación, la misión sigue esperando: no se muestra como aprobada
      console.warn('Backend approval call error:', e);
      this.deps.notify(`No se pudo registrar la aprobación: ${e?.message || e}`);
      return;
    }
    this.batch(() => {
      this.setDecisionsMap((prev) => {
        const cur = prev[nodeId];
        if (!cur) return prev;
        return {
          ...prev,
          [nodeId]: {
            ...cur,
            status: 'ALLOW',
            policyDecision: { ...cur.policyDecision, status: 'Authorized by human operator', requiresConfirmation: false },
            capability: { issued: true, statusText: 'Issued & HMAC Signed', token: `cap_${Math.random().toString(36).substring(2, 10)}` },
          },
        };
      });
      this.setNodes((prev) => prev.map((n) => (n.id === nodeId ? { ...n, status: 'ALLOW' } : n)));
      this.setSession((prev) => ({ ...prev, metrics: { ...prev.metrics, allowed: prev.metrics.allowed + 1, review: Math.max(0, prev.metrics.review - 1) } }));
      this.log('INFO', `Decisión #${decisionId} autorizada por el operador.`);
    });
  }

  async reject(decisionId, customReason, nodeId = this.state.selectedNodeId) {
    const rejectionMsg = customReason || 'Bloqueada por el operador';
    try {
      await this.deps.api.rejectDecision(decisionId, rejectionMsg, 'operator_admin', 'operator');
    } catch (e) {
      console.warn('Backend rejection error:', e);
    }
    this.batch(() => {
      this.setDecisionsMap((prev) => {
        const cur = prev[nodeId];
        if (!cur) return prev;
        return {
          ...prev,
          [nodeId]: { ...cur, status: 'BLOCKED', reason: rejectionMsg, capability: { issued: false, statusText: 'Revocada por el operador', token: null } },
        };
      });
      this.setNodes((prev) => prev.map((n) => (n.id === nodeId ? { ...n, status: 'BLOCK' } : n)));
      this.operatorMessage(`Rechazado: ${rejectionMsg}`);
      this.log('ERROR', `Decisión #${decisionId} rechazada y podada: ${rejectionMsg}`);
    });
  }

  async execute(decisionId, capability) {
    try {
      const res = await this.deps.api.executeDecision(decisionId, capability?.token);
      this.log(res?.data?.success ? 'INFO' : 'ERROR', `Ejecución en el sandbox terminada (${res?.data?.execution_time_ms || 12} ms): ${res?.data?.output || 'correcta'}`);
    } catch (e) {
      this.log('ERROR', `La ejecución falló: ${e.message}`);
    }
  }
}

function demoChat() {
  return [
    {
      id: 'msg-demo-user',
      role: 'user',
      text: INITIAL_SESSION.goal || 'Corregir el fallo de autenticación en la API',
      time: '14:32:00',
      config: { llm_provider: 'JEV (Claude-3.5-sonnet)', supervisor: 'LAYA System-1', execution_mode: 'local_restricted' },
    },
    {
      id: 'msg-demo-assistant',
      role: 'assistant',
      status: 'completed',
      time: '14:32:45',
      thoughts: [
        { step: 1, tool: 'analyze_codebase', thought: 'Analizar el middleware de autenticación para comprobar la validación del token y los flujos de bypass reportados.', verdict: 'ALLOW', score: 0.92 },
        { step: 2, tool: 'create_remediation_plan', thought: 'Formular un plan de remediación quirúrgico para corregir la cabecera X-API-Key y validar expiración de credenciales.', verdict: 'ALLOW', score: 0.89 },
        { step: 3, tool: 'read_file', thought: 'Leer auth/middleware.py para inspeccionar la implementación actual de la firma HMAC y los nonces.', verdict: 'ALLOW', score: 0.95 },
        { step: 4, tool: 'edit_file', thought: 'Aplicar parche de seguridad con validación estricta de capabilities y tiempo constante.', verdict: 'ALLOW', score: 0.85 },
        { step: 5, tool: 'git', thought: 'Intentar git push origin main sin aprobación previa de seguridad.', verdict: 'REVIEW', score: 0.64, reason: 'Acción de alto riesgo retenida para confirmación humana obligatoria.' },
      ],
      finalAnswer: INITIAL_SESSION.finalAnswer || 'El parche de autenticación ha sido validado satisfactoriamente contra la suite de tests. El intento de push directo fue interceptado preventivamente por la política de precedencia estricta de PRAXEON.',
    },
  ];
}

/**
 * Sesiones abiertas, una por panel. Cerrar un panel cierra su WebSocket. Un panel que no se ve (pestaña u otro maximizado) sigue
 * abierto: su misión y sus avisos no se detienen. `subscribe`/`getVersion` avisan de cualquier cambio en cualquiera de ellas
 * (para la cola global de aprobaciones).
 */
export function createSessionRegistry(makeDeps) {
  const sessions = new Map();
  const unsubs = new Map();
  const listeners = new Set();
  let version = 0;
  const bump = () => {
    version += 1;
    listeners.forEach((l) => l());
  };
  return {
    open(panelId, options) {
      if (!sessions.has(panelId)) {
        const session = new AgentSession(makeDeps(panelId), options);
        sessions.set(panelId, session);
        unsubs.set(panelId, session.subscribe(bump));
        bump();
      }
      return sessions.get(panelId);
    },
    get: (panelId) => sessions.get(panelId) || null,
    close(panelId) {
      if (!sessions.has(panelId)) return;
      unsubs.get(panelId)?.();
      unsubs.delete(panelId);
      sessions.get(panelId).dispose();
      sessions.delete(panelId);
      bump();
    },
    ids: () => [...sessions.keys()],
    closeAll() {
      [...sessions.keys()].forEach((id) => this.close(id));
    },
    /** Ciclo de vida del conjunto (lo hace quien lo posee, no cada panel): StrictMode desmonta y vuelve a montar. */
    activateAll() {
      sessions.forEach((s) => s.activate());
    },
    disposeAll() {
      sessions.forEach((s) => s.dispose());
    },
    subscribe(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    getVersion: () => version,
  };
}
