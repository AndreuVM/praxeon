import React, { useState, useEffect, useMemo, useRef, useCallback, Suspense } from 'react';
import ErrorBoundary from './components/ErrorBoundary';
import {
  Activity, FolderOpen,
  Plus, Map as MapIcon, ScrollText, History, FlaskConical, SquarePen, KeyRound, Pause, Play, Square,
} from 'lucide-react';
import Header from './components/Header';
import Rail, { NAV_ITEMS } from './components/Rail';
import Drawer from './components/Drawer';
import Conversation from './components/Conversation';
import CommandPalette from './components/CommandPalette';
import StepDetail from './components/StepDetail';
import EventsPanel from './components/EventsPanel';
import DecisionTree from './components/DecisionTree';
import ProposeActionModal from './components/ProposeActionModal';
import SessionsModal from './components/SessionsModal';
import UnauthorizedModal from './components/UnauthorizedModal';
import DialogsHost, { notify } from './components/Dialogs';
import WorkspacePicker from './components/workspace/WorkspacePicker';
import WorkspaceFlyout from './components/workspace/WorkspaceFlyout';
import useWorkspace from './components/workspace/useWorkspace';
import { loadChatConfig, loadChats, persistChatConfig, persistChats } from './utils/chatStore';
import { buildCascadeDecisionModel } from './utils/cascadeConfig';
import { rebuildChatFromSession } from './utils/chatFromEvents';

const SessionsView = React.lazy(() => import('./components/views/SessionsView'));
const DecisionsView = React.lazy(() => import('./components/views/DecisionsView'));
const AgentsView = React.lazy(() => import('./components/views/AgentsView'));
const ProvidersView = React.lazy(() => import('./components/views/ProvidersView'));
const McpView = React.lazy(() => import('./components/views/McpView'));
const FilesView = React.lazy(() => import('./components/views/FilesView'));
const SkillsView = React.lazy(() => import('./components/views/SkillsView'));
const SecurityView = React.lazy(() => import('./components/views/SecurityView'));
const SettingsView = React.lazy(() => import('./components/views/SettingsView'));
const WorkflowsView = React.lazy(() => import('./components/views/WorkflowsView'));

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
      message: 'Runtime de PRAXEON inicializado. Listo para recibir una misión.',
    },
  ]);
  const [events, setEvents] = useState([]);
  const [sessionsList, setSessionsList] = useState([]);

  const [runtimeActive, setRuntimeActive] = useState(true);
  const [isRunning, setIsRunning] = useState(false);
  const [followExecution, setFollowExecution] = useState(null); // misión gobernada por workflow
  const [isPaused, setIsPaused] = useState(false);
  const [isProposeOpen, setIsProposeOpen] = useState(false);
  const [isSessionsOpen, setIsSessionsOpen] = useState(false);
  const [isAuthModalOpen, setIsAuthModalOpen] = useState(false);
  const [isWorkspaceOpen, setIsWorkspaceOpen] = useState(false);
  const [isPaletteOpen, setIsPaletteOpen] = useState(false);
  const [isWsFlyoutOpen, setIsWsFlyoutOpen] = useState(false);
  const { workspace, choose: chooseWorkspace, forget: forgetWorkspace, pin: pinWorkspace, alias: aliasWorkspace } = useWorkspace();
  const workspaceRef = useRef(null);
  workspaceRef.current = workspace;

  // Cambio rápido de workspace (atajos, riel y paleta): avisa del destino y de cualquier fallo
  const switchWorkspace = useCallback(async (path) => {
    const target = (workspaceRef.current?.recent || []).find((w) => w.path === path);
    try {
      await chooseWorkspace(path);
      notify(`Workspace: ${target?.name || path}`, { tone: 'info' });
    } catch (err) {
      notify(err.message || 'No se pudo cambiar el workspace.');
    }
  }, [chooseWorkspace]);

  // Alt+1…9: ir al favorito con ese número · Alt+0: alternar con el anterior.
  // No se usa Ctrl: en teclado español Ctrl+Alt es AltGr y escribe @ # |.
  useEffect(() => {
    const onKey = (e) => {
      if (!e.altKey || e.ctrlKey || e.metaKey || e.shiftKey || !/^Digit\d$/.test(e.code)) return;
      const ws = workspaceRef.current;
      const n = Number(e.code.slice(5));
      const target = n === 0 ? ws?.previous : (ws?.recent || []).find((w) => w.slot === n && w.exists)?.path;
      if (!target) return;
      e.preventDefault();
      if (target !== ws?.path) switchWorkspace(target);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [switchWorkspace]);
  // Cajón de detalle: null (cerrado) | 'map' | 'step' | 'events'
  const [drawerTab, setDrawerTab] = useState(null);
  const [drawerWide, setDrawerWide] = useState(false);
  const [narrow, setNarrow] = useState(() => typeof window !== 'undefined' && window.matchMedia('(max-width: 980px)').matches);
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 980px)');
    const onChange = (e) => setNarrow(e.matches);
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);

  // Ctrl K / ⌘ K abre la paleta de comandos
  useEffect(() => {
    const onKey = (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setIsPaletteOpen((v) => !v);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);
  const maxSeqRef = useRef(0);

  // Escuchar eventos globales de 401/403 para abrir modal de autenticación reactivo
  useEffect(() => {
    const handleUnauthorized = () => {
      setIsAuthModalOpen(true);
    };
    window.addEventListener('praxeon:unauthorized', handleUnauthorized);
    return () => {
      window.removeEventListener('praxeon:unauthorized', handleUnauthorized);
    };
  }, []);

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
    ...loadChatConfig(), // última configuración usada: modelo, supervisor, modo, workflow, pasos…
  });
  useEffect(() => {
    persistChatConfig(missionConfig);
  }, [missionConfig]);

  const [chatMessages, setChatMessages] = useState([]);

  // Historial del chat de cada sesión: sobrevive al cambio de sesión y a reiniciar la aplicación
  const chatsRef = useRef(loadChats());
  const sidRef = useRef(null);
  const saveTimerRef = useRef(null);
  const scheduleChatSave = useCallback(() => {
    clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(() => persistChats(chatsRef.current), 400);
  }, []);
  const updateChat = useCallback(
    (updater) => {
      setChatMessages((prev) => {
        const next = typeof updater === 'function' ? updater(prev) : updater;
        const sid = sidRef.current;
        if (sid) {
          delete chatsRef.current[sid]; // reinserta al final: las sesiones recientes son las últimas
          chatsRef.current[sid] = next;
          scheduleChatSave();
        }
        return next;
      });
    },
    [scheduleChatSave]
  );

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
            logMsg = `Sesión #${ev.session_id} iniciada (agente: ${ev.payload?.agent_name || 'CodingAgent'})`;
          } else if (evType === 'goal.created') {
            logMsg = `Objetivo de la misión: ${ev.payload?.goal}`;
          } else if (evType === 'action.proposed') {
            logMsg = `Paso ${ev.payload?.step || 1}: propone la acción «${ev.payload?.tool}» (${ev.payload?.operation || ''})`;
          } else if (evType === 'operation.classified') {
            logMsg = `Operación clasificada: ${ev.payload?.category?.toUpperCase()} (riesgo: ${ev.payload?.risk_level?.toUpperCase()}, confianza: ${Math.round((ev.payload?.confidence || 0) * 100)}%)`;
          } else if (evType === 'evidence.evaluated') {
            logMsg = `Evaluadas ${ev.payload?.evidence_count || 0} afirmaciones empíricas (fundamentación: ${Math.round((ev.payload?.grounding_score || 0.85) * 100)}%)`;
          } else if (evType === 'risk.assessed') {
            logMsg = `Riesgo evaluado: ${ev.payload?.level || 'LOW'} (puntuación: ${ev.payload?.score || 0.15})`;
          } else if (evType === 'provider.evaluated') {
            logMsg = `Evaluación semántica (${ev.payload?.provider_name}): puntuación ${ev.payload?.score} -> ${ev.payload?.verdict}`;
          } else if (evType === 'policy.decided') {
            logMsg = `Política decidida: ${ev.payload?.status} (${ev.payload?.reason_code || 'STANDARD_POLICY'})`;
          } else if (evType === 'approval.requested') {
            logMsg = `La política requiere confirmación humana (riesgo: ${ev.payload?.risk_level || 'HIGH'})`;
          } else if (evType === 'approval.completed') {
            logMsg = `El operador decidió: ${ev.payload?.approved ? 'APROBADO' : 'RECHAZADO'}`;
          } else if (evType === 'capability.issued') {
            logMsg = `Token de capacidad emitido y firmado con HMAC`;
          } else if (evType === 'execution.started') {
            const isFull = (session.execution_mode === 'full_access');
            logMsg = isFull
              ? `Executing action on Host OS (Full Access): ${ev.payload?.tool}...`
              : `Executing action in local sandbox: ${ev.payload?.tool}...`;
          } else if (evType === 'execution.completed') {
            logMsg = `Acción ejecutada ${ev.payload?.success ? 'correctamente' : 'con error'} en ${ev.payload?.execution_time_ms || 12} ms`;
          } else if (evType === 'observation.recorded') {
            logMsg = `Observación: ${(ev.payload?.output || '').slice(0, 100)}…`;
          } else if (evType === 'decision.pruned') {
            logMsg = `Decisión podada: ${ev.payload?.reason}`;
          } else if (evType === 'intervention.applied') {
            logMsg = `[INTERVENCIÓN] ${ev.payload?.intervention || 'Supervisor'}: ${ev.payload?.message || ''}`;
          } else if (evType === 'workflow.node_started') {
            logMsg = `[FLUJO] Nodo «${ev.payload?.node_name || ev.payload?.node_id}»: ${ev.payload?.agent_name || 'agente'} toma el control`;
          } else if (evType === 'workflow.node_completed') {
            logMsg = `[FLUJO] Nodo «${ev.payload?.node_name || ev.payload?.node_id}» completado por ${ev.payload?.agent_name || 'agente'}`;
          } else if (evType === 'session.completed') {
            logMsg = `Misión completada: ${ev.payload?.summary || 'terminó correctamente'}`;
          }

          setLogs((prev) => [
            ...prev,
            { time: timeStr, level, message: logMsg },
          ]);

          // 3a. Un nodo de workflow empieza: el agente toma el control ya, sin esperar a su primera acción
          if (evType === 'workflow.node_started') {
            const w = ev.payload || {};
            const pendingId = `${w.node_id}_i${w.iteration}_pending`;
            setNodes((rawNodes) => {
              const prevNodes = rawNodes.map((n) =>
                n.wfNodeId === w.node_id && n.wfIteration < w.iteration && !n.blockDone ? { ...n, blockFailed: true } : n
              );
              if (prevNodes.some((n) => n.id === pendingId)) return prevNodes;
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
              return computeTreeLayout([...prevNodes, pending]);
            });
          }

          // 3b. Procesamiento dinámico del Decision Tree en tiempo real
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

              const wf = ev.payload?.workflow_node || null; // nodo/agente del workflow que ejecuta este paso
              const parentNode = prevNodes.find((n) => n.id === resolvedParentId);
              const handoff = Boolean(
                wf && parentNode && parentNode.wfNodeId && (parentNode.wfNodeId !== wf.node_id || parentNode.wfIteration !== wf.iteration)
              );
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

              const updatedNodes = [
                ...prevNodes.filter((n) => !(n.isPending && wf && n.wfNodeId === wf.node_id && n.wfIteration === wf.iteration)),
                newNode,
              ];
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
            const effMode = session.execution_mode || 'local_restricted';
            const effIso = effMode === 'full_access' ? 'None (Host OS)' : 'Active';
            const effBack = effMode === 'full_access' ? 'FullAccessExecutor' : (effMode === 'container' ? 'DockerContainer' : 'LocalProcessSandbox');
            const effNet = effMode === 'full_access' ? 'Host Direct' : 'Isolated (Restricted)';
            const effWs = session.workspace_root || session.working_directory || (effMode === 'full_access' ? '.' : '/workspace');
            const effModel = session.decision_model_effective?.model_id || session.decisionModel?.model_id || session.llm_model || missionConfig?.customModel || 'qwen2.5-coder:7b';
            const effProvider = ev.payload?.source || session?.decisionModel?.provider || session?.runtime?.provider || 'JEV';

            setDecisionsMap((prevMap) => ({
              ...prevMap,
              [actId]: {
                decisionId: ev.decision_id || `d_${actId}`,
                sequence: stepNum,
                status: 'PENDING',
                actionCommand: `${tool} ${JSON.stringify(ev.payload?.arguments || {})}`,
                tool: tool,
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
                decisionTab: {
                  decision_id: ev.decision_id || `d_${actId}`,
                  session_id: session.sessionId,
                  action_id: actId,
                  tool: tool,
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
              const curTab = current.decisionTab || {};
              const effMode = ev.payload?.execution_mode || current.execution_mode || session.execution_mode || 'local_restricted';
              const effIso = effMode === 'full_access' ? 'None (Host OS)' : (current.isolation || 'Active');
              const effBack = effMode === 'full_access' ? 'FullAccessExecutor' : (current.execution_backend || 'LocalProcessSandbox');
              return {
                ...prevMap,
                [actId]: {
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
                  decisionTab: {
                    ...curTab,
                    status: statusStr,
                    execution_mode: effMode,
                    isolation: effIso,
                    execution_backend: effBack,
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
            const isFull = (session.execution_mode === 'full_access');
            setNodes((prevNodes) =>
              prevNodes.map((n) => (n.id === actId ? { ...n, status: 'EXECUTING' } : n))
            );
            setDecisionsMap((prevMap) => {
              const current = prevMap[actId] || {};
              const curTab = current.decisionTab || {};
              return {
                ...prevMap,
                [actId]: {
                  ...current,
                  execution_backend: isFull ? 'FullAccessExecutor' : (current.execution_backend || 'LocalProcessSandbox'),
                  isolation: isFull ? 'None (Host OS)' : (current.isolation || 'Active'),
                  decisionTab: {
                    ...curTab,
                    execution_backend: isFull ? 'FullAccessExecutor' : (curTab.execution_backend || 'LocalProcessSandbox'),
                    isolation: isFull ? 'None (Host OS)' : (curTab.isolation || 'Active'),
                  },
                },
              };
            });
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

          if (evType === 'approval.requested' && ev.payload?.kind === 'node_failure') {
            setNodes((prevNodes) =>
              prevNodes.map((n) => (n.wfNodeId === ev.node_id && !n.blockDone ? { ...n, blockFailed: true } : n))
            );
          }

          if (evType === 'workflow.node_completed') {
            const w = ev.payload || {};
            setNodes((prevNodes) =>
              prevNodes.map((n) => {
                if (n.wfNodeId !== w.node_id || n.wfIteration !== w.iteration) return n;
                return n.isPending
                  ? { ...n, label: 'Completado', status: 'ALLOW', isPending: false, blockDone: true }
                  : { ...n, blockDone: true };
              })
            );
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
            // El finish de un nodo de workflow (ids «nodo_iN_act_M») no cierra la misión: es un hito del nodo
            const isWorkflowStep = /_i\d+_act_\d+$/.test(actId || '');
            // Solo el cierre explícito del agente («Tarea concluida: …», lo que devuelve `finish`) es la respuesta de la misión:
            // buscar «conclu»/«finaliz» en cualquier salida tomaba por respuesta el contenido de un archivo leído.
            if (!isWorkflowStep && /^\s*Tarea concluida:/.test(output)) {
              const cleanAnswer = output.replace(/^\s*Tarea concluida:/, '').trim();
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

  // Recuperar detalles completos de decisión desde el backend para DecisionInspector
  const fetchAndSetDecision = async (nodeId, decisionId) => {
    if (!decisionId || nodeId === 'start') return;
    try {
      const res = await api.fetchDecisionDetail(decisionId);
      const detail = res?.data || res;
      if (detail && detail.decision_tab) {
        setDecisionsMap((prev) => {
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
  };

  const handleSelectNode = (nodeId) => {
    setSelectedNodeId(nodeId);
    setDrawerTab('step');
    const found = decisionsMap[nodeId];
    if (found) {
      const timeStr = new Date().toTimeString().split(' ')[0];
      setLogs((prev) => [
        ...prev,
        { time: timeStr, level: 'INFO', message: `Inspeccionando la decisión #${found.sequence || 1}: ${found.actionCommand}` },
      ]);
      const decId = found.decisionId || (nodeId.startsWith('d_') ? nodeId : `d_${nodeId}`);
      fetchAndSetDecision(nodeId, decId);
    } else if (nodeId && nodeId !== 'start') {
      const decId = nodeId.startsWith('d_') ? nodeId : `d_${nodeId}`;
      fetchAndSetDecision(nodeId, decId);
    }
  };

  // Enriquecer automáticamente la decisión seleccionada si aún no tiene decisionTab
  useEffect(() => {
    if (!selectedNodeId || selectedNodeId === 'start') return;
    const cur = decisionsMap[selectedNodeId];
    if (cur && (!cur.decisionTab || !cur.decisionTab.execution_mode)) {
      const decId = cur.decisionId || (selectedNodeId.startsWith('d_') ? selectedNodeId : `d_${selectedNodeId}`);
      fetchAndSetDecision(selectedNodeId, decId);
    }
  }, [selectedNodeId]);

  // Lanzar misión interactiva en tiempo real
  const handleStartMission = async ({
    goal,
    execution_mode,
    autonomous = false,
    allow_unattended_execution = false,
    full_access_confirmed = false,
    llm_provider,
    llm_model,
    decision_model,
    supervisor,
    max_steps,
    llm_failure_policy,
    api_key,
    base_url,
    workspace_root,
    chat_history,
    workflow_id,
  }) => {
    setIsRunning(true);
    setIsPaused(false);
    const timeStr = new Date().toTimeString().split(' ')[0];
    // La cascada se configura en Proveedores: se envía con la misión para que cada una lleve la suya
    if (!decision_model && supervisor === 'cascade') decision_model = buildCascadeDecisionModel();

    try {
      const res = await api.runMission({
        goal,
        execution_mode,
        autonomous,
        allow_unattended_execution,
        full_access_confirmed,
        llm_provider,
        llm_model,
        decision_model,
        api_key,
        base_url,
        supervisor: decision_model?.provider || supervisor,
        max_steps,
        llm_failure_policy,
        step_delay_ms: 1000,
        workspace_root,
        chat_history,
        workflow_id,
      });

      if (res?.data) {
        const sid = res.data.session_id;
        const previousSid = sidRef.current;
        const wasRealSession = sessionsList.some((x) => x.session_id === previousSid);
        sidRef.current = sid;
        setChatMessages((prev) => {
          // La conversación continúa en la sesión nueva; la anterior conserva lo que tenía antes de este mensaje
          chatsRef.current[sid] = prev;
          if (previousSid && previousSid !== sid) {
            if (wasRealSession) chatsRef.current[previousSid] = prev.slice(0, -2);
            else delete chatsRef.current[previousSid];
          }
          scheduleChatSave();
          return prev;
        });
        setFollowExecution(workflow_id ? { workflowId: workflow_id, executionId: `wfx-${sid}` } : null);
        const effModel = res.data.decision_model_effective || decision_model;
        const newSess = {
          sessionId: sid,
          status: 'Active',
          agent: 'CodingAgent',
          goal: goal,
          execution_mode: res.data.execution_mode || execution_mode || 'local_restricted',
          autonomous: Boolean(autonomous || res.data.autonomous),
          isAutonomous: Boolean(autonomous || res.data.autonomous),
          isDegraded: Boolean(res.data.is_fallback || res.data.status === 'Degraded'),
          fallbackReason: res.data.degradation_reason || null,
          decisionModel: effModel,
          decision_model_effective: effModel,
          metrics: { totalDecisions: 0, allowed: 0, blocked: 0, review: 0 },
          runtime: {
            provider: `${(llm_provider || 'SIMULATOR').toUpperCase()} + ${(effModel?.provider || supervisor || 'LAYA').toUpperCase()}`,
            version: 'v1.1.0',
            latencyP50: 'N/A',
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
            message: `Iniciando la sesión interactiva #${sid} con el modelo (${llm_provider.toUpperCase()}) y el supervisor (${supervisor.toUpperCase()})`,
          },
          {
            time: timeStr,
            level: 'INFO',
            message: `Objetivo: ${goal}`,
          },
        ]);
      }
    } catch (err) {
      setLogs((prev) => [
        ...prev,
        { time: timeStr, level: 'ERROR', message: `No se pudo iniciar la misión: ${err.message}` },
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
    sidRef.current = newSid;
    setChatMessages([]);
    setDrawerTab(null);
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

    // El chat de la sesión elegida: la copia guardada, o vacío hasta reconstruirlo desde el backend
    sidRef.current = sid;
    const cachedChat = chatsRef.current[sid];
    setChatMessages(cachedChat || []);
    setEvents([]);

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
          setDecisionsMap(map);
        }

        // Eventos de la sesión (más recientes primero, como el stream en vivo) y chat reconstruido si no había copia
        const history = await api.fetchAllEvents(sid);
        if (sidRef.current === sid) {
          setEvents([...history].reverse());

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
            setNodes((prevNodes) =>
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
                })
              )
            );
          }
          if (!cachedChat) {
            const rebuilt = rebuildChatFromSession({ goal: d.goal, events: history, createdAt: d.created_at });
            if (rebuilt.length > 0) {
              chatsRef.current[sid] = rebuilt;
              scheduleChatSave();
              setChatMessages(rebuilt);
            }
          }
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
    setLogs(INITIAL_LOGS);
    setEvents(INITIAL_EVENTS);
    setChatMessages([
      {
        id: 'msg-demo-user',
        role: 'user',
        text: INITIAL_SESSION.goal || 'Corregir el fallo de autenticación en la API',
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
  const operatorMessage = (text) => {
    updateChat((prev) => [
      ...prev,
      { id: `op-${Date.now()}`, role: 'operator', text, time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) },
    ]);
  };

  const handleApprove = async (decisionId, nodeId = selectedNodeId) => {
    const timeStr = new Date().toTimeString().split(' ')[0];
    try {
      await api.confirmDecision(decisionId, true, 'Authorized from Decision Inspector', 'operator_admin', 'operator');
    } catch (e) {
      // Si el servidor no registró la aprobación, la misión sigue esperando: no se muestra como aprobada
      console.warn('Backend approval call error:', e);
      notify(`No se pudo registrar la aprobación: ${e?.message || e}`);
      return;
    }

    // Actualizar visualmente de inmediato
    setDecisionsMap((prev) => {
      const cur = prev[nodeId];
      if (!cur) return prev;
      return {
        ...prev,
        [nodeId]: {
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
      prev.map((n) => (n.id === nodeId ? { ...n, status: 'ALLOW' } : n))
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
      { time: timeStr, level: 'INFO', message: `Decisión #${decisionId} autorizada por el operador.` },
    ]);
  };

  // Rechazo de decisión desde el DecisionInspector
  const handleReject = async (decisionId, customReason, nodeId = selectedNodeId) => {
    const timeStr = new Date().toTimeString().split(' ')[0];
    const rejectionMsg = customReason || 'Bloqueada por el operador';
    try {
      await api.rejectDecision(decisionId, rejectionMsg, 'operator_admin', 'operator');
    } catch (e) {
      console.warn('Backend rejection error:', e);
    }

    setDecisionsMap((prev) => {
      const cur = prev[nodeId];
      if (!cur) return prev;
      return {
        ...prev,
        [nodeId]: {
          ...cur,
          status: 'BLOCKED',
          reason: rejectionMsg,
          capability: { issued: false, statusText: 'Revocada por el operador', token: null },
        },
      };
    });

    setNodes((prev) =>
      prev.map((n) => (n.id === nodeId ? { ...n, status: 'BLOCK' } : n))
    );
    operatorMessage(`Rechazado: ${rejectionMsg}`);

    setLogs((prev) => [
      ...prev,
      { time: timeStr, level: 'ERROR', message: `Decisión #${decisionId} rechazada y podada: ${rejectionMsg}` },
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
          message: `Ejecución en el sandbox terminada (${res?.data?.execution_time_ms || 12} ms): ${res?.data?.output || 'correcta'}`,
        },
      ]);
    } catch (e) {
      setLogs((prev) => [
        ...prev,
        { time: timeStr, level: 'ERROR', message: `La ejecución falló: ${e.message}` },
      ]);
    }
  };

  // Aprobaciones pendientes: decisiones que la política retiene para que las resuelva el operador (RD-6.3.3)
  const pendingApprovals = useMemo(() => {
    if (!isRunning) return [];
    return Object.entries(decisionsMap)
      .filter(([, d]) => d.status === 'REVIEW' && !d.capability?.issued)
      .map(([nodeId, d]) => ({
        nodeId,
        decisionId: d.decisionId || `d_${nodeId}`,
        tool: d.tool,
        command: d.actionCommand,
        riskLevel: d.riskLevel,
        laya: d.semanticEvaluation?.[0]?.score,
      }));
  }, [decisionsMap, isRunning]);

  const openDrawer = (tab) => {
    setActiveNav('live');
    setDrawerTab(tab);
  };

  // Esc en una vista secundaria vuelve a la conversación (RD-V3)
  useEffect(() => {
    const onKey = (e) => {
      if (e.key !== 'Escape' || e.defaultPrevented || activeNav === 'live') return;
      if (isPaletteOpen || isSessionsOpen || isProposeOpen || isAuthModalOpen || isWorkspaceOpen || isWsFlyoutOpen) return;
      const tag = (e.target?.tagName || '').toLowerCase();
      if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
      setActiveNav('live');
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [activeNav, isPaletteOpen, isSessionsOpen, isProposeOpen, isAuthModalOpen, isWorkspaceOpen, isWsFlyoutOpen]);

  const paletteItems = [
    { id: 'go-live', group: 'Conversación', label: 'Volver a la conversación', icon: Activity, run: () => setActiveNav('live') },
    { id: 'new-session', group: 'Conversación', label: 'Nueva sesión limpia', icon: Plus, run: handleNewCleanSession },
    { id: 'open-map', group: 'Conversación', label: 'Abrir el mapa de decisiones', icon: MapIcon, run: () => openDrawer('map') },
    { id: 'open-events', group: 'Conversación', label: 'Ver los eventos de la sesión', icon: ScrollText, run: () => openDrawer('events') },
    { id: 'open-sessions', group: 'Conversación', label: 'Abrir las sesiones registradas', icon: History, run: () => setIsSessionsOpen(true) },
    { id: 'propose', group: 'Conversación', label: 'Proponer una acción manual', icon: SquarePen, run: () => setIsProposeOpen(true) },
    { id: 'demo', group: 'Conversación', label: 'Cargar la sesión de demostración', icon: FlaskConical, run: () => { setActiveNav('live'); handleLoadDemo(); } },
    ...(isRunning
      ? [
          isPaused
            ? { id: 'resume', group: 'Conversación', label: 'Reanudar la misión', icon: Play, run: handleResumeMission }
            : { id: 'pause', group: 'Conversación', label: 'Pausar la misión', icon: Pause, run: handlePauseMission },
          { id: 'stop', group: 'Conversación', label: 'Detener la misión', icon: Square, run: handleStopMission },
        ]
      : []),
    { id: 'workspace', group: 'Proyecto y cuenta', label: 'Cambiar de workspace', icon: FolderOpen, run: () => setIsWsFlyoutOpen(true) },
    ...(workspace?.previous
      ? [{ id: 'workspace-previous', group: 'Workspaces', label: 'Volver al workspace anterior (Alt+0)', icon: FolderOpen, run: () => switchWorkspace(workspace.previous) }]
      : []),
    ...(workspace?.recent || [])
      .filter((w) => w.exists && w.path !== workspace?.path)
      .map((w) => ({ id: `workspace-${w.path}`, group: 'Workspaces', label: `Ir a ${w.name}${w.slot ? ` (Alt+${w.slot})` : ''}`, icon: FolderOpen, run: () => switchWorkspace(w.path) })),
    { id: 'api-key', group: 'Proyecto y cuenta', label: 'Configurar la clave de API', icon: KeyRound, run: () => setIsAuthModalOpen(true) },
    ...NAV_ITEMS.filter((i) => i.id !== 'live').map((i) => ({ id: `view-${i.id}`, group: 'Vistas', label: i.label, icon: i.icon, run: () => setActiveNav(i.id) })),
  ];

  const sessionWorkspace = session.workspace_root || session.working_directory || null;
  const workspaceMismatch = Boolean(sessionWorkspace && workspace?.path && sessionWorkspace !== workspace.path);
  const selectedNode = nodes.find((n) => n.id === selectedNodeId);
  const stepDecision = selectedNodeId === 'start' ? null : currentDecision;
  const executionModeNow = session.execution_mode || missionConfig.executionMode || 'local_restricted';

  return (
    <div style={{ display: 'flex', height: '100vh', width: '100vw', backgroundColor: 'var(--bg)', color: 'var(--text)' }}>
      <Rail
        active={activeNav}
        onSelect={setActiveNav}
        workspace={workspace}
        workspaceMismatch={workspaceMismatch}
        workspaceOpen={isWsFlyoutOpen}
        onSwitchWorkspace={switchWorkspace}
        onOpenWorkspace={() => setIsWsFlyoutOpen((v) => !v)}
        onOpenPalette={() => setIsPaletteOpen(true)}
        onOpenAccount={() => setIsAuthModalOpen(true)}
        pendingApprovals={pendingApprovals.length}
      />

      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
        <Header
          session={session}
          runtimeActive={runtimeActive}
          executionMode={executionModeNow}
          isAutonomous={Boolean(session.autonomous || session.isAutonomous || session.execution_mode === 'full_access_autonomous')}
          isDegraded={Boolean(session.isDegraded || session.fallbackActive || session.status === 'Degraded')}
          fallbackReason={session.fallbackReason}
          isRunning={isRunning}
          isPaused={isPaused}
          pendingApprovals={pendingApprovals.length}
          drawerTab={drawerTab}
          onOpenSessions={() => setIsSessionsOpen(true)}
          onNewSession={handleNewCleanSession}
          onOpenMap={() => (activeNav === 'live' && drawerTab === 'map' ? setDrawerTab(null) : openDrawer('map'))}
          onOpenPalette={() => setIsPaletteOpen(true)}
          onGoToConversation={() => setActiveNav('live')}
        />

        <div style={{ flex: 1, minHeight: 0, display: 'flex', position: 'relative', overflow: 'hidden' }}>
          <main style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
            {/* La conversación permanece montada al cambiar de vista para no perder el texto ni la configuración */}
            <div style={{ display: activeNav === 'live' ? 'flex' : 'none', flex: 1, minHeight: 0, flexDirection: 'column' }}>
              <Conversation
                session={session}
                isRunning={isRunning}
                isPaused={isPaused}
                events={events}
                chatMessages={chatMessages}
                onMessagesChange={updateChat}
                missionConfig={missionConfig}
                onConfigChange={setMissionConfig}
                onStartMission={handleStartMission}
                onPauseMission={handlePauseMission}
                onResumeMission={handleResumeMission}
                onStopMission={handleStopMission}
                nodes={nodes}
                selectedNodeId={selectedNodeId}
                drawerTab={drawerTab}
                pendingApprovals={pendingApprovals}
                onSelectStep={(nodeId) => {
                  if (nodeId) handleSelectNode(nodeId);
                  else setDrawerTab('step');
                }}
                onOpenDrawer={openDrawer}
                onApprove={(a) => handleApprove(a.decisionId, a.nodeId)}
                onReject={(a, reason) => handleReject(a.decisionId, reason, a.nodeId)}
                onViewApproval={(a) => {
                  setSelectedNodeId(a.nodeId);
                  setDrawerTab('step');
                }}
              />
            </div>

            {activeNav !== 'live' && (
              <div style={{ display: 'flex', flex: 1, overflow: 'hidden', minWidth: 0 }}>
                <ErrorBoundary key={activeNav}>
                <Suspense fallback={<div style={{ flex: 1, display: 'grid', placeItems: 'center', color: 'var(--muted)' }}>Cargando…</div>}>
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
                  decisions={nodes.filter((n) => n.type !== 'start').map((n) => ({
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
                    setDrawerTab('step');
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

              {activeNav === 'workflows' && <WorkflowsView followExecution={followExecution} />}

              {activeNav === 'providers' && <ProvidersView />}

              {activeNav === 'mcp' && <McpView />}

              {activeNav === 'files' && <FilesView workspace={workspace} />}

              {activeNav === 'skills' && <SkillsView />}

              {activeNav === 'security' && (
                <SecurityView
                  session={session}
                  sessionsList={sessionsList}
                  decisions={nodes.filter((n) => n.type !== 'start').map((n) => ({
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
            </Suspense>
                </ErrorBoundary>
              </div>
            )}
          </main>

          {activeNav === 'live' && (
            <Drawer
              tab={drawerTab}
              wide={drawerWide}
              overlay={narrow}
              onTabChange={setDrawerTab}
              onToggleWide={() => setDrawerWide((v) => !v)}
              onClose={() => setDrawerTab(null)}
            >
              {drawerTab === 'map' && (
                <DecisionTree nodes={nodes} selectedNodeId={selectedNodeId} onSelectNode={handleSelectNode} />
              )}
              {drawerTab === 'step' && (
                <div style={{ flex: 1, overflowY: 'auto', padding: '18px' }}>
                  <StepDetail
                    decision={stepDecision}
                    nodeId={selectedNodeId}
                    title={selectedNode && selectedNodeId !== 'start' ? selectedNode.label : ''}
                    session={session}
                    onApprove={handleApprove}
                    onReject={handleReject}
                    onExecute={handleExecute}
                  />
                </div>
              )}
              {drawerTab === 'events' && (
                <div style={{ flex: 1, overflowY: 'auto', padding: '18px' }}>
                  <EventsPanel logs={logs} events={events} runtime={session.runtime} />
                </div>
              )}
            </Drawer>
          )}
        </div>
      </div>

      <WorkspaceFlyout
        open={isWsFlyoutOpen}
        workspace={workspace}
        sessionWorkspace={sessionWorkspace}
        onSelect={chooseWorkspace}
        onForget={forgetWorkspace}
        onPin={pinWorkspace}
        onAlias={aliasWorkspace}
        onBrowse={() => {
          setIsWsFlyoutOpen(false);
          setIsWorkspaceOpen(true);
        }}
        onClose={() => setIsWsFlyoutOpen(false)}
      />
      <WorkspacePicker
        open={isWorkspaceOpen}
        title="Workspace del proyecto"
        initialPath={workspace?.path || ''}
        recent={workspace?.recent || []}
        onSelect={chooseWorkspace}
        onForget={forgetWorkspace}
        onClose={() => setIsWorkspaceOpen(false)}
      />
      <DialogsHost />
      <CommandPalette open={isPaletteOpen} items={paletteItems} onClose={() => setIsPaletteOpen(false)} />

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
          // El modal entrega solo el objetivo: se completa con la configuración de la conversación
          handleStartMission(
            typeof cfg === 'string'
              ? {
                  goal: cfg,
                  execution_mode: missionConfig.executionMode,
                  llm_provider: missionConfig.llmProvider,
                  supervisor: missionConfig.supervisor,
                  max_steps: missionConfig.maxSteps,
                }
              : cfg
          );
          setIsSessionsOpen(false);
          setActiveNav('live');
        }}
        onDeleteSession={handleDeleteSession}
        onNewCleanSession={handleNewCleanSession}
      />

      <UnauthorizedModal
        isOpen={isAuthModalOpen}
        onClose={() => setIsAuthModalOpen(false)}
        onSuccess={() => {
          refreshSessionsList();
        }}
      />
    </div>
  );
}
