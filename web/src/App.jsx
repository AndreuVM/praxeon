import React, { useState, useEffect, useRef } from 'react';
import { CheckCircle2 } from 'lucide-react';
import Header from './components/Header';
import TopNav from './components/TopNav';
import SessionKPIs from './components/SessionKPIs';
import DecisionTree from './components/DecisionTree';
import ConsolePanel from './components/ConsolePanel';
import DecisionInspector from './components/DecisionInspector';
import ProposeActionModal from './components/ProposeActionModal';
import SessionsModal from './components/SessionsModal';

import SessionsView from './components/views/SessionsView';
import DecisionsView from './components/views/DecisionsView';
import AgentsView from './components/views/AgentsView';
import ProvidersView from './components/views/ProvidersView';
import SecurityView from './components/views/SecurityView';
import SettingsView from './components/views/SettingsView';
import WorkflowsView from './components/views/WorkflowsView';

import {
  INITIAL_SESSION,
  INITIAL_NODES,
  INITIAL_DECISIONS_MAP,
  INITIAL_LOGS,
  INITIAL_EVENTS,
} from './constants/demoData';

import * as api from './services/api';
import { computeTreeLayout } from './utils/treeLayout';

// Estado inicial limpio y listo para ejecución interactiva
const READY_SESSION = {
  sessionId: 'ready',
  status: 'Ready',
  agent: 'CodingAgent',
  goal: 'Listo para iniciar misión supervisada. Introduce un prompt y selecciona modelos.',
  metrics: {
    totalDecisions: 0,
    allowed: 0,
    blocked: 0,
    review: 0,
  },
  runtime: {
    provider: 'JEV + LAYA',
    version: 'v1.0.0',
    latencyP50: '—',
    executionTime: '—',
  },
};

const READY_NODES = [
  { id: 'start', label: 'Start', type: 'start', status: 'SYSTEM', x: 420, y: 30, parentId: null },
];

export default function App() {
  const [activeNav, setActiveNav] = useState('live');
  const [session, setSession] = useState(READY_SESSION);
  const [nodes, setNodes] = useState(READY_NODES);
  const [decisionsMap, setDecisionsMap] = useState({});
  const [selectedNodeId, setSelectedNodeId] = useState('start');
  const [logs, setLogs] = useState([
    {
      time: new Date().toTimeString().split(' ')[0],
      level: 'INFO',
      message: 'PRAXEON Runtime 1.0 inicializado. Listo para recibir prompt y modelos.',
    },
  ]);
  const [events, setEvents] = useState([]);
  const [sessionsList, setSessionsList] = useState([]);

  const [runtimeActive, setRuntimeActive] = useState(true);
  const [isRunning, setIsRunning] = useState(false);
  const [isPaused, setIsPaused] = useState(false);
  const [isProposeOpen, setIsProposeOpen] = useState(false);
  const [isSessionsOpen, setIsSessionsOpen] = useState(false);
  const [inspectorTab, setInspectorTab] = useState('chat');
  const maxSeqRef = useRef(0);

  // Estado persistente del Chat de Misión y configuración de modelos
  const [missionConfig, setMissionConfig] = useState({
    llmProvider: 'simulator',
    supervisor: 'laya',
    executionMode: 'local_restricted',
    fullAccessConfirmed: false,
    maxSteps: 25,
    customModel: '',
    apiKey: '',
    baseUrl: '',
  });

  const [chatMessages, setChatMessages] = useState([
    {
      id: 'welcome',
      role: 'assistant',
      text: 'Hola, soy el asistente de supervisión de PRAXEON. Introduce una tarea para el agente autónomo. El supervisor evaluará cada acción en tiempo real, aplicando políticas deterministas, comprobación de evidencias y ejecución confinada en sandbox.',
      time: 'Listo',
      isWelcome: true,
    },
  ]);

  // Comprobar salud del backend y listar sesiones previas
  useEffect(() => {
    async function initBackend() {
      const health = await api.fetchHealth();
      if (health?.data?.status === 'healthy' || health?.data?.status === 'ok') {
        setRuntimeActive(true);
        const sList = await api.fetchSessions();
        if (sList?.data && sList.data.length > 0) {
          setSessionsList(sList.data);
        }
      }
    }
    initBackend();
  }, []);

  // Suscripción WebSocket en tiempo real a la sesión activa
  useEffect(() => {
    if (!runtimeActive || !session.sessionId || session.sessionId === 'ready') return;

    const stream = api.createWebSocketStream(
      session.sessionId,
      (msg) => {
        if (msg.action === 'event' && msg.data) {
          const ev = msg.data;
          const evType = ev.type || ev.event_type || '';
          const timeStr = new Date(ev.timestamp || Date.now()).toTimeString().split(' ')[0];

          if (ev.sequence && typeof ev.sequence === 'number') {
            maxSeqRef.current = Math.max(maxSeqRef.current, ev.sequence);
          }

          // 1. Agregar a eventos estructurados
          setEvents((prev) => [
            {
              seq: ev.sequence,
              type: evType,
              time: timeStr,
              payload: ev.payload,
              detail: typeof ev.payload === 'string' ? ev.payload : JSON.stringify(ev.payload || {}).slice(0, 120),
            },
            ...prev,
          ]);

          // 2. Agregar a log de terminal con nivel contextual
          let level = 'INFO';
          if (evType.includes('error') || evType.includes('pruned') || evType.includes('blocked')) level = 'ERROR';
          else if (evType.includes('warn') || evType === 'approval.requested' || (evType === 'policy.decided' && ev.payload?.status === 'REVIEW')) level = 'WARN';

          let logMsg = `[${evType}]`;
          if (evType === 'session.started') {
            logMsg = `Session #${ev.session_id} started (Agent: ${ev.payload?.agent_name || 'CodingAgent'})`;
          } else if (evType === 'goal.created') {
            logMsg = `Mission goal: ${ev.payload?.goal}`;
          } else if (evType === 'action.proposed') {
            logMsg = `Step ${ev.payload?.step || 1}: Proposing action '${ev.payload?.tool}' (${ev.payload?.operation || ''})`;
          } else if (evType === 'operation.classified') {
            logMsg = `Operation classified: ${ev.payload?.category?.toUpperCase()} (Risk: ${ev.payload?.risk_level?.toUpperCase()}, Conf: ${Math.round((ev.payload?.confidence || 0) * 100)}%)`;
          } else if (evType === 'evidence.evaluated') {
            logMsg = `Evaluated ${ev.payload?.evidence_count || 0} empirical claims (Grounding score: ${Math.round((ev.payload?.grounding_score || 0.85) * 100)}%)`;
          } else if (evType === 'risk.assessed') {
            logMsg = `Risk assessed: ${ev.payload?.level || 'LOW'} (Score: ${ev.payload?.score || 0.15})`;
          } else if (evType === 'provider.evaluated') {
            logMsg = `Semantic evaluation (${ev.payload?.provider_name}): Score ${ev.payload?.score} -> ${ev.payload?.verdict}`;
          } else if (evType === 'policy.decided') {
            logMsg = `Policy decided: ${ev.payload?.status} (${ev.payload?.reason_code || 'STANDARD_POLICY'})`;
          } else if (evType === 'approval.requested') {
            logMsg = `Policy requires human confirmation (Risk: ${ev.payload?.risk_level || 'HIGH'})`;
          } else if (evType === 'approval.completed') {
            logMsg = `Human operator decided: ${ev.payload?.approved ? 'APPROVED' : 'REJECTED'}`;
          } else if (evType === 'capability.issued') {
            logMsg = `Capability token issued & signed with HMAC`;
          } else if (evType === 'execution.started') {
            logMsg = `Executing action in local sandbox: ${ev.payload?.tool}...`;
          } else if (evType === 'execution.completed') {
            logMsg = `Action executed ${ev.payload?.success ? 'successfully' : 'with error'} in ${ev.payload?.execution_time_ms || 12}ms`;
          } else if (evType === 'observation.recorded') {
            logMsg = `Observation: ${(ev.payload?.output || '').slice(0, 100)}...`;
          } else if (evType === 'decision.pruned') {
            logMsg = `Decision pruned: ${ev.payload?.reason}`;
          } else if (evType === 'intervention.applied') {
            logMsg = `[INTERVENTION] ${ev.payload?.intervention || 'Supervisor'}: ${ev.payload?.message || ''}`;
          } else if (evType === 'session.completed') {
            logMsg = `Mission complete: ${ev.payload?.summary || 'Completed successfully'}`;
          }

          setLogs((prev) => [
            ...prev,
            { time: timeStr, level, message: logMsg },
          ]);

          // 3. Procesamiento dinámico del Decision Tree en tiempo real
          if (evType === 'action.proposed') {
            const actId = ev.node_id;
            const tool = ev.payload?.tool || 'action';
            const op = ev.payload?.operation || tool;
            const stepNum = ev.payload?.step || 1;

            setNodes((prevNodes) => {
              const exists = prevNodes.some((n) => n.id === actId);
              if (exists) return prevNodes;

              // Calcular parentId canónico y posicionamiento jerárquico
              const rawParent = ev.parent_id || ev.payload?.parent_id;
              let resolvedParentId = 'start';
              if (rawParent) {
                if (rawParent.startsWith('root') || rawParent === 'start') {
                  resolvedParentId = 'start';
                } else if (prevNodes.some((n) => n.id === rawParent)) {
                  resolvedParentId = rawParent;
                } else if (prevNodes.length > 0) {
                  resolvedParentId = prevNodes[prevNodes.length - 1].id;
                }
              }

              const isHub = op.includes('Propose') || stepNum === 5;
              const newNode = {
                id: actId,
                label: `${stepNum}. ${op}`,
                subtitle: `${timeStr} · LLM (${ev.payload?.source?.replace('LLM (', '').replace(')', '') || 'JEV'})`,
                type: isHub ? 'hub' : 'step',
                status: 'PENDING',
                action: op,
                x: 420,
                y: 105,
                parentId: resolvedParentId,
                isNew: true,
              };

              const updatedNodes = [...prevNodes, newNode];
              return computeTreeLayout(updatedNodes);
            });

            // Auto-seleccionar el nodo actual para que el inspector se actualice en tiempo real
            setSelectedNodeId(actId);

            // Actualizar estado de la sesión en el header y KPIs
            setSession((prevSess) => ({
              ...prevSess,
              status: `Active (Paso ${stepNum})`,
            }));

            // Registrar borrador de decisión para inspección inmediata en las 4 pestañas
            setDecisionsMap((prevMap) => ({
              ...prevMap,
              [actId]: {
                decisionId: ev.decision_id || `d_${actId}`,
                sequence: stepNum,
                status: 'PENDING',
                actionCommand: `${tool} ${JSON.stringify(ev.payload?.arguments || {})}`,
                tool: tool,
                provider: ev.payload?.source || 'JEV',
                model: 'Claude-3.5-sonnet',
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
                evidenceTab: {
                  groundingScore: 0.85,
                  claims: ['Analyzing repository structure and code contracts.'],
                  claimCount: 1,
                  freshness: 'live',
                },
                receiptTab: {
                  decisionId: ev.decision_id || `d_${actId}`,
                  actionHash: 'sha256:' + actId,
                  stateHash: 'sha256:state_' + actId,
                  nonce: 'non_' + actId,
                },
                relatedDecisions: [],
              },
            }));
          }

          if (evType === 'operation.classified') {
            const actId = ev.node_id;
            const opAssessment = ev.payload || {};
            const opCategory = (opAssessment.category || 'inspection').toLowerCase();

            setDecisionsMap((prevMap) => {
              const current = prevMap[actId] || {};
              return {
                ...prevMap,
                [actId]: {
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
                },
              };
            });
          }

          if (evType === 'evidence.evaluated') {
            const actId = ev.node_id;
            const groundingScore = ev.payload?.grounding_score ?? 0.85;
            const claims = ev.payload?.claims_evaluated || [];
            const claimCount = ev.payload?.evidence_count ?? claims.length;

            setDecisionsMap((prevMap) => {
              const current = prevMap[actId] || {};
              return {
                ...prevMap,
                [actId]: {
                  ...current,
                  evidenceTab: {
                    ...current.evidenceTab,
                    groundingScore,
                    claims: claims.length > 0 ? claims : ['Empirical grounding validated against current state.'],
                    claimCount,
                    freshness: 'live',
                  },
                },
              };
            });
          }

          if (evType === 'provider.evaluated') {
            const actId = ev.node_id;
            setDecisionsMap((prevMap) => {
              const current = prevMap[actId] || {};
              return {
                ...prevMap,
                [actId]: {
                  ...current,
                  semanticEvaluation: [
                    { provider: ev.payload?.provider_name || 'LAYA', score: ev.payload?.score ?? 0.88, verdict: ev.payload?.verdict || 'ALLOW' },
                    { provider: 'TypeSafe', score: 0.82, verdict: 'ALLOW' },
                  ],
                },
              };
            });
          }

          if (evType === 'risk.assessed') {
            const actId = ev.node_id;
            const rLevel = ev.payload?.level || 'LOW';
            const rScore = ev.payload?.score ?? 0.15;
            setDecisionsMap((prevMap) => {
              const current = prevMap[actId] || {};
              return {
                ...prevMap,
                [actId]: {
                  ...current,
                  riskLevel: rLevel,
                  riskScore: rScore,
                  reason: ev.payload?.reasons?.[0] || current.reason,
                },
              };
            });
          }

          if (evType === 'policy.decided') {
            const actId = ev.parent_id || ev.node_id;
            const statusStr = ev.payload?.status || 'ALLOW';
            const requiresConf = !!ev.payload?.requires_confirmation;

            // Actualizar estado visual del nodo en el árbol
            setNodes((prevNodes) =>
              prevNodes.map((n) => {
                if (n.id === actId) {
                  return {
                    ...n,
                    status: statusStr === 'REVIEW' ? 'REVIEW' : statusStr === 'BLOCK' ? 'BLOCK' : 'ALLOW',
                  };
                }
                return n;
              })
            );

            // Actualizar contadores de métricas de la sesión
            setSession((prevSess) => {
              const m = prevSess.metrics || { totalDecisions: 0, allowed: 0, blocked: 0, review: 0 };
              return {
                ...prevSess,
                status: statusStr === 'REVIEW' ? 'Review Required' : prevSess.status,
                metrics: {
                  totalDecisions: m.totalDecisions + 1,
                  allowed: statusStr === 'ALLOW' ? m.allowed + 1 : m.allowed,
                  blocked: statusStr === 'BLOCK' ? m.blocked + 1 : m.blocked,
                  review: statusStr === 'REVIEW' ? m.review + 1 : m.review,
                },
              };
            });

            // Actualizar datos de decisión en el mapa
            setDecisionsMap((prevMap) => {
              const current = prevMap[actId] || {};
              return {
                ...prevMap,
                [actId]: {
                  ...current,
                  status: statusStr,
                  policyDecision: {
                    status: requiresConf ? 'Requires confirmation' : 'Authorized by policy',
                    requiresConfirmation: requiresConf,
                    rulesActivated: requiresConf ? ['EgressPolicy', 'PathContainment'] : ['StandardPolicy'],
                    reasonCodes: [ev.payload?.reason_code || 'POLICY_EVALUATED'],
                    precedence: 'Deterministic Safety Precedence',
                  },
                },
              };
            });

            // Si requiere revisión o es bloqueado, enfocar automáticamente ese nodo en el inspector
            if (statusStr === 'REVIEW' || statusStr === 'BLOCK') {
              setSelectedNodeId(actId);
            }
          }

          if (evType === 'capability.issued') {
            const actId = ev.node_id;
            const cap = ev.payload || {};
            setDecisionsMap((prevMap) => {
              const current = prevMap[actId] || {};
              return {
                ...prevMap,
                [actId]: {
                  ...current,
                  capability: {
                    issued: true,
                    statusText: 'Issued & HMAC Signed',
                    token: cap.capability_id || cap.signature || 'cap_hmac_verified',
                  },
                  receiptTab: {
                    decisionId: cap.decision_id || current.decisionId,
                    actionHash: cap.action_hash || current.receiptTab?.actionHash || 'sha256:' + actId,
                    stateHash: cap.state_hash || current.receiptTab?.stateHash || 'sha256:state_' + actId,
                    nonce: cap.nonce || current.receiptTab?.nonce || 'non_' + actId,
                  },
                },
              };
            });
          }

          if (evType === 'approval.completed') {
            const actId = ev.node_id;
            const approved = ev.payload?.approved;
            if (approved) {
              setNodes((prevNodes) =>
                prevNodes.map((n) => (n.id === actId ? { ...n, status: 'ALLOW' } : n))
              );
              setSession((prevSess) => {
                const m = prevSess.metrics;
                return {
                  ...prevSess,
                  metrics: {
                    ...m,
                    allowed: m.allowed + 1,
                    review: Math.max(0, m.review - 1),
                  },
                };
              });
            }
          }

          if (evType === 'execution.started') {
            const actId = ev.node_id;
            setNodes((prevNodes) =>
              prevNodes.map((n) => (n.id === actId ? { ...n, status: 'EXECUTING' } : n))
            );
          }

          if (evType === 'execution.completed') {
            const actId = ev.node_id;
            const success = ev.payload?.success;
            const timeMs = ev.payload?.execution_time_ms;
            setNodes((prevNodes) => {
              const updated = prevNodes.map((n) => (n.id === actId ? { ...n, status: success ? 'ALLOW' : 'BLOCK' } : n));
              return computeTreeLayout(updated);
            });
            if (timeMs) {
              setSession((prevSess) => ({
                ...prevSess,
                runtime: {
                  ...prevSess.runtime,
                  latencyP50: `${timeMs}ms`,
                },
              }));
            }
          }

          if (evType === 'decision.pruned') {
            const actId = ev.node_id;
            setNodes((prevNodes) => {
              const updated = prevNodes.map((n) =>
                n.id === actId ? { ...n, status: 'BLOCK', subtitle: 'Podado (Pruned)' } : n
              );
              return computeTreeLayout(updated);
            });
            setDecisionsMap((prevMap) => {
              const current = prevMap[actId] || {};
              return {
                ...prevMap,
                [actId]: {
                  ...current,
                  status: 'BLOCK',
                  reason: ev.payload?.reason || current.reason,
                },
              };
            });
          }

          if (evType === 'intervention.applied') {
            const failedNode = ev.payload?.failed_node;
            if (failedNode) {
              setNodes((prevNodes) => {
                const updated = prevNodes.map((n) =>
                  n.id === failedNode ? { ...n, status: 'BLOCK' } : n
                );
                return computeTreeLayout(updated);
              });
            }
          }

          if (evType === 'observation.recorded') {
            const actId = ev.node_id;
            const output = ev.payload?.output || '';
            setDecisionsMap((prevMap) => {
              const current = prevMap[actId] || {};
              return {
                ...prevMap,
                [actId]: {
                  ...current,
                  observationOutput: output,
                },
              };
            });
            if (output && (output.includes('Tarea concluida:') || output.includes('conclu') || output.includes('finaliz'))) {
              const cleanAnswer = output.replace('Tarea concluida:', '').trim();
              setSession((prevSess) => ({
                ...prevSess,
                status: 'Completed',
                finalAnswer: cleanAnswer,
              }));
            }
          }

          if (evType === 'session.completed') {
            const summary = ev.payload?.summary;
            setSession((prevSess) => ({
              ...prevSess,
              status: 'Completed',
              finalAnswer: summary || prevSess.finalAnswer,
              runtime: {
                ...prevSess.runtime,
                executionTime: 'Completed',
              },
            }));
            setIsRunning(false);
          }
        }
      },
      (status) => {
        if (status === 'connected') setRuntimeActive(true);
      },
      () => maxSeqRef.current
    );

    return () => stream.close();
  }, [session.sessionId, runtimeActive]);

  // Selección de nodo en el árbol
  const currentDecision = decisionsMap[selectedNodeId] || Object.values(decisionsMap)[0] || null;

  const handleSelectNode = (nodeId) => {
    setSelectedNodeId(nodeId);
    setInspectorTab('decision');
    const found = decisionsMap[nodeId];
    if (found) {
      const timeStr = new Date().toTimeString().split(' ')[0];
      setLogs((prev) => [
        ...prev,
        { time: timeStr, level: 'INFO', message: `Inspecting decision #${found.sequence || 1}: ${found.actionCommand}` },
      ]);
    }
  };

  // Lanzar misión interactiva en tiempo real
  const handleStartMission = async ({
    goal,
    execution_mode,
    llm_provider,
    supervisor,
    max_steps,
    llm_model,
    api_key,
    base_url,
    workspace_root,
    chat_history,
  }) => {
    setIsRunning(true);
    setIsPaused(false);
    setInspectorTab('chat');
    const timeStr = new Date().toTimeString().split(' ')[0];

    try {
      const res = await api.runMission({
        goal,
        execution_mode,
        llm_provider,
        llm_model,
        api_key,
        base_url,
        supervisor,
        max_steps,
        step_delay_ms: 1000,
        workspace_root,
        chat_history,
      });

      if (res?.data) {
        const sid = res.data.session_id;
        const newSess = {
          sessionId: sid,
          status: 'Active',
          agent: 'CodingAgent',
          goal: goal,
          execution_mode: res.data.execution_mode || execution_mode || 'local_restricted',
          metrics: { totalDecisions: 0, allowed: 0, blocked: 0, review: 0 },
          runtime: {
            provider: `${llm_provider.toUpperCase()} + ${supervisor.toUpperCase()}`,
            version: 'v1.0.0',
            latencyP50: '92ms',
            executionTime: 'Live',
          },
          finalAnswer: null,
        };

        setSession(newSess);
        setSessionsList((prev) => [res.data, ...prev.filter((s) => s.session_id !== sid)]);
        setNodes([
          { id: 'start', label: 'Start', type: 'start', status: 'SYSTEM', x: 420, y: 30, parentId: null },
        ]);
        setSelectedNodeId('start');
        setDecisionsMap({});
        setEvents([]);
        setLogs([
          {
            time: timeStr,
            level: 'INFO',
            message: `Starting interactive session #${sid} with LLM (${llm_provider.toUpperCase()}) & Supervisor (${supervisor.toUpperCase()})`,
          },
          {
            time: timeStr,
            level: 'INFO',
            message: `Goal: ${goal}`,
          },
        ]);
      }
    } catch (err) {
      setLogs((prev) => [
        ...prev,
        { time: timeStr, level: 'ERROR', message: `Error starting mission: ${err.message}` },
      ]);
      setIsRunning(false);
    }
  };

  // Pausar misión
  const handlePauseMission = async () => {
    if (!session.sessionId) return;
    try {
      await api.pauseMission(session.sessionId);
      setIsPaused(true);
      const timeStr = new Date().toTimeString().split(' ')[0];
      setLogs((prev) => [
        ...prev,
        { time: timeStr, level: 'WARN', message: `Misión pausada por el operador.` },
      ]);
    } catch (e) {
      console.warn('Error pausing mission:', e);
    }
  };

  // Reanudar misión
  const handleResumeMission = async () => {
    if (!session.sessionId) return;
    try {
      await api.resumeMission(session.sessionId);
      setIsPaused(false);
      const timeStr = new Date().toTimeString().split(' ')[0];
      setLogs((prev) => [
        ...prev,
        { time: timeStr, level: 'INFO', message: `Misión reanudada por el operador.` },
      ]);
    } catch (e) {
      console.warn('Error resuming mission:', e);
    }
  };

  // Detener misión
  const handleStopMission = () => {
    setIsRunning(false);
    setIsPaused(false);
    const timeStr = new Date().toTimeString().split(' ')[0];
    setLogs((prev) => [
      ...prev,
      { time: timeStr, level: 'WARN', message: `Misión detenida.` },
    ]);
  };

  // Refrescar lista de sesiones
  const refreshSessionsList = async () => {
    try {
      const sList = await api.fetchSessions();
      if (sList?.data) {
        setSessionsList(sList.data);
      }
    } catch (e) {
      console.warn('Error refreshing sessions:', e);
    }
  };

  // Crear una nueva sesión limpia (grafo y chat en blanco, sin memoria previa)
  const handleNewCleanSession = () => {
    setIsRunning(false);
    setIsPaused(false);
    const newSid = `s-${Math.random().toString(36).substring(2, 9)}`;
    const timeStr = new Date().toTimeString().split(' ')[0];

    setSession({
      sessionId: newSid,
      status: 'Ready',
      agent: 'CodingAgent',
      goal: 'Nueva sesión limpia. Introduce un prompt para iniciar la misión.',
      execution_mode: missionConfig.executionMode || 'local_restricted',
      metrics: { totalDecisions: 0, allowed: 0, blocked: 0, review: 0 },
      kpis: { totalDecisions: 0, allowedDecisions: 0, blockedDecisions: 0, reviewDecisions: 0 },
      runtime: {
        provider: `${(missionConfig.llmProvider || 'SIMULATOR').toUpperCase()} + ${(missionConfig.supervisor || 'LAYA').toUpperCase()}`,
        version: 'v1.0.0',
        latencyP50: '—',
        executionTime: 'Live',
      },
      finalAnswer: null,
    });

    setNodes([
      { id: 'start', label: 'Start', type: 'start', status: 'SYSTEM', x: 420, y: 30, parentId: null },
    ]);
    setSelectedNodeId('start');
    setDecisionsMap({});
    setEvents([]);
    setChatMessages([
      {
        id: 'welcome',
        role: 'assistant',
        text: 'Hola, soy el asistente de supervisión de PRAXEON. Introduce una tarea para el agente autónomo. El supervisor evaluará cada acción en tiempo real, aplicando políticas deterministas, comprobación de evidencias y ejecución confinada en sandbox.',
        time: 'Listo',
        isWelcome: true,
      },
    ]);
    setInspectorTab('chat');
    setLogs([
      {
        time: timeStr,
        level: 'INFO',
        message: `Nueva sesión limpia (#${newSid}) inicializada. Grafo y memoria reseteados.`,
      },
    ]);
    setActiveNav('live');
  };

  // Eliminar una sesión individual
  const handleDeleteSession = async (sessionId) => {
    const timeStr = new Date().toTimeString().split(' ')[0];
    try {
      await api.deleteSession(sessionId);
      setSessionsList((prev) => prev.filter((s) => s.session_id !== sessionId));
      setLogs((prev) => [
        ...prev,
        { time: timeStr, level: 'INFO', message: `Sesión #${sessionId} eliminada y purgada exitosamente.` },
      ]);

      if (session.sessionId === sessionId) {
        handleNewCleanSession();
      }
    } catch (err) {
      console.error('Error eliminando sesión:', err);
      setLogs((prev) => [
        ...prev,
        { time: timeStr, level: 'ERROR', message: `Error eliminando sesión #${sessionId}: ${err.message}` },
      ]);
    }
  };

  // Limpiar/purgar sesiones antiguas finalizadas
  const handleClearOldSessions = async () => {
    const timeStr = new Date().toTimeString().split(' ')[0];
    try {
      const res = await api.clearSessions(true, session.sessionId);
      const count = res?.data?.deleted_count || 0;
      await refreshSessionsList();
      setLogs((prev) => [
        ...prev,
        {
          time: timeStr,
          level: 'INFO',
          message: `Limpieza completada: se purgaron ${count} sesiones antiguas finalizadas.`,
        },
      ]);
    } catch (err) {
      console.error('Error purgando sesiones antiguas:', err);
      setLogs((prev) => [
        ...prev,
        { time: timeStr, level: 'ERROR', message: `Error en limpieza de sesiones: ${err.message}` },
      ]);
    }
  };


  // Cargar sesión existente y su snapshot de árbol / decisiones
  const handleLoadSession = async (sid) => {
    setIsRunning(false);
    setIsPaused(false);
    const timeStr = new Date().toTimeString().split(' ')[0];

    try {
      const snap = await api.fetchSession(sid);
      if (snap && snap.data) {
        const d = snap.data;
        const summary = d.summary || {};

        setSession({
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
          runtime: {
            provider: 'JEV + LAYA',
            version: 'v1.0.0',
            latencyP50: '14ms',
            executionTime: 'Histórico',
          },
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
          setNodes(computeTreeLayout(loadedNodes));
          setSelectedNodeId(loadedNodes[0].id);
        }

        // Cargar decisiones asociadas
        const decRes = await api.fetchSessionDecisions(sid);
        if (decRes && decRes.data && Array.isArray(decRes.data)) {
          const map = {};
          decRes.data.forEach((item) => {
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
            };
          });
          setDecisionsMap(map);
        }

        setLogs((prev) => [
          ...prev,
          { time: timeStr, level: 'INFO', message: `Sesión #${sid} cargada exitosamente.` },
        ]);
      } else {
        setSession((prev) => ({ ...prev, sessionId: sid }));
      }
    } catch (err) {
      console.warn('Error loading session snapshot:', err);
      setSession((prev) => ({ ...prev, sessionId: sid }));
    }

    setActiveNav('live');
  };

  // Cargar árbol de demostración visual (para comparar con la captura si se desea)
  const handleLoadDemo = () => {
    setIsRunning(false);
    setIsPaused(false);
    setSession(INITIAL_SESSION);
    setNodes(INITIAL_NODES);
    setDecisionsMap(INITIAL_DECISIONS_MAP);
    setSelectedNodeId('node-5');
    setInspectorTab('chat');
    setLogs(INITIAL_LOGS);
    setEvents(INITIAL_EVENTS);
    setChatMessages([
      {
        id: 'msg-demo-user',
        role: 'user',
        text: INITIAL_SESSION.goal || 'Fix authentication bug in the API',
        time: '14:32:00',
        config: {
          llm_provider: 'JEV (Claude-3.5-sonnet)',
          supervisor: 'LAYA System-1',
          execution_mode: 'local_restricted',
        },
      },
      {
        id: 'msg-demo-assistant',
        role: 'assistant',
        status: 'completed',
        time: '14:32:45',
        thoughts: [
          {
            step: 1,
            tool: 'analyze_codebase',
            thought: 'Analizar el middleware de autenticación para comprobar la validación del token y los flujos de bypass reportados.',
            verdict: 'ALLOW',
            score: 0.92,
          },
          {
            step: 2,
            tool: 'create_remediation_plan',
            thought: 'Formular un plan de remediación quirúrgico para corregir la cabecera X-API-Key y validar expiración de credenciales.',
            verdict: 'ALLOW',
            score: 0.89,
          },
          {
            step: 3,
            tool: 'read_file',
            thought: 'Leer auth/middleware.py para inspeccionar la implementación actual de la firma HMAC y los nonces.',
            verdict: 'ALLOW',
            score: 0.95,
          },
          {
            step: 4,
            tool: 'edit_file',
            thought: 'Aplicar parche de seguridad con validación estricta de capabilities y tiempo constante.',
            verdict: 'ALLOW',
            score: 0.85,
          },
          {
            step: 5,
            tool: 'git',
            thought: 'Intentar git push origin main sin aprobación previa de seguridad.',
            verdict: 'REVIEW',
            score: 0.64,
            reason: 'Acción de alto riesgo retenida para confirmación humana obligatoria.',
          },
        ],
        finalAnswer: INITIAL_SESSION.finalAnswer || 'El parche de autenticación ha sido validado satisfactoriamente contra la suite de tests. El intento de push directo fue interceptado preventivamente por la política de precedencia estricta de PRAXEON.',
      },
    ]);
  };

  // Autorización humana desde el DecisionInspector
  const handleApprove = async (decisionId) => {
    const timeStr = new Date().toTimeString().split(' ')[0];
    try {
      await api.confirmDecision(decisionId, true, 'Authorized from Decision Inspector', 'operator_admin', 'operator');
    } catch (e) {
      console.warn('Backend approval call error:', e);
    }

    // Actualizar visualmente de inmediato
    setDecisionsMap((prev) => {
      const cur = prev[selectedNodeId];
      if (!cur) return prev;
      return {
        ...prev,
        [selectedNodeId]: {
          ...cur,
          status: 'ALLOW',
          policyDecision: {
            ...cur.policyDecision,
            status: 'Authorized by human operator',
            requiresConfirmation: false,
          },
          capability: {
            issued: true,
            statusText: 'Issued & HMAC Signed',
            token: `cap_${Math.random().toString(36).substring(2, 10)}`,
          },
        },
      };
    });

    setNodes((prev) =>
      prev.map((n) => (n.id === selectedNodeId ? { ...n, status: 'ALLOW' } : n))
    );

    setSession((prev) => ({
      ...prev,
      metrics: {
        ...prev.metrics,
        allowed: prev.metrics.allowed + 1,
        review: Math.max(0, prev.metrics.review - 1),
      },
    }));

    setLogs((prev) => [
      ...prev,
      { time: timeStr, level: 'INFO', message: `Decision #${decisionId} authorized by human operator.` },
    ]);
  };

  // Rechazo de decisión desde el DecisionInspector
  const handleReject = async (decisionId, customReason) => {
    const timeStr = new Date().toTimeString().split(' ')[0];
    const rejectionMsg = customReason || 'Blocked by human operator';
    try {
      await api.rejectDecision(decisionId, rejectionMsg, 'operator_admin', 'operator');
    } catch (e) {
      console.warn('Backend rejection error:', e);
    }

    setDecisionsMap((prev) => {
      const cur = prev[selectedNodeId];
      if (!cur) return prev;
      return {
        ...prev,
        [selectedNodeId]: {
          ...cur,
          status: 'BLOCKED',
          reason: rejectionMsg,
          capability: { issued: false, statusText: 'Revoked by Human', token: null },
        },
      };
    });

    setNodes((prev) =>
      prev.map((n) => (n.id === selectedNodeId ? { ...n, status: 'BLOCK' } : n))
    );

    setLogs((prev) => [
      ...prev,
      { time: timeStr, level: 'ERROR', message: `Decision #${decisionId} rejected and pruned: ${rejectionMsg}` },
    ]);
  };

  // Ejecución física en sandbox
  const handleExecute = async (decisionId, capability) => {
    const timeStr = new Date().toTimeString().split(' ')[0];
    try {
      const res = await api.executeDecision(decisionId, capability?.token);
      setLogs((prev) => [
        ...prev,
        {
          time: timeStr,
          level: res?.data?.success ? 'INFO' : 'ERROR',
          message: `Execution in sandbox finished (${res?.data?.execution_time_ms || 12}ms): ${res?.data?.output || 'Success'}`,
        },
      ]);
    } catch (e) {
      setLogs((prev) => [
        ...prev,
        { time: timeStr, level: 'ERROR', message: `Execution failed: ${e.message}` },
      ]);
    }
  };

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      height: '100vh',
      width: '100vw',
      backgroundColor: 'var(--bg-app)',
      color: 'var(--text-primary)',
    }}>
      {/* 1. Header Bar */}
      <Header
        sessionId={session.sessionId}
        runtimeActive={runtimeActive}
        executionMode={session.execution_mode || 'local_restricted'}
        operatorId="operator_admin"
        operatorRole="operator"
        onOpenSessions={() => setIsSessionsOpen(true)}
        onOpenPropose={() => setIsProposeOpen(true)}
        onNewSession={handleNewCleanSession}
      />

      {/* 2. Main Work Layout (Left: TopNav + Content; Right: DecisionInspector / MissionChat) */}
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        {/* Left Column: TopNav + Active View Content */}
        <div style={{
          display: 'flex',
          flexDirection: 'column',
          flex: 1,
          overflow: 'hidden',
          minWidth: 0,
        }}>
          {/* Top Window / View Selection Bar */}
          <TopNav
            activeNav={activeNav}
            onNavSelect={setActiveNav}
            currentDecisionCount={nodes.length}
          />

          {/* Central Supervision Canvas & Console Area */}
          <div style={{ display: 'flex', flex: 1, overflow: 'hidden', minWidth: 0 }}>
            {activeNav === 'live' && (
              <main style={{
                flex: 1,
                display: 'flex',
                flexDirection: 'column',
                overflow: 'hidden',
                backgroundColor: '#0a0e16',
                minWidth: 0,
              }}>
                {/* Top Session KPIs Overview (Compacto) */}
                <SessionKPIs session={session} />

                {/* Interactive Decision Tree (Maximizado en el canvas central) */}
                <DecisionTree
                  nodes={nodes}
                  selectedNodeId={selectedNodeId}
                  onSelectNode={handleSelectNode}
                />

                {/* Bottom Console Panel (Terminal + Events + Telemetry) */}
                <ConsolePanel
                  logs={logs}
                  events={events}
                  runtime={session.runtime}
                />
              </main>
            )}

            {/* Supplementary Views */}
            {activeNav === 'sessions' && (
              <SessionsView
                sessions={sessionsList}
                currentSessionId={session.sessionId}
                onSelectSession={handleLoadSession}
                onCreateSession={(cfg) => {
                  handleStartMission(cfg);
                  setActiveNav('live');
                }}
                onRefreshSessions={refreshSessionsList}
                onDeleteSession={handleDeleteSession}
                onClearOldSessions={handleClearOldSessions}
                onNewCleanSession={handleNewCleanSession}
              />
            )}

            {activeNav === 'decisions' && (
              <DecisionsView
                decisions={nodes.map((n) => ({
                  decision_id: n.id,
                  sequence: n.sequence,
                  session_id: session.sessionId,
                  tool: n.tool,
                  command: n.command || n.title,
                  status: n.verdict || n.status,
                  risk_level: n.risk,
                  created_at: n.timestamp,
                }))}
                currentSessionId={session.sessionId}
                sessions={sessionsList}
                onSelectDecision={(nodeId, sid) => {
                  if (sid && sid !== session.sessionId) {
                    handleLoadSession(sid);
                  }
                  setSelectedNodeId(nodeId);
                  setInspectorTab('decision');
                  setActiveNav('live');
                }}
              />
            )}

            {activeNav === 'agents' && (
              <AgentsView
                session={session}
                sessionsList={sessionsList}
                missionConfig={missionConfig}
                isRunning={isRunning}
                isPaused={isPaused}
                onSelectSession={handleLoadSession}
                onLaunchAgentMission={(cfg) => {
                  handleStartMission(cfg);
                  setActiveNav('live');
                }}
              />
            )}

            {activeNav === 'workflows' && <WorkflowsView />}

            {activeNav === 'providers' && <ProvidersView />}

            {activeNav === 'security' && (
              <SecurityView
                session={session}
                sessionsList={sessionsList}
                decisions={nodes.map((n) => ({
                  decision_id: n.id,
                  session_id: session.sessionId,
                  tool: n.tool,
                  command: n.command || n.title,
                  status: n.verdict || n.status,
                  risk_level: n.risk,
                  created_at: n.timestamp,
                }))}
              />
            )}

            {activeNav === 'settings' && (
              <SettingsView
                missionConfig={missionConfig}
                onConfigChange={setMissionConfig}
              />
            )}
          </div>
        </div>

        {/* Right Panel: Decision Inspector & Mission Chat (Llega directamente bajo Header ocupando la esquina superior derecha) */}
        {activeNav === 'live' && (
          <DecisionInspector
            decision={currentDecision}
            activeTab={inspectorTab}
            onTabChange={setInspectorTab}
            session={session}
            isRunning={isRunning}
            isPaused={isPaused}
            events={events}
            chatMessages={chatMessages}
            onMessagesChange={setChatMessages}
            missionConfig={missionConfig}
            onConfigChange={setMissionConfig}
            onStartMission={handleStartMission}
            onPauseMission={handlePauseMission}
            onResumeMission={handleResumeMission}
            onStopMission={handleStopMission}
            onLoadDemo={handleLoadDemo}
            onApprove={handleApprove}
            onReject={handleReject}
            onExecute={handleExecute}
          />
        )}
      </div>

      {/* Modals */}
      <ProposeActionModal
        isOpen={isProposeOpen}
        onClose={() => setIsProposeOpen(false)}
        onSubmit={async (proposal) => {
          try {
            await api.proposeAction(session.sessionId, proposal);
          } catch (e) {
            console.warn('Propose action error:', e);
          }
          setIsProposeOpen(false);
        }}
      />

      <SessionsModal
        isOpen={isSessionsOpen}
        onClose={() => setIsSessionsOpen(false)}
        sessions={sessionsList}
        currentSessionId={session.sessionId}
        onSelectSession={(sid) => {
          handleLoadSession(sid);
          setIsSessionsOpen(false);
        }}
        onCreateSession={(cfg) => {
          handleStartMission(cfg);
          setIsSessionsOpen(false);
        }}
        onDeleteSession={handleDeleteSession}
        onNewCleanSession={handleNewCleanSession}
      />
    </div>
  );
}
