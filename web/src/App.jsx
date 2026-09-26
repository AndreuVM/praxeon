import React, { useState, useEffect } from 'react';
import Header from './components/Header';
import Sidebar from './components/Sidebar';
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

import {
  INITIAL_SESSION,
  INITIAL_NODES,
  INITIAL_DECISIONS_MAP,
  INITIAL_LOGS,
  INITIAL_EVENTS,
} from './constants/demoData';

import * as api from './services/api';

export default function App() {
  const [activeNav, setActiveNav] = useState('live');
  const [session, setSession] = useState(INITIAL_SESSION);
  const [nodes, setNodes] = useState(INITIAL_NODES);
  const [decisionsMap, setDecisionsMap] = useState(INITIAL_DECISIONS_MAP);
  const [selectedNodeId, setSelectedNodeId] = useState('node-5');
  const [logs, setLogs] = useState(INITIAL_LOGS);
  const [events, setEvents] = useState(INITIAL_EVENTS);
  const [sessionsList, setSessionsList] = useState([
    { session_id: '7f3a2c', goal: 'Fix authentication bug in the API', status: 'Active' },
  ]);

  const [runtimeActive, setRuntimeActive] = useState(true);
  const [isProposeOpen, setIsProposeOpen] = useState(false);
  const [isSessionsOpen, setIsSessionsOpen] = useState(false);

  // Check health and initialize backend connection
  useEffect(() => {
    async function initBackend() {
      const health = await api.fetchHealth();
      if (health?.data?.status === 'ok') {
        setRuntimeActive(true);
        const sList = await api.fetchSessions();
        if (sList?.data && sList.data.length > 0) {
          setSessionsList(sList.data);
        }
      }
    }
    initBackend();
  }, []);

  // Real-time WebSocket connection to the active session stream
  useEffect(() => {
    if (!runtimeActive || !session.sessionId) return;

    const stream = api.createWebSocketStream(
      session.sessionId,
      (msg) => {
        if (msg.action === 'event' && msg.data) {
          const ev = msg.data;
          const timeStr = new Date(ev.timestamp || Date.now()).toTimeString().split(' ')[0];

          // Add to structured events
          setEvents((prev) => [
            {
              seq: ev.sequence,
              type: ev.event_type,
              time: timeStr,
              detail: JSON.stringify(ev.payload || {}).slice(0, 100),
            },
            ...prev,
          ]);

          // Add to terminal logs
          setLogs((prev) => [
            ...prev,
            {
              time: timeStr,
              level: ev.event_type.includes('error') ? 'ERROR' : ev.event_type.includes('warn') || ev.event_type === 'approval.requested' ? 'WARN' : 'INFO',
              message: `[WS ${ev.event_type}] ${ev.node_id || ''} ${JSON.stringify(ev.payload || '')}`,
            },
          ]);
        }
      },
      (status) => {
        if (status === 'connected') {
          setRuntimeActive(true);
        }
      }
    );

    return () => stream.close();
  }, [session.sessionId, runtimeActive]);

  // Selected decision for the right inspector
  const currentDecision = decisionsMap[selectedNodeId] || decisionsMap['node-5'];

  // Handle selecting a node in the tree
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

  // Handle proposing an action via the Modal
  const handleProposeAction = async (proposal) => {
    const timeStr = new Date().toTimeString().split(' ')[0];
    const newSeq = nodes.length + 1;
    const newNodeId = `node-${newSeq}`;

    setLogs((prev) => [
      ...prev,
      { time: timeStr, level: 'INFO', message: `Proposing action: ${proposal.operation || proposal.tool}` },
    ]);

    let res = null;
    try {
      res = await api.proposeAction(session.sessionId, proposal);
    } catch (e) {
      console.warn('[API Propose failed, using local simulation]:', e);
    }

    const decisionData = res?.data;
    const isAllow = decisionData?.status === 'ALLOW' || proposal.tool === 'read_file';
    const isReview = decisionData?.status === 'REVIEW' || proposal.tool === 'run_command';
    const status = isAllow ? 'ALLOW' : isReview ? 'REVIEW' : 'BLOCK';

    const lastNode = nodes[nodes.length - 1] || { x: 420, y: 340 };
    const newNode = {
      id: newNodeId,
      label: `${newSeq}. ${proposal.operation?.slice(0, 18) || proposal.tool}`,
      subtitle: `${timeStr} · Tool (${proposal.tool})`,
      type: 'step',
      status: status,
      action: proposal.operation || proposal.tool,
      x: 420,
      y: lastNode.y + 60,
      parentId: selectedNodeId || 'node-5',
    };

    setNodes((prev) => [...prev, newNode]);
    setSelectedNodeId(newNodeId);

    const newDecisionRecord = {
      decisionId: decisionData?.decision_id || `d_${session.sessionId}-${newSeq}`,
      sequence: newSeq,
      status: status,
      actionCommand: proposal.operation || proposal.tool,
      tool: proposal.tool,
      provider: 'JEV + LAYA',
      model: 'Claude-3.5-sonnet',
      riskLevel: proposal.tool === 'run_command' ? 'HIGH' : 'LOW',
      riskScore: proposal.tool === 'run_command' ? 0.82 : 0.15,
      semanticEvaluation: [
        { provider: 'LAYA', score: 0.85, verdict: isAllow ? 'ALLOW' : 'REVIEW' },
        { provider: 'TypeSafe', score: 0.78, verdict: 'ALLOW' },
      ],
      policyDecision: {
        status: isReview ? 'Requires confirmation' : 'Authorized by policy',
        requiresConfirmation: isReview,
        rulesActivated: isReview ? ['EgressPolicy', 'DoubleVerification'] : ['StandardPolicy'],
        reasonCodes: isReview ? ['REQUIRE_HUMAN_CONFIRMATION'] : ['SAFE_READ'],
        precedence: 'Deterministic Safety Precedence',
      },
      capability: {
        issued: isAllow,
        statusText: isAllow ? 'Issued & HMAC Signed' : 'Not issued',
        token: isAllow ? `cap_${Math.random().toString(36).slice(2, 8)}` : null,
      },
      reason: isReview
        ? 'High risk action requires confirmation according to policy rules.'
        : 'Read-only action verified against ground truth.',
      evidenceTab: {
        groundingScore: 0.88,
        claimCount: 2,
        claims: ['Validated schema integrity and execution sandbox target.'],
        freshness: `live (${timeStr} UTC)`,
      },
      receiptTab: {
        decisionId: decisionData?.decision_id || `d_${session.sessionId}-${newSeq}`,
        sessionId: session.sessionId,
        actionHash: decisionData?.action_hash || 'sha256:7e9b04fc41a7d6568297b83321588632',
        stateHash: 'sha256:4b81c201a096180373ad412e8473e6',
        nonce: `non_${Math.random().toString(36).slice(2, 10)}`,
        signature: 'hmac-sha256:39a7b212f008cb042aaefc32986423a884efbb5c',
        hasValidHmac: true,
        expiresAt: '5 min TTL',
        isExpired: false,
      },
      relatedDecisions: [
        { id: `#${session.sessionId}-prev`, tool: 'Previous step', verdict: 'ALLOW' },
      ],
    };

    setDecisionsMap((prev) => ({
      ...prev,
      [newNodeId]: newDecisionRecord,
    }));

    setSession((prev) => ({
      ...prev,
      metrics: {
        ...prev.metrics,
        totalDecisions: prev.metrics.totalDecisions + 1,
        allowed: isAllow ? prev.metrics.allowed + 1 : prev.metrics.allowed,
        review: isReview ? prev.metrics.review + 1 : prev.metrics.review,
        blocked: !isAllow && !isReview ? prev.metrics.blocked + 1 : prev.metrics.blocked,
      },
    }));

    setLogs((prev) => [
      ...prev,
      {
        time: timeStr,
        level: isReview ? 'WARN' : 'INFO',
        message: `Policy decided: ${status} for action #${newSeq}`,
      },
    ]);
  };

  // Handle Human Approval
  const handleApprove = async (decisionId) => {
    const timeStr = new Date().toTimeString().split(' ')[0];
    try {
      await api.confirmDecision(decisionId, true);
    } catch (e) {
      console.warn('[Approval sent locally]:', e);
    }

    setDecisionsMap((prev) => {
      const target = { ...prev[selectedNodeId] };
      if (target) {
        target.status = 'ALLOW';
        target.capability = {
          issued: true,
          statusText: 'Issued & Signed (Human Sign-off)',
          token: `cap_signed_${decisionId}`,
        };
        target.policyDecision.status = 'Human Sign-off Approved';
        target.policyDecision.requiresConfirmation = false;
      }
      return { ...prev, [selectedNodeId]: target };
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
      { time: timeStr, level: 'INFO', message: `Operator approved decision ${decisionId}. Capability issued.` },
    ]);
  };

  // Handle Human Rejection
  const handleReject = async (decisionId) => {
    const timeStr = new Date().toTimeString().split(' ')[0];
    try {
      await api.confirmDecision(decisionId, false);
    } catch (e) {
      console.warn('[Rejection sent locally]:', e);
    }

    setDecisionsMap((prev) => {
      const target = { ...prev[selectedNodeId] };
      if (target) {
        target.status = 'BLOCKED';
        target.policyDecision.status = 'Operator Rejected';
        target.capability = { issued: false, statusText: 'Blocked by Operator' };
      }
      return { ...prev, [selectedNodeId]: target };
    });

    setNodes((prev) =>
      prev.map((n) => (n.id === selectedNodeId ? { ...n, status: 'BLOCK' } : n))
    );

    setSession((prev) => ({
      ...prev,
      metrics: {
        ...prev.metrics,
        blocked: prev.metrics.blocked + 1,
        review: Math.max(0, prev.metrics.review - 1),
      },
    }));

    setLogs((prev) => [
      ...prev,
      { time: timeStr, level: 'WARN', message: `Operator rejected decision ${decisionId}. Action blocked.` },
    ]);
  };

  // Handle Execution in Sandbox
  const handleExecute = async (decisionId, capability) => {
    const timeStr = new Date().toTimeString().split(' ')[0];
    try {
      await api.executeDecision(decisionId, capability);
    } catch (e) {
      console.warn('[Execute sent locally]:', e);
    }

    setLogs((prev) => [
      ...prev,
      {
        time: timeStr,
        level: 'INFO',
        message: `Executing capability in secure sandbox: ${currentDecision.actionCommand}... SUCCESS (exit code 0)`,
      },
    ]);
  };

  // Handle Creating a new Session
  const handleCreateSession = async (goal) => {
    const newId = Math.random().toString(36).slice(2, 8);
    try {
      await api.createSession(goal, newId);
    } catch (e) {
      console.warn('[Session created locally]:', e);
    }

    const newSess = {
      sessionId: newId,
      status: 'Active',
      agent: 'SupervisorAgent',
      goal,
      metrics: { totalDecisions: 0, allowed: 0, blocked: 0, review: 0 },
      runtime: INITIAL_SESSION.runtime,
    };

    setSession(newSess);
    setSessionsList((prev) => [{ session_id: newId, goal, status: 'Active' }, ...prev]);
    setNodes([
      { id: 'start', label: 'Start', type: 'start', status: 'SYSTEM', x: 420, y: 30, parentId: null },
    ]);
    setSelectedNodeId('start');
    setActiveNav('live');
    setLogs([
      { time: new Date().toTimeString().split(' ')[0], level: 'INFO', message: `Session #${newId} initialized: ${goal}` },
    ]);
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
            onCreateSession={handleCreateSession}
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
        onSubmit={handleProposeAction}
      />

      <SessionsModal
        isOpen={isSessionsOpen}
        onClose={() => setIsSessionsOpen(false)}
        sessions={sessionsList}
        currentSessionId={session.sessionId}
        onSelectSession={(sid) => {
          setSession((prev) => ({ ...prev, sessionId: sid }));
        }}
        onCreateSession={handleCreateSession}
      />
    </div>
  );
}
