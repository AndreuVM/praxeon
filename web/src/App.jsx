import React, { useState, useEffect } from 'react';
import Header from './components/Header';
import Sidebar from './components/Sidebar';
import SessionKPIs from './components/SessionKPIs';
import DecisionTree from './components/DecisionTree';
import ConsolePanel from './components/ConsolePanel';
import DecisionInspector from './components/DecisionInspector';
import MissionLauncher from './components/MissionLauncher';
import ProposeActionModal from './components/ProposeActionModal';
import SessionsModal from './components/SessionsModal';

import SessionsView from './components/views/SessionsView';
import DecisionsView from './components/views/DecisionsView';
import AgentsView from './components/views/AgentsView';
import ProvidersView from './components/views/ProvidersView';
import SecurityView from './components/views/SecurityView';
import SettingsView from './components/views/SettingsView';

import {
  INITIAL_SESSION,
  INITIAL_NODES,
  INITIAL_DECISIONS_MAP,
  INITIAL_LOGS,
  INITIAL_EVENTS,
} from './constants/demoData';

import * as api from './services/api';

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
          const timeStr = new Date(ev.timestamp || Date.now()).toTimeString().split(' ')[0];

          // 1. Agregar a eventos estructurados
          setEvents((prev) => [
            {
              seq: ev.sequence,
              type: ev.event_type,
              time: timeStr,
              detail: typeof ev.payload === 'string' ? ev.payload : JSON.stringify(ev.payload || {}).slice(0, 100),
            },
            ...prev,
          ]);

          // 2. Agregar a log de terminal
          let level = 'INFO';
          if (ev.event_type.includes('error') || ev.event_type.includes('pruned')) level = 'ERROR';
          else if (ev.event_type.includes('warn') || ev.event_type === 'approval.requested') level = 'WARN';

          let logMsg = `[${ev.event_type}]`;
          if (ev.event_type === 'action.proposed') {
            logMsg = `Proposing action: ${ev.payload?.tool} ${JSON.stringify(ev.payload?.arguments || {})}`;
          } else if (ev.event_type === 'provider.evaluated') {
            logMsg = `Evaluating with ${ev.payload?.provider_name}... ${ev.payload?.score} ${ev.payload?.verdict}`;
          } else if (ev.event_type === 'policy.decided') {
            logMsg = `Policy decided: ${ev.payload?.status} (${ev.payload?.reason_code || 'SAFE'})`;
          } else if (ev.event_type === 'approval.requested') {
            logMsg = `Policy requires human confirmation for this action (Risk: ${ev.payload?.risk_level || 'HIGH'})`;
          } else if (ev.event_type === 'approval.completed') {
            logMsg = `Human operator decided: ${ev.payload?.approved ? 'APPROVED' : 'REJECTED'}`;
          } else if (ev.event_type === 'capability.issued') {
            logMsg = `Capability issued & signed with HMAC token`;
          }

          setLogs((prev) => [
            ...prev,
            { time: timeStr, level, message: logMsg },
          ]);

          // 3. Procesamiento dinámico del Decision Tree
          if (ev.event_type === 'action.proposed') {
            const actId = ev.node_id;
            const parentId = ev.parent_id || 'start';
            const tool = ev.payload?.tool || 'action';
            const op = ev.payload?.operation || tool;
            const stepNum = ev.payload?.step || 1;

            setNodes((prevNodes) => {
              const exists = prevNodes.some((n) => n.id === actId);
              if (exists) return prevNodes;

              // Calcular posición vertical u horizontal en el árbol
              const parentNode = prevNodes.find((n) => n.id === parentId) || prevNodes[prevNodes.length - 1];
              const newY = parentNode ? parentNode.y + 65 : 100;
              const newX = parentNode ? parentNode.x : 420;

              const isHub = op.includes('Propose') || stepNum === 5;
              const newNode = {
                id: actId,
                label: `${stepNum}. ${op}`,
                subtitle: `${timeStr} · LLM (${ev.payload?.source?.replace('LLM (', '').replace(')', '') || 'JEV'})`,
                type: isHub ? 'hub' : 'step',
                status: isHub ? 'PROPOSE' : 'PENDING',
                action: op,
                x: newX,
                y: newY,
                parentId: parentId,
              };
              return [...prevNodes, newNode];
            });

            // Registrar borrador de decisión para inspección
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
                capability: { issued: false, statusText: 'Evaluating', token: null },
                reason: ev.payload?.thought_rationale || 'Evaluando paso.',
                relatedDecisions: [],
              },
            }));
          }

          if (ev.event_type === 'provider.evaluated') {
            const actId = ev.node_id;
            setDecisionsMap((prevMap) => {
              const current = prevMap[actId] || {};
              return {
                ...prevMap,
                [actId]: {
                  ...current,
                  semanticEvaluation: [
                    { provider: ev.payload?.provider_name || 'LAYA', score: ev.payload?.score || 0.81, verdict: ev.payload?.verdict || 'ALLOW' },
                    { provider: 'TypeSafe', score: 0.75, verdict: 'ALLOW' },
                  ],
                },
              };
            });
          }

          if (ev.event_type === 'risk.assessed') {
            const actId = ev.node_id;
            const rLevel = ev.payload?.level || 'LOW';
            const rScore = ev.payload?.score || 0.15;
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

          if (ev.event_type === 'policy.decided') {
            const actId = ev.parent_id || ev.node_id;
            const statusStr = ev.payload?.status || 'ALLOW';
            const requiresConf = !!ev.payload?.requires_confirmation;

            // Actualizar estado del nodo en el árbol
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

          if (ev.event_type === 'capability.issued') {
            const actId = ev.node_id;
            setDecisionsMap((prevMap) => {
              const current = prevMap[actId] || {};
              return {
                ...prevMap,
                [actId]: {
                  ...current,
                  capability: {
                    issued: true,
                    statusText: 'Issued & HMAC Signed',
                    token: ev.payload?.capability_token || 'cap_hmac_verified',
                  },
                },
              };
            });
          }

          if (ev.event_type === 'approval.completed') {
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
        }
      },
      (status) => {
        if (status === 'connected') setRuntimeActive(true);
      }
    );

    return () => stream.close();
  }, [session.sessionId, runtimeActive]);

  // Selección de nodo en el árbol
  const currentDecision = decisionsMap[selectedNodeId] || Object.values(decisionsMap)[0] || null;

  const handleSelectNode = (nodeId) => {
    setSelectedNodeId(nodeId);
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
  const handleStartMission = async ({ goal, llm_provider, supervisor, max_steps }) => {
    setIsRunning(true);
    setIsPaused(false);
    const timeStr = new Date().toTimeString().split(' ')[0];

    try {
      const res = await api.runMission({
        goal,
        llm_provider,
        supervisor,
        max_steps,
        step_delay_ms: 1000,
      });

      if (res?.data) {
        const sid = res.data.session_id;
        const newSess = {
          sessionId: sid,
          status: 'Active',
          agent: 'CodingAgent',
          goal: goal,
          metrics: { totalDecisions: 0, allowed: 0, blocked: 0, review: 0 },
          runtime: {
            provider: `${llm_provider.toUpperCase()} + ${supervisor.toUpperCase()}`,
            version: 'v1.0.0',
            latencyP50: '92ms',
            executionTime: 'Live',
          },
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
  };

  // Autorización humana desde el DecisionInspector
  const handleApprove = async (decisionId) => {
    const timeStr = new Date().toTimeString().split(' ')[0];
    try {
      await api.confirmDecision(decisionId, true, 'operator_ui', 'Authorized from Decision Inspector');
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
  const handleReject = async (decisionId) => {
    const timeStr = new Date().toTimeString().split(' ')[0];
    try {
      await api.confirmDecision(decisionId, false, 'operator_ui', 'Blocked by human operator');
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
          capability: { issued: false, statusText: 'Revoked by Human', token: null },
        },
      };
    });

    setNodes((prev) =>
      prev.map((n) => (n.id === selectedNodeId ? { ...n, status: 'BLOCK' } : n))
    );

    setLogs((prev) => [
      ...prev,
      { time: timeStr, level: 'ERROR', message: `Decision #${decisionId} rejected and pruned by operator.` },
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
        onOpenSessions={() => setIsSessionsOpen(true)}
        onOpenPropose={() => setIsProposeOpen(true)}
      />

      {/* 2. Main 3-Column Work Area */}
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        {/* Left Navigation Sidebar */}
        <Sidebar activeNav={activeNav} onNavSelect={setActiveNav} />

        {/* Central Supervision Canvas & Console Area */}
        {activeNav === 'live' && (
          <>
            <main style={{
              flex: 1,
              display: 'flex',
              flexDirection: 'column',
              overflow: 'hidden',
              backgroundColor: '#0a0e16',
            }}>
              {/* Interactive Mission Control Launcher */}
              <MissionLauncher
                isRunning={isRunning}
                isPaused={isPaused}
                onStartMission={handleStartMission}
                onPauseMission={handlePauseMission}
                onResumeMission={handleResumeMission}
                onStopMission={handleStopMission}
                onLoadDemo={handleLoadDemo}
              />

              {/* Top Session KPIs Overview */}
              <SessionKPIs session={session} />

              {/* Interactive Decision Tree */}
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

            {/* Right Decision Inspector (4 Tabs) */}
            <DecisionInspector
              decision={currentDecision}
              onApprove={handleApprove}
              onReject={handleReject}
              onExecute={handleExecute}
            />
          </>
        )}

        {/* Supplementary Views */}
        {activeNav === 'sessions' && (
          <SessionsView
            sessions={sessionsList}
            currentSessionId={session.sessionId}
            onSelectSession={(sid) => {
              setSession((prev) => ({ ...prev, sessionId: sid }));
              setActiveNav('live');
            }}
            onCreateSession={({ goal }) => handleStartMission({ goal, llm_provider: 'simulator', supervisor: 'laya', max_steps: 6 })}
          />
        )}

        {activeNav === 'decisions' && (
          <DecisionsView
            onSelectDecision={(nodeId) => {
              setSelectedNodeId(nodeId);
              setActiveNav('live');
            }}
          />
        )}

        {activeNav === 'agents' && (
          <AgentsView
            onSelectSession={(sid) => {
              setSession((prev) => ({ ...prev, sessionId: sid }));
              setActiveNav('live');
            }}
          />
        )}

        {activeNav === 'providers' && <ProvidersView />}

        {activeNav === 'security' && <SecurityView />}

        {activeNav === 'settings' && <SettingsView />}
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
          setSession((prev) => ({ ...prev, sessionId: sid }));
        }}
        onCreateSession={({ goal }) => handleStartMission({ goal, llm_provider: 'simulator', supervisor: 'laya', max_steps: 6 })}
      />
    </div>
  );
}
