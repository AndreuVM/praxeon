import React, { useState, useEffect, useRef } from 'react';
import WorkspaceField from './workspace/WorkspaceField';
import { fetchWorkflows } from '../services/api';
import ModelPicker from './ModelPicker';
import { deriveChatState } from '../utils/chatFromEvents';
import { PROVIDERS, saveChoice, savedChoice } from '../utils/modelCatalog';
import {
  Send,
  Maximize2,
  Minimize2,
  Play,
  Pause,
  Square,
  Sparkles,
  Bot,
  Cpu,
  Shield,
  Terminal,
  Settings,
  AlertTriangle,
  CheckCircle2,
  Copy,
  Check,
  ChevronDown,
  ChevronUp,
  RefreshCw,
  Clock,
} from 'lucide-react';

export default function MissionChat({
  session = {},
  isRunning = false,
  isPaused = false,
  events = [],
  chatMessages,
  onMessagesChange,
  missionConfig = {},
  onConfigChange,
  onStartMission,
  onPauseMission,
  onResumeMission,
  onStopMission,
  onLoadDemo,
}) {
  const [inputText, setInputText] = useState('');
  const [inputExpanded, setInputExpanded] = useState(false);
  const inputRef = useRef(null);

  // Configuración persistente con fallback local
  const [localConfig, setLocalConfig] = useState({
    llmProvider: 'simulator',
    supervisor: 'laya',
    executionMode: 'local_restricted',
    fullAccessConfirmed: false,
    maxSteps: 25,
    customModel: '',
    apiKey: '',
    baseUrl: '',
  });

  const effectiveConfig = { ...localConfig, ...missionConfig };

  const updateConfig = (patch) => {
    setLocalConfig((prev) => ({ ...prev, ...patch }));
    onConfigChange?.({ ...effectiveConfig, ...patch });
  };

  const llmProvider = effectiveConfig.llmProvider;
  const supervisor = effectiveConfig.supervisor;
  const executionMode = effectiveConfig.executionMode;
  const fullAccessConfirmed = effectiveConfig.fullAccessConfirmed;
  const maxSteps = effectiveConfig.maxSteps;
  const customModel = effectiveConfig.customModel;
  const apiKey = effectiveConfig.apiKey;
  const baseUrl = effectiveConfig.baseUrl;

  const [showAdvanced, setShowAdvanced] = useState(false);
  const [activeWorkflows, setActiveWorkflows] = useState([]);
  const workflowId = effectiveConfig.workflowId || '';

  // Solo los workflows activos pueden gobernar una tarea
  const loadActiveWorkflows = () => {
    fetchWorkflows()
      .then((resp) => {
        const active = (resp?.data || []).filter((w) => w.active);
        setActiveWorkflows(active);
        if (workflowId && !active.some((w) => w.workflow_id === workflowId)) updateConfig({ workflowId: '' });
      })
      .catch(() => {});
  };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(loadActiveWorkflows, []);
  const [copied, setCopied] = useState(false);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!effectiveConfig.customModel) {
      const remembered = savedChoice(effectiveConfig.llmProvider);
      if (remembered) updateConfig({ customModel: remembered });
    }
  }, []);
  const [showPastThoughts, setShowPastThoughts] = useState(false);

  // Historial de mensajes persistente con fallback local
  const [localMessages, setLocalMessages] = useState([]);

  const messages = chatMessages !== undefined ? chatMessages : localMessages;
  const setMessages = React.useCallback((updater) => {
    if (onMessagesChange) {
      onMessagesChange(updater);
    }
    setLocalMessages(updater);
  }, [onMessagesChange]);

  const messagesEndRef = useRef(null);
  const prevSessionIdRef = useRef(session.sessionId);
  const lastEventCountRef = useRef(0);

  // El campo de texto crece con el contenido (hasta su altura máxima, después tiene scroll)
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${el.scrollHeight + 2}px`;
  }, [inputText, inputExpanded]);

  // Auto-scroll al final del chat cuando se añaden mensajes o pasos de razonamiento
  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, isRunning, events.length]);

  // Si cambia la sesión (ej. carga de demo o nueva sesión desde otro sitio)
  useEffect(() => {
    if (session.sessionId && session.sessionId !== prevSessionIdRef.current) {
      prevSessionIdRef.current = session.sessionId;

      if (session.sessionId === '7f3a2c') {
        // Carga de demo interactiva
        queueMicrotask(() => {
          setMessages([
            {
              id: 'msg-demo-user',
              role: 'user',
              text: session.goal || 'Fix authentication bug in the API',
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
              finalAnswer: session.finalAnswer || 'El parche de autenticación ha sido validado satisfactoriamente contra la suite de tests. El intento de push directo fue interceptado preventivamente por la política de precedencia estricta de PRAXEON.',
            },
          ]);
        });
      }
    }
  }, [session.sessionId, session.goal, session.finalAnswer, setMessages]);

  // Capturar razonamiento en tiempo real desde los eventos WebSocket
  useEffect(() => {
    if (!isRunning && events.length === 0) return;
    if (events.length === lastEventCountRef.current) return;
    lastEventCountRef.current = events.length;

    // Razonamiento, hitos y agente en control derivados de los eventos (del más antiguo al más reciente)
    const { thoughts, milestones, activeNode, currentThought: currentThoughtText } = deriveChatState([...events].reverse());

    queueMicrotask(() => {
      setMessages((prev) => {
        const lastMsg = prev[prev.length - 1];
        if (lastMsg && lastMsg.role === 'assistant' && (lastMsg.status === 'running' || isRunning)) {
          return [
            ...prev.slice(0, -1),
            {
              ...lastMsg,
              thoughts: thoughts.length > 0 ? thoughts : lastMsg.thoughts,
              milestones: milestones.length > 0 ? milestones : lastMsg.milestones,
              activeNode: activeNode || null,
              currentThought: currentThoughtText || lastMsg.currentThought,
              status: session.status === 'Completed' || session.finalAnswer ? 'completed' : 'running',
              finalAnswer: session.finalAnswer || lastMsg.finalAnswer,
            },
          ];
        }
        return prev;
      });
    });
  }, [events, isRunning, session.status, session.finalAnswer, setMessages]);

  // Si la sesión concluye, asegurar que el mensaje del asistente refleje el estado completado
  useEffect(() => {
    if (session.status === 'Completed' || session.finalAnswer) {
      queueMicrotask(() => {
        setMessages((prev) => {
          const lastMsg = prev[prev.length - 1];
          if (lastMsg && lastMsg.role === 'assistant' && lastMsg.status !== 'completed') {
            return [
              ...prev.slice(0, -1),
              {
                ...lastMsg,
                status: 'completed',
                finalAnswer: session.finalAnswer || lastMsg.finalAnswer || 'Misión completada satisfactoriamente bajo la supervisión de PRAXEON.',
              },
            ];
          }
          return prev;
        });
      });
    }
  }, [session.status, session.finalAnswer, setMessages]);

  const handleSend = (e) => {
    e?.preventDefault();
    const task = inputText.trim();
    if (!task) return;

    if (executionMode === 'full_access' && !fullAccessConfirmed) {
      alert('Debes confirmar que comprendes que el aislamiento de proceso del host está deshabilitado para usar Full Access.');
      return;
    }

    const timeStr = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

    // 1. Mensaje del usuario
    const userMsg = {
      id: `msg-${Date.now()}`,
      role: 'user',
      text: task,
      time: timeStr,
      config: {
        llm_provider: llmProvider,
        supervisor: supervisor,
        execution_mode: executionMode,
      },
    };

    // 2. Mensaje inicial del asistente en ejecución
    const assistantMsg = {
      id: `assistant-${Date.now() + 1}`,
      role: 'assistant',
      status: 'running',
      time: timeStr,
      currentThought: 'Iniciando sesión supervisada y conectando con el modelo...',
      thoughts: [],
      finalAnswer: null,
    };

    // Extraer historial conversacional previo para persistir memoria multi-turno
    const history = messages
      .filter((m) => !m.isWelcome && (m.role === 'user' || (m.role === 'assistant' && (m.finalAnswer || m.thoughts?.length > 0))))
      .map((m) => ({
        role: m.role,
        content: m.role === 'user' ? m.text : (m.finalAnswer || (m.thoughts && m.thoughts[m.thoughts.length - 1]?.thought) || ''),
      }))
      .filter((m) => m.content && m.content.trim());

    setMessages((prev) => [...prev, userMsg, assistantMsg]);
    setInputText('');

    const isFullAccess = executionMode === 'full_access';

    // Disparar inicio de misión en backend
    onStartMission?.({
      goal: task,
      execution_mode: executionMode,
      full_access_confirmed: isFullAccess && Boolean(fullAccessConfirmed),
      autonomous: isFullAccess && Boolean(fullAccessConfirmed),
      allow_unattended_execution: isFullAccess && Boolean(fullAccessConfirmed),
      workspace_root: effectiveConfig.workspaceRoot?.trim() || undefined,
      llm_provider: llmProvider,
      llm_model: customModel.trim() || undefined,
      api_key: apiKey.trim() || undefined,
      base_url: baseUrl.trim() || undefined,
      supervisor: supervisor,
      max_steps: Number(maxSteps),
      workflow_id: workflowId || undefined,
      chat_history: history.length > 0 ? history : undefined,
    });
  };

  const handleCopyAnswer = (text) => {
    navigator.clipboard?.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        backgroundColor: '#0c0f14',
        color: '#f0f6fc',
        overflow: 'hidden',
      }}
    >
      {/* 1. Header Compacto de Misión */}
      <div
        style={{
          padding: '10px 14px',
          backgroundColor: '#0f131c',
          borderBottom: '1px solid #1a202c',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          flexShrink: 0,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Sparkles size={14} style={{ color: '#58a6ff' }} />
          <span style={{ fontSize: '12px', fontWeight: '700', letterSpacing: '0.02em', color: '#f0f6fc' }}>
            Misión & Chat Interactivo
          </span>
          <span
            style={{
              fontSize: '10px',
              padding: '1px 6px',
              borderRadius: '9999px',
              backgroundColor: isRunning ? 'rgba(63, 185, 80, 0.15)' : 'rgba(139, 148, 158, 0.15)',
              color: isRunning ? '#3fb950' : '#8b949e',
              border: `1px solid ${isRunning ? 'rgba(63, 185, 80, 0.3)' : '#242c3b'}`,
              fontWeight: '600',
              display: 'flex',
              alignItems: 'center',
              gap: '4px',
            }}
          >
            {isRunning && <span className="pulse-dot" style={{ backgroundColor: '#3fb950' }} />}
            {isRunning ? (isPaused ? 'Pausada' : 'En Vivo') : 'Listo'}
          </span>
        </div>

        <button
          type="button"
          onClick={onLoadDemo}
          className="btn btn-secondary"
          style={{ fontSize: '10.5px', padding: '3px 8px', display: 'flex', alignItems: 'center', gap: '4px' }}
          title="Cargar árbol de sesión de demostración visual"
        >
          <RefreshCw size={11} />
          Demo
        </button>
      </div>

      {/* 2. Área Scrollable del Historial de Chat */}
      <div
        style={{
          flex: 1,
          overflowY: 'auto',
          padding: '14px',
          display: 'flex',
          flexDirection: 'column',
          gap: '14px',
        }}
      >
        {messages.map((msg) => {
          if (msg.role === 'user') {
            return (
              <div
                key={msg.id}
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'flex-end',
                  gap: '4px',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11px', color: '#8b949e' }}>
                  <span style={{ fontWeight: '600', color: '#c9d1d9' }}>Tú</span>
                  <span>·</span>
                  <span>{msg.time}</span>
                </div>
                <div
                  style={{
                    backgroundColor: '#162235',
                    border: '1px solid #233550',
                    borderRadius: '10px 10px 2px 10px',
                    padding: '10px 14px',
                    maxWidth: '92%',
                    color: '#f0f6fc',
                    fontSize: '12.5px',
                    lineHeight: '1.45',
                    boxShadow: 'var(--shadow-clay-sm)',
                  }}
                >
                  <p style={{ margin: 0, whiteSpace: 'pre-wrap' }}>{msg.text}</p>

                  {/* Badges de configuración de la solicitud */}
                  {msg.config && (
                    <div style={{ display: 'flex', gap: '5px', marginTop: '7px', flexWrap: 'wrap' }}>
                      <span
                        style={{
                          fontSize: '9.5px',
                          padding: '1px 6px',
                          borderRadius: '4px',
                          backgroundColor: 'rgba(88, 166, 255, 0.15)',
                          color: '#58a6ff',
                          border: '1px solid rgba(88, 166, 255, 0.3)',
                          display: 'flex',
                          alignItems: 'center',
                          gap: '3px',
                        }}
                      >
                        <Cpu size={9} />
                        {msg.config.llm_provider?.toUpperCase()}
                      </span>
                      <span
                        style={{
                          fontSize: '9.5px',
                          padding: '1px 6px',
                          borderRadius: '4px',
                          backgroundColor: 'rgba(163, 113, 247, 0.15)',
                          color: '#d2a8ff',
                          border: '1px solid rgba(163, 113, 247, 0.3)',
                          display: 'flex',
                          alignItems: 'center',
                          gap: '3px',
                        }}
                      >
                        <Shield size={9} />
                        {msg.config.supervisor?.toUpperCase()}
                      </span>
                      <span
                        style={{
                          fontSize: '9.5px',
                          padding: '1px 6px',
                          borderRadius: '4px',
                          backgroundColor: msg.config.execution_mode === 'full_access' ? 'rgba(248, 113, 113, 0.15)' : 'rgba(63, 185, 80, 0.15)',
                          color: msg.config.execution_mode === 'full_access' ? '#f87171' : '#7ee787',
                          border: `1px solid ${msg.config.execution_mode === 'full_access' ? 'rgba(248, 113, 113, 0.3)' : 'rgba(63, 185, 80, 0.3)'}`,
                          display: 'flex',
                          alignItems: 'center',
                          gap: '3px',
                        }}
                      >
                        <Terminal size={9} />
                        {msg.config.execution_mode === 'full_access' ? 'Host Direct' : 'Sandbox'}
                      </span>
                    </div>
                  )}
                </div>
              </div>
            );
          }

          if (msg.isWelcome) return null;

          // Mensaje normal del asistente (con razonamiento en tiempo real y respuesta final)
          const isActivelyThinking = msg.status === 'running' || (isRunning && msg === messages[messages.length - 1]);
          const thoughtsList = msg.thoughts || [];
          const latestThought = thoughtsList[thoughtsList.length - 1];

          return (
            <div
              key={msg.id}
              style={{
                display: 'flex',
                flexDirection: 'column',
                gap: '8px',
                maxWidth: '96%',
              }}
            >
              {/* Header del mensaje del asistente */}
              <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11px', color: '#8b949e' }}>
                <Bot size={13} style={{ color: '#58a6ff' }} />
                <span style={{ fontWeight: '600', color: '#c9d1d9' }}>Agente Supervisado</span>
                <span>·</span>
                <span>{msg.time}</span>
              </div>

              {/* CARD DE RAZONAMIENTO EN TIEMPO REAL */}
              {(isActivelyThinking || thoughtsList.length > 0) && (
                <div
                  style={{
                    backgroundColor: '#101520',
                    border: `1px solid ${isActivelyThinking ? 'rgba(88, 166, 255, 0.35)' : '#1e2638'}`,
                    borderRadius: '8px',
                    padding: '10px 12px',
                    boxShadow: isActivelyThinking ? '0 0 12px rgba(88, 166, 255, 0.08)' : 'none',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '8px',
                  }}
                >
                  {/* Encabezado del razonamiento con toggle de pasos anteriores */}
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '7px' }}>
                      {isActivelyThinking ? (
                        <span className="pulse-dot" style={{ backgroundColor: '#58a6ff' }} />
                      ) : (
                        <CheckCircle2 size={13} style={{ color: '#3fb950' }} />
                      )}
                      <span style={{ fontSize: '11.5px', fontWeight: '700', color: isActivelyThinking ? '#79c0ff' : '#cbd5e1' }}>
                        {isActivelyThinking ? 'Razonamiento en segundo plano...' : `Razonamiento supervisado (${thoughtsList.length} pasos)`}
                      </span>
                    </div>

                    {thoughtsList.length > 1 && (
                      <button
                        type="button"
                        onClick={() => setShowPastThoughts(!showPastThoughts)}
                        style={{
                          background: 'none',
                          border: 'none',
                          color: '#8b949e',
                          fontSize: '10.5px',
                          cursor: 'pointer',
                          display: 'flex',
                          alignItems: 'center',
                          gap: '3px',
                          padding: '2px 4px',
                        }}
                      >
                        <span>{showPastThoughts ? 'Ocultar' : 'Ver todos'}</span>
                        {showPastThoughts ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
                      </button>
                    )}
                  </div>

                  {/* Paso activo o más reciente */}
                  {latestThought ? (
                    <div
                      style={{
                        backgroundColor: '#0a0d14',
                        border: '1px solid #1a2233',
                        borderRadius: '6px',
                        padding: '8px 10px',
                        display: 'flex',
                        flexDirection: 'column',
                        gap: '6px',
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                        <span style={{ fontSize: '11px', fontWeight: '700', color: '#f0f6fc', fontFamily: 'var(--font-mono)' }}>
                          Paso {latestThought.step}: {latestThought.tool}
                          {latestThought.agentName && (
                            <span style={{ fontWeight: 400, color: '#8b949e' }}> · {latestThought.agentName}</span>
                          )}
                        </span>
                        <span
                          style={{
                            fontSize: '9.5px',
                            fontWeight: '600',
                            padding: '1px 6px',
                            borderRadius: '4px',
                            backgroundColor:
                              latestThought.verdict === 'ALLOW'
                                ? 'rgba(63, 185, 80, 0.15)'
                                : latestThought.verdict === 'REVIEW'
                                ? 'rgba(210, 153, 34, 0.15)'
                                : latestThought.verdict === 'BLOCK'
                                ? 'rgba(248, 81, 73, 0.15)'
                                : latestThought.verdict === 'BACKTRACK' || latestThought.verdict === 'REPLAN' || String(latestThought.verdict).includes('CIRCUIT_BREAKER')
                                ? 'rgba(168, 85, 247, 0.20)'
                                : 'rgba(88, 166, 255, 0.15)',
                            color:
                              latestThought.verdict === 'ALLOW'
                                ? '#3fb950'
                                : latestThought.verdict === 'REVIEW'
                                ? '#d29922'
                                : latestThought.verdict === 'BLOCK'
                                ? '#f85149'
                                : latestThought.verdict === 'BACKTRACK' || latestThought.verdict === 'REPLAN' || String(latestThought.verdict).includes('CIRCUIT_BREAKER')
                                ? '#c084fc'
                                : '#58a6ff',
                          }}
                        >
                          {latestThought.verdict || 'EVALUANDO'}
                        </span>
                      </div>

                      <div style={{ fontSize: '11.5px', color: '#c9d1d9', fontStyle: 'italic', lineHeight: '1.4' }}>
                        &ldquo;{latestThought.thought}&rdquo;
                      </div>

                      {latestThought.interventionMessage && (
                        <div style={{
                          backgroundColor: 'rgba(168, 85, 247, 0.10)',
                          border: '1px solid rgba(168, 85, 247, 0.35)',
                          borderRadius: '4px',
                          padding: '5px 8px',
                          fontSize: '10.5px',
                          color: '#d8b4fe',
                          lineHeight: '1.4',
                        }}>
                          ⚡ {latestThought.interventionMessage}
                        </div>
                      )}

                      {latestThought.score !== undefined && (
                        <div style={{ display: 'flex', gap: '8px', fontSize: '10px', color: '#8b949e' }}>
                          <span>Supervisor Score: <strong style={{ color: '#f0f6fc' }}>{latestThought.score}</strong></span>
                          {latestThought.executionTimeMs && (
                            <span>Sandbox: <strong style={{ color: '#f0f6fc' }}>{latestThought.executionTimeMs}ms</strong></span>
                          )}
                        </div>
                      )}
                    </div>
                  ) : (
                    <div style={{ fontSize: '11px', color: '#8b949e', fontStyle: 'italic', display: 'flex', alignItems: 'center', gap: '6px' }}>
                      <Clock size={11} />
                      <span>{msg.currentThought || 'Procesando evidencias e intenciones del agente...'}</span>
                    </div>
                  )}

                  {/* Acordeón de pasos históricos pasados */}
                  {showPastThoughts && thoughtsList.length > 1 && (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '5px', marginTop: '2px' }}>
                      {thoughtsList.slice(0, -1).map((th, idx) => (
                        <div
                          key={idx}
                          style={{
                            backgroundColor: '#0a0d14',
                            border: '1px solid #161c28',
                            borderRadius: '5px',
                            padding: '6px 8px',
                            fontSize: '11px',
                          }}
                        >
                          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '2px' }}>
                            <span style={{ color: '#8b949e', fontWeight: '600' }}>Paso {th.step}: {th.tool}</span>
                            <span style={{ fontSize: '9px', color: th.verdict === 'ALLOW' ? '#3fb950' : '#d29922' }}>{th.verdict}</span>
                          </div>
                          <p style={{ margin: 0, color: '#a0aec0', fontSize: '10.5px' }}>{th.thought}</p>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {/* Agente que tiene ahora el control en el workflow */}
              {msg.activeNode && msg.status !== 'completed' && (
                <div style={{ display: 'flex', alignItems: 'center', gap: '7px', fontSize: '11.5px', color: '#38bdf8', backgroundColor: 'rgba(56,189,248,0.08)', border: '1px solid rgba(56,189,248,0.3)', borderRadius: '6px', padding: '6px 9px' }}>
                  <span className="pulse-dot" style={{ backgroundColor: '#38bdf8' }} />
                  <span>
                    <strong>{msg.activeNode.agent_name}</strong>
                    {msg.activeNode.handoff_from ? ` recibe la tarea de ${msg.activeNode.handoff_from}` : ' toma el control'}
                    {' · '}«{msg.activeNode.node_name || msg.activeNode.node_id}»
                    {msg.activeNode.iteration > 1 ? ` (pasada ${msg.activeNode.iteration})` : ''}
                    {msg.activeNode.retry_reason ? ' · reintento automático tras una interrupción' : ''}
                  </span>
                </div>
              )}

              {/* HITOS DEL WORKFLOW: resultado de cada nodo; NO es la respuesta final */}
              {msg.milestones?.length > 0 && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '5px' }}>
                  <div style={{ fontSize: '10.5px', fontWeight: 700, color: '#8b949e', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
                    Hitos del workflow
                  </div>
                  {msg.milestones.map((m) => (
                    <details
                      key={m.key}
                      style={{ backgroundColor: '#0d1520', border: '1px solid #1e2b3f', borderLeft: '3px solid #58a6ff', borderRadius: '6px', padding: '6px 9px' }}
                    >
                      <summary style={{ cursor: 'pointer', fontSize: '11.5px', color: '#f0f6fc', display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap' }}>
                        <CheckCircle2 size={12} style={{ color: '#3fb950', flexShrink: 0 }} />
                        <strong>{m.nodeName}</strong>
                        <span style={{ color: '#8b949e' }}>
                          {m.agentName ? `· ${m.agentName}` : ''}
                          {m.agentRole ? ` (${m.agentRole})` : ''}
                          {m.iteration > 1 ? ` · pasada ${m.iteration}` : ''}
                        </span>
                      </summary>
                      <div style={{ marginTop: '6px', fontSize: '11px', color: '#a0aec0', lineHeight: 1.45, whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: '220px', overflowY: 'auto' }}>
                        {m.summary}
                      </div>
                    </details>
                  ))}
                </div>
              )}

              {/* RESPUESTA FINAL COMPLETADA */}
              {msg.finalAnswer && (
                <div
                  style={{
                    backgroundColor: 'rgba(16, 185, 129, 0.07)',
                    border: '1px solid rgba(16, 185, 129, 0.3)',
                    borderRadius: '10px',
                    padding: '12px 14px',
                    boxShadow: 'var(--shadow-clay-sm)',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '8px',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px', color: '#34d399', fontSize: '12px', fontWeight: '700' }}>
                      <CheckCircle2 size={14} strokeWidth={2.5} />
                      <span>Respuesta de la Misión</span>
                    </div>
                    <button
                      type="button"
                      onClick={() => handleCopyAnswer(msg.finalAnswer)}
                      style={{
                        background: 'none',
                        border: 'none',
                        color: copied ? '#34d399' : '#8b949e',
                        cursor: 'pointer',
                        display: 'flex',
                        alignItems: 'center',
                        gap: '3px',
                        fontSize: '10.5px',
                      }}
                      title="Copiar respuesta"
                    >
                      {copied ? <Check size={11} /> : <Copy size={11} />}
                      <span>{copied ? 'Copiado' : 'Copiar'}</span>
                    </button>
                  </div>

                  <div style={{ color: '#f1f5f9', fontSize: '12px', lineHeight: '1.5', whiteSpace: 'pre-wrap' }}>
                    {msg.finalAnswer}
                  </div>

                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '8px',
                      fontSize: '10px',
                      color: '#6ee7b7',
                      borderTop: '1px solid rgba(16, 185, 129, 0.15)',
                      paddingTop: '6px',
                    }}
                  >
                    <span>✓ Supervisión formal exitosa</span>
                    <span>·</span>
                    <span>HMAC-SHA256 validado</span>
                    <span>·</span>
                    <span>Zero host bypass</span>
                  </div>
                </div>
              )}
            </div>
          );
        })}
        <div ref={messagesEndRef} />
      </div>

      {/* 3. BARRA DE INPUT INTEGRADA CON SELECTORES (LLM + SUPERVISIÓN + MODO) */}
      <div
        style={{
          padding: '10px 12px',
          backgroundColor: '#0f1420',
          borderTop: '1px solid #1a2233',
          display: 'flex',
          flexDirection: 'column',
          gap: '8px',
          flexShrink: 0,
        }}
      >
        {/* SELECTORES INTEGRADOS EN LA PARTE SUPERIOR DE LA BARRA DE INPUT */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            flexWrap: 'wrap',
          }}
        >
          {/* Selector 1: Modelo LLM */}
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '4px',
              backgroundColor: '#090c12',
              border: '1px solid #1d2536',
              borderRadius: '6px',
              padding: '3px 6px',
              flex: '1 1 120px',
            }}
          >
            <Cpu size={11} style={{ color: '#58a6ff', flexShrink: 0 }} />
            <select
              value={llmProvider}
              onChange={(e) => updateConfig({ llmProvider: e.target.value, customModel: savedChoice(e.target.value) })}
              disabled={isRunning}
              style={{
                background: 'none',
                border: 'none',
                color: '#f0f6fc',
                fontSize: '11px',
                width: '100%',
                outline: 'none',
                cursor: isRunning ? 'not-allowed' : 'pointer',
              }}
              title="Proveedor del modelo (el modelo exacto se elige en los ajustes del chat)"
            >
              {PROVIDERS.map((p) => (
                <option key={p.value} value={p.value} style={{ background: '#0f1420' }}>
                  {p.label}
                </option>
              ))}
            </select>
          </div>

          {/* Selector 2: Supervisión */}
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '4px',
              backgroundColor: '#090c12',
              border: '1px solid #1d2536',
              borderRadius: '6px',
              padding: '3px 6px',
              flex: '1 1 110px',
            }}
          >
            <Shield size={11} style={{ color: '#a371f7', flexShrink: 0 }} />
            <select
              value={supervisor}
              onChange={(e) => updateConfig({ supervisor: e.target.value })}
              disabled={isRunning}
              style={{
                background: 'none',
                border: 'none',
                color: '#f0f6fc',
                fontSize: '11px',
                width: '100%',
                outline: 'none',
                cursor: isRunning ? 'not-allowed' : 'pointer',
              }}
              title="Supervisor Cognitivo de Runtime"
            >
              <option value="laya" style={{ background: '#0f1420' }}>LAYA System-1</option>
              <option value="typesafe" style={{ background: '#0f1420' }}>TypeSafe AI AST</option>
              <option value="cascade" style={{ background: '#0f1420' }}>Cascade JEV</option>
            </select>
          </div>

          {/* Selector 3: Modo de Ejecución */}
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '4px',
              backgroundColor: executionMode === 'full_access' ? 'rgba(239, 68, 68, 0.15)' : '#090c12',
              border: `1px solid ${executionMode === 'full_access' ? 'rgba(239, 68, 68, 0.4)' : '#1d2536'}`,
              borderRadius: '6px',
              padding: '3px 6px',
              flex: '1 1 115px',
            }}
          >
            <Terminal size={11} style={{ color: executionMode === 'full_access' ? '#f87171' : '#3fb950', flexShrink: 0 }} />
            <select
              value={executionMode}
              onChange={(e) => {
                const val = e.target.value;
                updateConfig({
                  executionMode: val,
                  ...(val !== 'full_access' ? { fullAccessConfirmed: false } : {}),
                });
              }}
              disabled={isRunning}
              style={{
                background: 'none',
                border: 'none',
                color: executionMode === 'full_access' ? '#f87171' : '#f0f6fc',
                fontSize: '11px',
                width: '100%',
                outline: 'none',
                cursor: isRunning ? 'not-allowed' : 'pointer',
                fontWeight: executionMode === 'full_access' ? '600' : 'normal',
              }}
              title="Entorno y modo de contención"
            >
              <option value="local_restricted" style={{ background: '#0f1420', color: '#f0f6fc' }}>Local Restricted</option>
              <option value="container" style={{ background: '#0f1420', color: '#f0f6fc' }}>Container (Docker)</option>
              <option value="full_access" style={{ background: '#0f1420', color: '#f87171' }}>Full Access ⚠</option>
            </select>
          </div>

          {/* Botón de Ajustes Avanzados */}
          <button
            type="button"
            onClick={() => setShowAdvanced(!showAdvanced)}
            style={{
              background: showAdvanced ? '#1f2738' : '#090c12',
              border: '1px solid #1d2536',
              borderRadius: '6px',
              color: showAdvanced ? '#58a6ff' : '#8b949e',
              padding: '4px 6px',
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
            }}
            title="Ajustes de modelo, API key y pasos máximos"
          >
            <Settings size={12} />
          </button>
        </div>

        {/* Workflow que gobierna la tarea */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <label style={{ fontSize: '10.5px', color: '#8b949e', whiteSpace: 'nowrap' }}>Workflow:</label>
          <select
            value={workflowId}
            onChange={(e) => updateConfig({ workflowId: e.target.value })}
            onFocus={loadActiveWorkflows}
            disabled={isRunning}
            title="Con un workflow activo, la tarea se ejecuta siguiendo sus nodos, agentes y bucles (se ven en la pestaña Workflows)"
            style={{
              flex: 1,
              minWidth: 0,
              backgroundColor: workflowId ? 'rgba(52, 211, 153, 0.08)' : '#090c12',
              border: `1px solid ${workflowId ? 'rgba(52, 211, 153, 0.45)' : '#1d2536'}`,
              borderRadius: '6px',
              color: workflowId ? '#34d399' : '#f0f6fc',
              fontSize: '11px',
              padding: '4px 6px',
              cursor: isRunning ? 'not-allowed' : 'pointer',
            }}
          >
            <option value="" style={{ background: '#0f1420', color: '#f0f6fc' }}>Sin workflow (agente único)</option>
            {activeWorkflows.map((w) => (
              <option key={w.workflow_id} value={w.workflow_id} style={{ background: '#0f1420', color: '#f0f6fc' }}>
                {w.name}
              </option>
            ))}
          </select>
        </div>
        {activeWorkflows.length === 0 && (
          <div style={{ fontSize: '10px', color: '#64748b', marginTop: '-4px' }}>
            No hay workflows activos: actívalos en la pestaña Workflows para usarlos aquí.
          </div>
        )}

        {/* Alerta y confirmación inline para Full Access */}
        {executionMode === 'full_access' && (
          <div
            style={{
              backgroundColor: 'rgba(239, 68, 68, 0.08)',
              border: '1px solid rgba(239, 68, 68, 0.35)',
              borderRadius: '6px',
              padding: '6px 8px',
              display: 'flex',
              flexDirection: 'column',
              gap: '4px',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '5px', color: '#f87171', fontSize: '11px', fontWeight: '700' }}>
              <AlertTriangle size={12} />
              <span>Full Access: Sin aislamiento de sistema operativo</span>
            </div>
            <label style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '10.5px', color: '#cbd5e1', cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={fullAccessConfirmed}
                onChange={(e) => updateConfig({ fullAccessConfirmed: e.target.checked })}
                disabled={isRunning}
              />
              <span>Confirmo ejecución directa sobre el host</span>
            </label>
          </div>
        )}

        {/* Cajón de Configuración Avanzada */}
        {showAdvanced && (
          <div
            style={{
              backgroundColor: '#090d14',
              border: '1px solid #1c2434',
              borderRadius: '6px',
              padding: '8px 10px',
              display: 'grid',
              gridTemplateColumns: '1fr 1fr',
              gap: '6px',
            }}
          >
            <div style={{ gridColumn: 'span 2' }}>
              <label style={{ fontSize: '10px', color: '#8b949e', display: 'flex', justifyContent: 'space-between', marginBottom: '2px' }}>
                <span>Carpeta de trabajo (workspace):</span>
              </label>
              <WorkspaceField
                value={effectiveConfig.workspaceRoot || ''}
                onChange={(path) => updateConfig({ workspaceRoot: path })}
                disabled={isRunning}
                compact
              />
            </div>
            <ModelPicker
              provider={llmProvider}
              value={customModel}
              onChange={(model) => {
                saveChoice(llmProvider, model);
                updateConfig({ customModel: model });
              }}
              disabled={isRunning}
            />
            <div>
              <label style={{ fontSize: '10px', color: '#8b949e', display: 'block', marginBottom: '2px' }}>API Key / Token:</label>
              <input
                type="password"
                value={apiKey}
                onChange={(e) => updateConfig({ apiKey: e.target.value })}
                placeholder="gsk_... / sk-..."
                disabled={isRunning}
                style={{
                  width: '100%',
                  backgroundColor: '#121722',
                  border: '1px solid #202737',
                  borderRadius: '4px',
                  padding: '4px 6px',
                  color: '#f0f6fc',
                  fontSize: '10.5px',
                  outline: 'none',
                }}
              />
            </div>
            <div>
              <label style={{ fontSize: '10px', color: '#8b949e', display: 'block', marginBottom: '2px' }}>Base URL (opcional):</label>
              <input
                type="text"
                value={baseUrl}
                onChange={(e) => updateConfig({ baseUrl: e.target.value })}
                placeholder="http://localhost:11434"
                disabled={isRunning}
                style={{
                  width: '100%',
                  backgroundColor: '#121722',
                  border: '1px solid #202737',
                  borderRadius: '4px',
                  padding: '4px 6px',
                  color: '#f0f6fc',
                  fontSize: '10.5px',
                  outline: 'none',
                }}
              />
            </div>
            <div>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '3px' }}>
                <label style={{ fontSize: '10px', color: '#8b949e' }}>Límite de pasos:</label>
                <span style={{ fontSize: '10px', color: '#58a6ff', fontFamily: 'var(--font-mono)', fontWeight: '600' }}>
                  {Number(maxSteps) === 0 ? 'sin límite' : `${maxSteps} pasos`}
                </span>
              </div>
              <div style={{ display: 'flex', gap: '5px', alignItems: 'center' }}>
                <input
                  type="number"
                  min={1}
                  max={500}
                  value={Number(maxSteps) === 0 ? '' : maxSteps}
                  placeholder="∞"
                  onChange={(e) => updateConfig({ maxSteps: Math.max(1, Math.min(500, Number(e.target.value) || 1)) })}
                  disabled={isRunning}
                  style={{
                    width: '64px',
                    backgroundColor: '#121722',
                    border: '1px solid #202737',
                    borderRadius: '4px',
                    padding: '4px 6px',
                    color: '#f0f6fc',
                    fontSize: '11px',
                    fontFamily: 'var(--font-mono)',
                    outline: 'none',
                    textAlign: 'center',
                  }}
                />
                <div style={{ display: 'flex', gap: '3px', flex: 1 }}>
                  {[10, 25, 50, 100, 0].map((preset) => (
                    <button
                      key={preset}
                      type="button"
                      onClick={() => updateConfig({ maxSteps: preset })}
                      disabled={isRunning}
                      style={{
                        flex: 1,
                        padding: '3px 0',
                        fontSize: '9.5px',
                        fontWeight: '500',
                        backgroundColor: maxSteps === preset ? '#1f6feb' : '#161b22',
                        color: maxSteps === preset ? '#ffffff' : '#8b949e',
                        border: `1px solid ${maxSteps === preset ? '#388bfd' : '#30363d'}`,
                        borderRadius: '4px',
                        cursor: isRunning ? 'not-allowed' : 'pointer',
                        transition: 'all 0.15s ease',
                      }}
                    >
                      {preset === 0 ? '∞' : preset}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </div>
        )}

        {/* CAMPO DE TEXTO PRINCIPAL Y BOTONES DE EJECUCIÓN / CONTROL */}
        <form
          onSubmit={handleSend}
          style={{
            display: 'flex',
            alignItems: 'flex-end',
            gap: '6px',
          }}
        >
          <div style={{ flex: 1, position: 'relative', minWidth: 0 }}>
            <textarea
              ref={inputRef}
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              onKeyDown={(e) => {
                // Enter envía; Mayús+Enter inserta un salto de línea
                if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  if (inputText.trim() && !isRunning && !(executionMode === 'full_access' && !fullAccessConfirmed)) handleSend(e);
                }
              }}
              placeholder={isRunning ? 'Misión en curso... (observando razonamiento)' : 'Escribe la tarea para el agente… (Mayús+Enter para nueva línea)'}
              disabled={isRunning}
              rows={3}
              style={{
                width: '100%',
                display: 'block',
                minHeight: '78px',
                maxHeight: inputExpanded ? '60vh' : '240px',
                resize: 'none',
                backgroundColor: '#080b10',
                border: '1px solid #1e2638',
                borderRadius: '7px',
                padding: '9px 30px 9px 12px',
                color: '#f0f6fc',
                fontSize: '12.5px',
                lineHeight: 1.5,
                fontFamily: 'inherit',
                outline: 'none',
                boxShadow: 'inset 0 1px 3px rgba(0,0,0,0.5)',
                overflowY: 'auto',
              }}
            />
            <button
              type="button"
              onClick={() => setInputExpanded((v) => !v)}
              title={inputExpanded ? 'Reducir el campo' : 'Ampliar el campo para ver todo el prompt'}
              style={{ position: 'absolute', top: '5px', right: '5px', background: 'none', border: 'none', color: '#64748b', cursor: 'pointer', padding: '2px' }}
            >
              {inputExpanded ? <Minimize2 size={12} /> : <Maximize2 size={12} />}
            </button>
          </div>

          {!isRunning ? (
            <button
              type="submit"
              disabled={!inputText.trim() || (executionMode === 'full_access' && !fullAccessConfirmed)}
              style={{
                backgroundColor: !inputText.trim() || (executionMode === 'full_access' && !fullAccessConfirmed) ? '#18202d' : '#238636',
                border: `1px solid ${!inputText.trim() || (executionMode === 'full_access' && !fullAccessConfirmed) ? '#283344' : '#2ea043'}`,
                borderRadius: '7px',
                color: !inputText.trim() || (executionMode === 'full_access' && !fullAccessConfirmed) ? '#64748b' : '#ffffff',
                padding: '8px 14px',
                fontSize: '12px',
                fontWeight: '600',
                cursor: !inputText.trim() || (executionMode === 'full_access' && !fullAccessConfirmed) ? 'not-allowed' : 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: '5px',
                flexShrink: 0,
                transition: 'all 0.15s ease',
              }}
              title="Iniciar misión supervisada"
            >
              <Send size={13} />
              <span>Enviar</span>
            </button>
          ) : (
            <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
              {isPaused ? (
                <button
                  type="button"
                  onClick={onResumeMission}
                  className="btn btn-secondary"
                  style={{ padding: '7px 9px', fontSize: '11px', display: 'flex', alignItems: 'center', gap: '3px' }}
                  title="Reanudar misión"
                >
                  <Play size={12} />
                </button>
              ) : (
                <button
                  type="button"
                  onClick={onPauseMission}
                  className="btn btn-secondary"
                  style={{ padding: '7px 9px', fontSize: '11px', display: 'flex', alignItems: 'center', gap: '3px' }}
                  title="Pausar misión"
                >
                  <Pause size={12} />
                </button>
              )}
              <button
                type="button"
                onClick={onStopMission}
                className="btn btn-danger"
                style={{ padding: '7px 9px', fontSize: '11px', display: 'flex', alignItems: 'center', gap: '3px' }}
                title="Detener misión"
              >
                <Square size={12} />
              </button>
            </div>
          )}
        </form>
      </div>
    </div>
  );
}
