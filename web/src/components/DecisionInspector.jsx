import React, { useState } from 'react';
import {
  Copy,
  Check,
  X,
  AlertTriangle,
  Shield,
  ShieldCheck,
  FileCheck,
  UserCheck,
  Terminal,
  ExternalLink,
  ChevronRight,
  Play,
  RotateCcw,
  MessageSquare,
  Key,
  Maximize2,
  Minimize2,
} from 'lucide-react';
import MissionChat from './MissionChat';

export default function DecisionInspector({
  decision = null,
  activeTab: activeTabProp,
  onTabChange,
  session = {},
  isRunning = false,
  isPaused = false,
  events = [],
  chatMessages,
  onMessagesChange,
  missionConfig,
  onConfigChange,
  onStartMission,
  onPauseMission,
  onResumeMission,
  onStopMission,
  onLoadDemo,
  onApprove,
  onReject,
  onExecute,
}) {
  const [internalTab, setInternalTab] = useState('chat');
  const activeTab = activeTabProp !== undefined ? activeTabProp : internalTab;
  const setActiveTab = onTabChange || setInternalTab;

  const [copied, setCopied] = useState(false);
  const [actionWrapped, setActionWrapped] = useState(true);
  const [actionExpanded, setActionExpanded] = useState(false);
  const [obsExpanded, setObsExpanded] = useState(false);
  const [copiedObs, setCopiedObs] = useState(false);
  const [isConfirming, setIsConfirming] = useState(false);
  const [isRejecting, setIsRejecting] = useState(false);
  const [rejectionReason, setRejectionReason] = useState('');

  const handleCopyObs = () => {
    if (!observationOutput) return;
    navigator.clipboard?.writeText(observationOutput);
    setCopiedObs(true);
    setTimeout(() => setCopiedObs(false), 2000);
  };

  const {
    decisionId = 'd_unknown',
    sequence = 5,
    status = 'BLOCKED',
    actionCommand = 'git push origin main',
    tool = 'git',
    provider = 'JEV',
    model = 'Claude-3.5-sonnet',
    riskLevel = 'HIGH',
    riskScore = 0.82,
    semanticEvaluation = [
      { provider: 'LAYA', score: 0.81, verdict: 'ALLOW' },
      { provider: 'TypeSafe', score: 0.64, verdict: 'REVIEW' },
    ],
    policyDecision = {
      status: 'Requires confirmation',
      requiresConfirmation: true,
      rulesActivated: ['EgressPolicy', 'PathContainment', 'DoubleVerification'],
      reasonCodes: ['REQUIRE_HUMAN_CONFIRMATION'],
      precedence: 'Deterministic Safety Precedence',
    },
    capability = {
      issued: false,
      statusText: 'Not issued',
      token: null,
    },
    reason = 'High risk action requires confirmation according to policy rules.',
    evidenceTab = {},
    receiptTab = {},
    decisionTab = {},
    relatedDecisions = [],
    observationOutput = null,
  } = decision || {};

  const rawDecisionTab = decision?.decisionTab || decision?.decision_tab || {};
  const opAssessment = rawDecisionTab.operation_assessment || decision?.operation_assessment || decision?.operationAssessment || null;
  const opCategory = (rawDecisionTab.operation_category || decision?.operation_category || opAssessment?.category || 'inspection').toLowerCase();
  const executionMode = rawDecisionTab.execution_mode || decision?.execution_mode || decision?.executionMode || 'local_restricted';
  const isolation = rawDecisionTab.isolation || (executionMode === 'full_access' ? 'None (Host OS)' : 'Active');
  const workingDirectory = rawDecisionTab.working_directory || '/workspace';
  const networkMode = rawDecisionTab.network_mode || (executionMode === 'full_access' ? 'Host Direct' : 'Isolated (Restricted)');
  const executionBackend = rawDecisionTab.execution_backend || (
    executionMode === 'full_access' ? 'FullAccessExecutor' : (executionMode === 'container' ? 'DockerContainer' : 'LocalProcessSandbox')
  );

  const handleCopyCommand = () => {
    navigator.clipboard?.writeText(actionCommand);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const getStatusBadge = () => {
    if (status === 'ALLOW') {
      return (
        <span className="badge badge-success">
          <Check size={11} strokeWidth={2.5} />
          ALLOW
        </span>
      );
    }
    if (status === 'REVIEW') {
      return (
        <span className="badge badge-warning">
          <AlertTriangle size={11} strokeWidth={2.3} />
          REVIEW
        </span>
      );
    }
    if (status === 'REPLAN') {
      return (
        <span
          className="badge"
          style={{
            backgroundColor: 'rgba(168, 85, 247, 0.15)',
            color: '#c084fc',
            border: '1px solid rgba(168, 85, 247, 0.35)',
            display: 'inline-flex',
            alignItems: 'center',
            gap: '4px',
            padding: '2px 8px',
            borderRadius: '4px',
            fontSize: '11px',
            fontWeight: 600,
          }}
        >
          <RotateCcw size={11} strokeWidth={2.5} />
          REPLAN (PODA)
        </span>
      );
    }
    return (
      <span className="badge badge-danger">
        <X size={11} strokeWidth={2.5} />
        BLOCKED
      </span>
    );
  };

  const tabsConfig = [
    { id: 'chat', label: 'Chat', icon: MessageSquare },
    { id: 'decision', label: 'Decisión', icon: Shield },
    { id: 'evidence', label: 'Evidencia', icon: FileCheck },
    { id: 'policy', label: 'Política', icon: ShieldCheck },
    { id: 'receipt', label: 'Recibo', icon: Key },
  ];

  return (
    <aside style={{
      width: '400px',
      minWidth: '380px',
      maxWidth: '430px',
      backgroundColor: '#0c0f14',
      borderLeft: '1px solid #1a202c',
      display: 'flex',
      flexDirection: 'column',
      flexShrink: 0,
      userSelect: 'none',
      height: '100%',
    }}>
      {/* 5 Tabs Header */}
      <div style={{
        height: '42px',
        backgroundColor: '#0c0f14',
        borderBottom: '1px solid #1a202c',
        display: 'flex',
        alignItems: 'center',
        padding: '0 8px',
        justifyContent: 'space-between',
        flexShrink: 0,
      }}>
        {tabsConfig.map((t) => {
          const isActive = activeTab === t.id;
          const IconComponent = t.icon;
          return (
            <button
              key={t.id}
              onClick={() => setActiveTab(t.id)}
              style={{
                background: 'none',
                border: 'none',
                padding: '10px 6px',
                fontSize: '11.5px',
                fontWeight: isActive ? '600' : '400',
                color: isActive ? '#f0f6fc' : '#8b949e',
                cursor: 'pointer',
                borderBottom: isActive ? '2px solid #58a6ff' : '2px solid transparent',
                transition: 'all 0.15s ease',
                display: 'flex',
                alignItems: 'center',
                gap: '4px',
              }}
            >
              <IconComponent size={12} style={{ color: isActive ? '#58a6ff' : '#8b949e' }} />
              <span>{t.label}</span>
            </button>
          );
        })}
      </div>

      {/* Tab 1: Chat Interactivo de Misión (Permanentemente montado con CSS toggle para retener historial y modelos) */}
      <div style={{ flex: 1, overflow: 'hidden', display: activeTab === 'chat' ? 'flex' : 'none', flexDirection: 'column' }}>
        <MissionChat
          session={session}
          isRunning={isRunning}
          isPaused={isPaused}
          events={events}
          chatMessages={chatMessages}
          onMessagesChange={onMessagesChange}
          missionConfig={missionConfig}
          onConfigChange={onConfigChange}
          onStartMission={onStartMission}
          onPauseMission={onPauseMission}
          onResumeMission={onResumeMission}
          onStopMission={onStopMission}
          onLoadDemo={onLoadDemo}
        />
      </div>

      {/* Tabs 2, 3, 4, 5: Inspector de Nodos */}
      <div style={{ flex: 1, overflow: 'hidden', display: activeTab !== 'chat' ? 'flex' : 'none', flexDirection: 'column' }}>
        {!decision ? (
          <div style={{
            flex: 1,
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            textAlign: 'center',
            padding: '30px',
            color: '#64748b',
            gap: '12px',
          }}>
            <Shield size={32} style={{ opacity: 0.35, color: '#58a6ff' }} />
            <p style={{ margin: 0, fontSize: '12px', color: '#8b949e', lineHeight: '1.5' }}>
              Selecciona un nodo del grafo en vivo para inspeccionar su trazabilidad formal, evaluación semántica y capability HMAC.
            </p>
            <button
              type="button"
              onClick={() => setActiveTab('chat')}
              className="btn btn-secondary"
              style={{ fontSize: '11.5px', marginTop: '6px', display: 'flex', alignItems: 'center', gap: '5px' }}
            >
              <MessageSquare size={12} />
              <span>Ir al Chat de Misión</span>
            </button>
          </div>
        ) : (
          <div style={{
            flex: 1,
            overflowY: 'auto',
            padding: '18px',
            display: 'flex',
            flexDirection: 'column',
            gap: '18px',
            backgroundColor: '#0c0f14',
          }}>
        {activeTab === 'decision' && (
          <>
            {/* Status & Sequence */}
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              {getStatusBadge()}
              <span style={{ fontSize: '12px', color: '#64748b', fontFamily: 'var(--font-mono)' }}>
                # {sequence}
              </span>
            </div>

            {/* Action Box (Scrollable & Traversable) */}
            <div>
              <div style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                marginBottom: '6px',
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <span style={{ fontSize: '11px', color: '#8b949e', fontWeight: '600' }}>
                    Action
                  </span>
                  {actionCommand && (
                    <span style={{
                      fontSize: '9.5px',
                      color: '#64748b',
                      backgroundColor: '#121620',
                      border: '1px solid #1e2636',
                      padding: '1px 5px',
                      borderRadius: '3px',
                      fontFamily: 'var(--font-mono)',
                    }}>
                      {actionCommand.length} carácteres
                    </span>
                  )}
                </div>

                <div style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                  {/* Alternar ajuste de línea / scroll horizontal continuo */}
                  <button
                    type="button"
                    onClick={() => setActionWrapped(!actionWrapped)}
                    style={{
                      background: actionWrapped ? 'rgba(88, 166, 255, 0.12)' : '#141a24',
                      border: `1px solid ${actionWrapped ? 'rgba(88, 166, 255, 0.35)' : '#212a3a'}`,
                      borderRadius: '4px',
                      color: actionWrapped ? '#58a6ff' : '#8b949e',
                      cursor: 'pointer',
                      padding: '2px 7px',
                      fontSize: '10px',
                      fontWeight: '500',
                      transition: 'all 0.15s ease',
                    }}
                    title={actionWrapped ? 'Modo actual: Ajustado. Clic para activar línea continua con scroll horizontal' : 'Modo actual: Continuo. Clic para activar ajuste automático de línea'}
                  >
                    {actionWrapped ? 'Ajustado' : 'Continuo'}
                  </button>

                  {/* Expandir / Compactar altura */}
                  <button
                    type="button"
                    onClick={() => setActionExpanded(!actionExpanded)}
                    style={{
                      background: actionExpanded ? 'rgba(167, 139, 250, 0.12)' : '#141a24',
                      border: `1px solid ${actionExpanded ? 'rgba(167, 139, 250, 0.35)' : '#212a3a'}`,
                      borderRadius: '4px',
                      color: actionExpanded ? '#c084fc' : '#8b949e',
                      cursor: 'pointer',
                      padding: '2px 6px',
                      fontSize: '10px',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '3px',
                      transition: 'all 0.15s ease',
                    }}
                    title={actionExpanded ? 'Compactar caja de acción' : 'Expandir caja de acción'}
                  >
                    {actionExpanded ? <Minimize2 size={11} /> : <Maximize2 size={11} />}
                    <span>{actionExpanded ? 'Reducir' : 'Expandir'}</span>
                  </button>

                  {/* Copiar */}
                  <button
                    type="button"
                    onClick={handleCopyCommand}
                    style={{
                      background: copied ? 'rgba(63, 185, 80, 0.15)' : '#141a24',
                      border: `1px solid ${copied ? 'rgba(63, 185, 80, 0.4)' : '#212a3a'}`,
                      borderRadius: '4px',
                      color: copied ? '#3fb950' : '#8b949e',
                      cursor: 'pointer',
                      padding: '2px 7px',
                      fontSize: '10px',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '3px',
                      transition: 'all 0.15s ease',
                    }}
                    title="Copiar comando completo al portapapeles"
                  >
                    {copied ? <Check size={11} /> : <Copy size={11} />}
                    <span>{copied ? 'Copiado' : 'Copiar'}</span>
                  </button>
                </div>
              </div>

              {/* Contenedor desplazable de acción (permite recorrer todo el texto horizontal y verticalmente) */}
              <div
                style={{
                  backgroundColor: '#0c1017',
                  borderRadius: '6px',
                  border: '1px solid #1e2636',
                  boxShadow: 'var(--shadow-clay-sm)',
                  padding: '10px 12px',
                  maxHeight: actionExpanded ? '380px' : '150px',
                  overflowX: 'auto',
                  overflowY: 'auto',
                  transition: 'max-height 0.2s ease',
                }}
              >
                <div style={{
                  fontFamily: 'var(--font-mono)',
                  fontSize: '11.5px',
                  color: '#f0f6fc',
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: '8px',
                  lineHeight: '1.5',
                  whiteSpace: actionWrapped ? 'pre-wrap' : 'pre',
                  wordBreak: actionWrapped ? 'break-word' : 'normal',
                  overflowWrap: actionWrapped ? 'anywhere' : 'normal',
                  userSelect: 'text',
                }}>
                  <span style={{ color: '#58a6ff', userSelect: 'none', flexShrink: 0, fontWeight: '700' }}>
                    &gt;_
                  </span>
                  <span style={{ flex: 1, minWidth: 0, userSelect: 'text' }}>
                    {actionCommand || '(Comando vacío)'}
                  </span>
                </div>
              </div>
            </div>

            {/* Attributes Grid */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', fontSize: '11.5px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: '#8b949e' }}>Tool</span>
                <span style={{ color: '#f0f6fc', fontFamily: 'var(--font-mono)' }}>{tool}</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: '#8b949e' }}>Provider</span>
                <span style={{ color: '#f0f6fc' }}>{provider}</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: '#8b949e' }}>Model</span>
                <span style={{ color: '#f0f6fc' }}>{model}</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ color: '#8b949e' }}>Risk Level</span>
                <div style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                  <span style={{
                    width: '7px',
                    height: '7px',
                    borderRadius: '50%',
                    backgroundColor: riskLevel === 'HIGH' ? '#ef4444' : riskLevel === 'MEDIUM' ? '#f59e0b' : '#10b981',
                  }} />
                  <span style={{
                    fontSize: '11px',
                    fontWeight: '700',
                    color: riskLevel === 'HIGH' ? '#f87171' : riskLevel === 'MEDIUM' ? '#fbbf24' : '#34d399',
                  }}>
                    {riskLevel}
                  </span>
                </div>
              </div>
            </div>

            {/* Semantic Evaluation */}
            <div style={{
              backgroundColor: '#0f1520',
              borderRadius: '6px',
              padding: '12px',
              border: '1px solid #1a2434',
            }}>
              <span style={{ fontSize: '11px', color: '#8595a8', fontWeight: '600' }}>
                Semantic Evaluation
              </span>
              <div style={{ marginTop: '8px', display: 'flex', flexDirection: 'column', gap: '6px' }}>
                {semanticEvaluation.map((ev, idx) => (
                  <div key={idx} style={{ display: 'flex', justifyContent: 'space-between', fontSize: '11.5px' }}>
                    <span style={{ color: '#94a3b8' }}>{ev.provider}</span>
                    <div style={{ display: 'flex', gap: '8px' }}>
                      <span style={{ color: ev.verdict === 'ALLOW' ? '#34d399' : '#fbbf24', fontWeight: '600' }}>
                        {ev.score}
                      </span>
                      <span style={{
                        color: ev.verdict === 'ALLOW' ? '#34d399' : '#fbbf24',
                        fontWeight: '700',
                        fontSize: '10.5px',
                      }}>
                        {ev.verdict}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* Policy Decision */}
            <div>
              <span style={{ fontSize: '11px', color: '#73849c', fontWeight: '500' }}>
                Policy Decision
              </span>
              <div style={{
                marginTop: '6px',
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                color: policyDecision.requiresConfirmation ? '#fbbf24' : '#34d399',
                fontSize: '11.5px',
                fontWeight: '600',
              }}>
                <AlertTriangle size={14} />
                <span>{policyDecision.status}</span>
              </div>
            </div>

            {/* Capability */}
            <div>
              <span style={{ fontSize: '11px', color: '#73849c', fontWeight: '500' }}>
                Capability
              </span>
              <div style={{
                marginTop: '6px',
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                color: capability.issued ? '#34d399' : '#64748b',
                fontSize: '11.5px',
              }}>
                <Shield size={14} />
                <span>{capability.statusText || (capability.issued ? 'Issued & Signed' : 'Not issued')}</span>
              </div>
            </div>

            {/* Command Classification Card (Sección 4 y 5) */}
            <div style={{
              backgroundColor: opCategory === 'destructive' || opCategory === 'privilege'
                ? 'rgba(239, 68, 68, 0.08)'
                : (opCategory === 'unknown' ? 'rgba(245, 158, 11, 0.08)' : '#0f1520'),
              borderRadius: '6px',
              padding: '10px 12px',
              border: `1px solid ${
                opCategory === 'destructive' || opCategory === 'privilege'
                  ? 'rgba(239, 68, 68, 0.35)'
                  : (opCategory === 'unknown' ? 'rgba(245, 158, 11, 0.35)' : '#1a2434')
              }`,
            }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
                <span style={{ fontSize: '11px', color: '#8595a8', fontWeight: '700' }}>
                  Command Classification
                </span>
                <span style={{
                  fontSize: '9.5px',
                  fontWeight: '700',
                  padding: '2px 6px',
                  borderRadius: '4px',
                  backgroundColor: opCategory === 'destructive' || opCategory === 'privilege'
                    ? '#ef4444'
                    : (opCategory === 'unknown' ? '#f59e0b' : (opCategory === 'inspection' || opCategory === 'build_test' ? '#10b981' : '#38bdf8')),
                  color: '#ffffff',
                  textTransform: 'uppercase',
                }}>
                  {opCategory}
                </span>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '5px', fontSize: '11px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ color: '#64748b' }}>Read-only</span>
                  <span style={{ color: opAssessment?.read_only ? '#34d399' : '#94a3b8', fontWeight: '600' }}>
                    {opAssessment?.read_only ? 'Yes' : 'No'}
                  </span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ color: '#64748b' }}>Reversible</span>
                  <span style={{ color: opAssessment?.reversible ? '#34d399' : '#f87171', fontWeight: '600' }}>
                    {opAssessment?.reversible ? 'Yes' : 'No'}
                  </span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ color: '#64748b' }}>Network Access</span>
                  <span style={{ color: opAssessment?.network_access ? '#fbbf24' : '#64748b' }}>
                    {opAssessment?.network_access ? 'Required' : 'None'}
                  </span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ color: '#64748b' }}>Confidence / Classifier</span>
                  <span style={{ color: '#f0f6fc' }}>
                    {opAssessment ? `${Math.round((opAssessment.confidence || 0) * 100)}% (${opAssessment.classifier || 'rule'})` : '100% (deterministic)'}
                  </span>
                </div>
              </div>
            </div>

            {/* Execution Environment Subsection */}
            <div style={{
              backgroundColor: executionMode === 'full_access' ? 'rgba(239, 68, 68, 0.08)' : '#0f1520',
              borderRadius: '6px',
              padding: '10px 12px',
              border: `1px solid ${executionMode === 'full_access' ? 'rgba(239, 68, 68, 0.35)' : '#1a2434'}`,
            }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
                <span style={{ fontSize: '11px', color: executionMode === 'full_access' ? '#f87171' : '#8595a8', fontWeight: '700' }}>
                  Execution Environment
                </span>
                <span style={{
                  fontSize: '9.5px',
                  fontWeight: '700',
                  padding: '1px 6px',
                  borderRadius: '4px',
                  backgroundColor: executionMode === 'full_access' ? '#ef4444' : (executionMode === 'container' ? '#38bdf8' : '#8b5cf6'),
                  color: '#ffffff',
                  textTransform: 'uppercase',
                }}>
                  {executionMode}
                </span>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '5px', fontSize: '11px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ color: '#64748b' }}>Isolation</span>
                  <span style={{ color: executionMode === 'full_access' ? '#f87171' : '#34d399', fontWeight: '600' }}>{isolation}</span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ color: '#64748b' }}>Working Dir</span>
                  <span style={{ color: '#c9d1d9', fontFamily: 'var(--font-mono)', fontSize: '10px' }} title={workingDirectory}>
                    {workingDirectory.length > 25 ? '...' + workingDirectory.slice(-22) : workingDirectory}
                  </span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ color: '#64748b' }}>Network Posture</span>
                  <span style={{ color: executionMode === 'full_access' ? '#fbbf24' : '#34d399' }}>{networkMode}</span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ color: '#64748b' }}>Execution Backend</span>
                  <span style={{ color: '#f0f6fc', fontWeight: '500' }}>{executionBackend}</span>
                </div>
              </div>
            </div>

            {/* Reason */}
            <div>
              <span style={{ fontSize: '11px', color: '#73849c', fontWeight: '500' }}>
                Reason
              </span>
              <p style={{
                marginTop: '6px',
                fontSize: '11.5px',
                color: '#94a3b8',
                lineHeight: '1.5',
              }}>
                {reason}
              </p>
            </div>

            {/* Execution Observation Output / Final Answer */}
            {observationOutput && (
              <div>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '6px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                    <span style={{ fontSize: '11px', color: tool === 'finish' ? '#34d399' : '#38bdf8', fontWeight: '600' }}>
                      {tool === 'finish' ? 'Respuesta / Conclusión Final' : 'Execution Observation'}
                    </span>
                    <span style={{
                      fontSize: '9.5px',
                      color: '#64748b',
                      backgroundColor: '#121722',
                      border: '1px solid #1e2636',
                      padding: '1px 5px',
                      borderRadius: '3px',
                      fontFamily: 'var(--font-mono)',
                    }}>
                      {(observationOutput.length >= 1024 ? `${(observationOutput.length / 1024).toFixed(1)} KB` : `${observationOutput.length} B`)} · {observationOutput.split('\n').length} lns
                    </span>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                    <span style={{ fontSize: '10px', color: '#64748b' }}>
                      {tool === 'finish' ? 'Mission Completed' : (executionMode === 'full_access' ? 'Host OS' : 'Live sandbox')}
                    </span>
                    <button
                      type="button"
                      onClick={handleCopyObs}
                      style={{
                        background: 'none',
                        border: 'none',
                        color: copiedObs ? '#3fb950' : '#8b949e',
                        cursor: 'pointer',
                        padding: '2px 4px',
                        display: 'flex',
                        alignItems: 'center',
                        gap: '3px',
                        fontSize: '10px',
                      }}
                      title="Copiar observación completa"
                    >
                      {copiedObs ? <Check size={11} /> : <Copy size={11} />}
                      <span>{copiedObs ? 'Copiado' : 'Copiar'}</span>
                    </button>
                    <button
                      type="button"
                      onClick={() => setObsExpanded(!obsExpanded)}
                      style={{
                        background: 'none',
                        border: 'none',
                        color: '#8b949e',
                        cursor: 'pointer',
                        padding: '2px 4px',
                        display: 'flex',
                        alignItems: 'center',
                        gap: '3px',
                        fontSize: '10px',
                      }}
                      title={obsExpanded ? 'Compactar vista' : 'Ampliar vista completa'}
                    >
                      {obsExpanded ? <Minimize2 size={11} /> : <Maximize2 size={11} />}
                      <span>{obsExpanded ? 'Compactar' : 'Ampliar'}</span>
                    </button>
                  </div>
                </div>
                <div style={{
                  padding: '10px 12px',
                  backgroundColor: tool === 'finish' ? 'rgba(16, 185, 129, 0.08)' : '#070a10',
                  border: tool === 'finish' ? '1px solid rgba(16, 185, 129, 0.35)' : '1px solid #1e293b',
                  borderRadius: '6px',
                  fontFamily: tool === 'finish' ? 'inherit' : 'var(--font-mono)',
                  fontSize: '11.5px',
                  color: '#34d399',
                  maxHeight: obsExpanded ? '580px' : '260px',
                  overflowY: 'auto',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                  lineHeight: '1.45',
                  transition: 'max-height 0.2s ease',
                }}>
                  {observationOutput}
                </div>
              </div>
            )}

            {/* Action Buttons: Request Human Approval or Approve/Reject */}
            <div>
              {capability.issued ? (
                <button
                  onClick={() => onExecute?.(decisionId, capability)}
                  className="btn btn-primary"
                  style={{ width: '100%', padding: '9px 12px' }}
                >
                  <Play size={13} />
                  Execute in Sandbox
                </button>
              ) : isRejecting ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                  <label style={{ fontSize: '11px', color: '#f87171', fontWeight: '600' }}>
                    Motivo justificado de rechazo (obligatorio):
                  </label>
                  <input
                    type="text"
                    value={rejectionReason}
                    onChange={(e) => setRejectionReason(e.target.value)}
                    placeholder="Motivo de denegación..."
                    style={{
                      backgroundColor: '#0c0f14',
                      border: '1px solid #ef4444',
                      borderRadius: '4px',
                      padding: '6px 8px',
                      color: '#f0f6fc',
                      fontSize: '11.5px',
                      outline: 'none',
                    }}
                  />
                  <div style={{ display: 'flex', gap: '6px' }}>
                    <button
                      onClick={() => {
                        if (!rejectionReason.trim()) {
                          alert('Es obligatorio ingresar un motivo de rechazo.');
                          return;
                        }
                        onReject?.(decisionId, rejectionReason.trim());
                        setIsRejecting(false);
                        setIsConfirming(false);
                        setRejectionReason('');
                      }}
                      className="btn btn-danger"
                      style={{ flex: 1, padding: '7px' }}
                    >
                      Confirmar Rechazo
                    </button>
                    <button
                      onClick={() => setIsRejecting(false)}
                      className="btn btn-secondary"
                      style={{ padding: '7px 10px' }}
                    >
                      Cancelar
                    </button>
                  </div>
                </div>
              ) : isConfirming ? (
                <div style={{ display: 'flex', gap: '8px' }}>
                  <button
                    onClick={() => {
                      onApprove?.(decisionId);
                      setIsConfirming(false);
                    }}
                    className="btn btn-success"
                    style={{ flex: 1, padding: '8px' }}
                  >
                    <Check size={13} />
                    Approve
                  </button>
                  <button
                    onClick={() => setIsRejecting(true)}
                    className="btn btn-danger"
                    style={{ flex: 1, padding: '8px' }}
                  >
                    <X size={13} />
                    Reject...
                  </button>
                </div>
              ) : (
                <button
                  onClick={() => setIsConfirming(true)}
                  className="btn btn-approval"
                >
                  <UserCheck size={14} />
                  Request Human Approval
                </button>
              )}
            </div>

            {/* Related Decisions */}
            {relatedDecisions.length > 0 && (
              <div style={{ borderTop: '1px solid #1a202c', paddingTop: '14px' }}>
                <span style={{ fontSize: '11px', color: '#f0f6fc', fontWeight: '600' }}>
                  Related Decisions
                </span>
                <div style={{ marginTop: '8px', display: 'flex', flexDirection: 'column', gap: '6px' }}>
                  {relatedDecisions.map((rd, idx) => (
                    <div
                      key={idx}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        padding: '6px 10px',
                        borderRadius: '6px',
                        backgroundColor: '#131822',
                        border: '1px solid #1e2636',
                        boxShadow: 'var(--shadow-clay-sm)',
                        fontSize: '11.5px',
                      }}
                    >
                      <span style={{ color: '#8b949e', fontFamily: 'var(--font-mono)' }}>{rd.id}</span>
                      <span style={{ color: '#f0f6fc' }}>{rd.tool}</span>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                        <span style={{ color: '#3fb950', fontWeight: '600', fontSize: '10.5px' }}>
                          ✓ {rd.verdict}
                        </span>
                        <ChevronRight size={12} style={{ color: '#8b949e' }} />
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}

        {/* Tab 2: Evidence */}
        {activeTab === 'evidence' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '14px', fontSize: '11.5px' }}>
            <div style={{
              padding: '12px',
              borderRadius: '8px',
              backgroundColor: '#131822',
              border: '1px solid #1e2636',
              boxShadow: 'var(--shadow-clay-sm)',
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: '#8b949e' }}>Grounding Score</span>
                <span style={{ color: '#3fb950', fontWeight: '700' }}>
                  {Math.round((evidenceTab.groundingScore || 0.85) * 100)}%
                </span>
              </div>
              <div style={{
                height: '6px',
                borderRadius: '9999px',
                backgroundColor: '#1a202c',
                marginTop: '8px',
                overflow: 'hidden',
              }}>
                <div style={{
                  width: `${Math.round((evidenceTab.groundingScore || 0.85) * 100)}%`,
                  height: '100%',
                  backgroundColor: '#3fb950',
                }} />
              </div>
            </div>

            <div>
              <span style={{ color: '#f0f6fc', fontWeight: '600' }}>
                Empirical Claims ({evidenceTab.claimCount || 0})
              </span>
              <ul style={{ marginTop: '8px', paddingLeft: '16px', color: '#c9d1d9', lineHeight: '1.6' }}>
                {(evidenceTab.claims || [
                  'Git branch HEAD is verified against remote origin.',
                  'No uncommitted conflicting unstaged files in tree.'
                ]).map((claim, idx) => (
                  <li key={idx}>{claim}</li>
                ))}
              </ul>
            </div>

            <div style={{ borderTop: '1px solid #1a202c', paddingTop: '10px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', color: '#8b949e' }}>
                <span>Freshness</span>
                <span style={{ color: '#f0f6fc' }}>{evidenceTab.freshness || 'live'}</span>
              </div>
            </div>
          </div>
        )}

        {/* Tab 3: Policy */}
        {activeTab === 'policy' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '14px', fontSize: '11.5px' }}>
            <div>
              <span style={{ color: '#f0f6fc', fontWeight: '600' }}>Activated Policy Rules</span>
              <div style={{ marginTop: '8px', display: 'flex', flexDirection: 'column', gap: '6px' }}>
                {(policyDecision.rulesActivated || ['EgressPolicy', 'PathContainment']).map((rule, idx) => (
                  <div
                    key={idx}
                    style={{
                      padding: '6px 10px',
                      borderRadius: '6px',
                      backgroundColor: '#131822',
                      border: '1px solid #1e2636',
                      boxShadow: 'var(--shadow-clay-sm)',
                      color: '#f0f6fc',
                    }}
                  >
                    🛡️ {rule}
                  </div>
                ))}
              </div>
            </div>

            <div>
              <span style={{ color: '#f0f6fc', fontWeight: '600' }}>Precedence Chain</span>
              <p style={{ marginTop: '6px', color: '#8b949e', lineHeight: '1.5' }}>
                {policyDecision.precedence || 'Deterministic Safety Precedence (Hard Block > Human Gate > Auto Allow)'}
              </p>
            </div>
          </div>
        )}

        {/* Tab 4: Receipt */}
        {activeTab === 'receipt' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', fontSize: '11px' }}>
            <div style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              padding: '6px 10px',
              borderRadius: '6px',
              backgroundColor: 'rgba(63, 185, 80, 0.12)',
              border: '1px solid rgba(63, 185, 80, 0.28)',
              color: '#3fb950',
              fontWeight: '600',
            }}>
              <ShieldCheck size={14} />
              <span>HMAC Signed & Verified</span>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
              <span style={{ color: '#8b949e' }}>Decision ID</span>
              <span style={{ fontFamily: 'var(--font-mono)', color: '#f0f6fc' }}>
                {receiptTab.decisionId || decisionId}
              </span>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
              <span style={{ color: '#8b949e' }}>Action Hash</span>
              <span style={{
                fontFamily: 'var(--font-mono)',
                color: '#c9d1d9',
                wordBreak: 'break-all',
              }}>
                {receiptTab.actionHash || 'sha256:7e9b04fc41a7d6568297b83321588632a488c0352ef2bc560ec0a8c27e852d43'}
              </span>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
              <span style={{ color: '#8b949e' }}>State Hash</span>
              <span style={{
                fontFamily: 'var(--font-mono)',
                color: '#c9d1d9',
                wordBreak: 'break-all',
              }}>
                {receiptTab.stateHash || 'sha256:4b81c201a096180373ad412e8473e6a71e8bfb510ca1c1696a60db9372179b02'}
              </span>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
              <span style={{ color: '#8b949e' }}>Nonce</span>
              <span style={{ fontFamily: 'var(--font-mono)', color: '#f0f6fc' }}>
                {receiptTab.nonce || 'non_89a01f7c11'}
              </span>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
              <span style={{ color: '#8b949e' }}>Execution Mode (HMAC Bound)</span>
              <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                <span style={{ fontFamily: 'var(--font-mono)', color: '#38bdf8', fontWeight: '600' }}>
                  {receiptTab.execution_mode || receiptTab.executionMode || executionMode}
                </span>
                <span style={{ fontSize: '9.5px', color: '#34d399', backgroundColor: 'rgba(52, 211, 153, 0.1)', padding: '1px 5px', borderRadius: '3px' }}>
                  HMAC BOUND
                </span>
              </div>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
              <span style={{ color: '#8b949e' }}>Replay Protection Status</span>
              <span style={{
                fontFamily: 'var(--font-mono)',
                color: receiptTab.is_executed ? '#fbbf24' : '#34d399',
                fontWeight: '600',
              }}>
                {receiptTab.is_executed ? 'CONSUMED (Single-Use Locked)' : 'AVAILABLE (Pending Execution)'}
              </span>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
              <span style={{ color: '#8b949e' }}>Cryptographic Signature</span>
              <span style={{
                fontFamily: 'var(--font-mono)',
                color: '#8b949e',
                wordBreak: 'break-all',
              }}>
                {receiptTab.signature || 'hmac-sha256:39a7b212f008cb042aaefc32986423a884efbb5c'}
              </span>
            </div>
          </div>
        )}
          </div>
        )}
      </div>
    </aside>
  );
}
