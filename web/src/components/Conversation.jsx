import React, { useState, useEffect, useRef } from 'react';
import WorkspaceField from './workspace/WorkspaceField';
import { fetchWorkflows } from '../services/api';
import ModelPicker from './ModelPicker';
import ApprovalPopup from './ApprovalPopup';
import Markdown from './Markdown';
import { presentAnswer } from '../utils/answerText';
import { deriveChatState } from '../utils/chatFromEvents';
import { PROVIDERS, saveChoice, savedChoice } from '../utils/modelCatalog';
import { Maximize2, Minimize2, Play, Pause, Square, Settings, AlertTriangle, Copy, Check } from 'lucide-react';

/** Posición del último mensaje del asistente (-1 si no hay): los mensajes del operador pueden ir detrás. */
function lastAssistantIndex(messages) {
  for (let i = messages.length - 1; i >= 0; i--) if (messages[i].role === 'assistant') return i;
  return -1;
}

export default function Conversation({
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
  nodes = [],
  selectedNodeId = null,
  drawerTab = null,
  pendingApprovals = [],
  onSelectStep,
  onOpenDrawer,
  onApprove,
  onReject,
  onViewApproval,
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

  // Historial de mensajes persistente con fallback local
  const [localMessages, setLocalMessages] = useState([]);

  const messages = chatMessages !== undefined ? chatMessages : localMessages;
  const setMessages = React.useCallback((updater) => {
    if (onMessagesChange) {
      onMessagesChange(updater);
    }
    setLocalMessages(updater);
  }, [onMessagesChange]);

  const metrics = session.metrics || { totalDecisions: 0, allowed: 0, blocked: 0, review: 0 };
  const messagesEndRef = useRef(null);
  const scrollRef = useRef(null);
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
  // Solo se desplaza al final si el usuario ya estaba cerca de él (o acaba de enviar un mensaje)
  const stickRef = useRef(true);
  const scrollToBottom = () => {
    const el = scrollRef.current;
    if (!el) return;
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < 140;
    if (near || stickRef.current) {
      el.scrollTop = el.scrollHeight;
      stickRef.current = false;
    }
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
        // El mensaje del asistente en curso no tiene por qué ser el último: una aprobación o una intervención añade mensajes del operador detrás
        const idx = lastAssistantIndex(prev);
        const lastMsg = idx >= 0 ? prev[idx] : null;
        if (lastMsg && (lastMsg.status === 'running' || isRunning)) {
          return [
            ...prev.slice(0, idx),
            {
              ...lastMsg,
              thoughts: thoughts.length > 0 ? thoughts : lastMsg.thoughts,
              milestones: milestones.length > 0 ? milestones : lastMsg.milestones,
              activeNode: activeNode || null,
              currentThought: currentThoughtText || lastMsg.currentThought,
              status: session.status === 'Completed' || session.finalAnswer ? 'completed' : 'running',
              finalAnswer: session.finalAnswer || lastMsg.finalAnswer,
            },
            ...prev.slice(idx + 1),
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
          const idx = lastAssistantIndex(prev);
          const lastMsg = idx >= 0 ? prev[idx] : null;
          if (lastMsg && lastMsg.status !== 'completed') {
            return [
              ...prev.slice(0, idx),
              {
                ...lastMsg,
                status: 'completed',
                finalAnswer: session.finalAnswer || lastMsg.finalAnswer || 'Misión completada satisfactoriamente bajo la supervisión de PRAXEON.',
              },
              ...prev.slice(idx + 1),
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
      return; // el aviso de confirmación ya está visible bajo el campo de texto
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

    stickRef.current = true;
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


  // ---------- Presentación ----------
  const verdictInfo = (th, live) => {
    const v = String(th.verdict || '').toUpperCase();
    if (v === 'ALLOW') return { dot: 'dot', note: null };
    if (v === 'REVIEW') return { dot: 'dot review', note: 'Requiere tu revisión', tone: 'var(--accent)' };
    if (v === 'BLOCK' || v === 'BLOCKED') return { dot: 'dot bad', note: 'Bloqueado por política', tone: 'var(--bad)' };
    if (v && v !== 'PENDING' && v !== 'EVALUANDO') return { dot: 'dot idle', note: 'Replanificado por el supervisor', tone: 'var(--muted)' };
    return live ? { dot: 'dot run', note: null } : { dot: 'dot idle', note: null };
  };

  const stepLabel = (th) => String(th.operation || th.tool || 'acción').replace(/^\s*\d+\.\s*/, '');

  const resolveNodeId = (th) => {
    if (th.nodeId) return th.nodeId;
    const found = nodes.find((n) => typeof n.label === 'string' && n.label.startsWith(`${th.step}.`));
    return found ? found.id : null;
  };

  const renderStep = (th, idx, msg, live) => {
    const info = verdictInfo(th, live);
    const nodeId = resolveNodeId(th);
    const selected = nodeId && nodeId === selectedNodeId && drawerTab === 'step';
    const label = stepLabel(th);
    const isFallbackText = typeof th.thought === 'string' && th.thought.startsWith(`Paso ${th.step}: Proponiendo`);
    const meta = [];
    if (th.agentName) meta.push(th.agentName + (th.nodeName ? ` · ${th.nodeName}` : ''));
    if (th.score !== undefined) meta.push(`Supervisor ${th.score}`);
    if (th.executionTimeMs) meta.push(`${th.executionTimeMs} ms`);
    return (
      <React.Fragment key={`${msg.id}-${th.step}-${idx}`}>
        {th.thought && !isFallbackText && (
          <div className="rise" style={{ fontSize: 'var(--fs-chat)', lineHeight: 1.65, maxWidth: '64ch' }}>
            <Markdown text={th.thought} compact />
          </div>
        )}
        <button
          type="button"
          className="rise"
          onClick={() => onSelectStep?.(nodeId, th)}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '11px',
            width: 'min(100%, 540px)',
            padding: '9px 13px',
            border: `1px solid ${selected ? 'var(--muted)' : 'var(--line)'}`,
            background: selected ? 'var(--surface)' : 'transparent',
            borderRadius: 'var(--r-card)',
            textAlign: 'left',
            marginTop: '-6px',
          }}
          title="Ver el detalle de esta decisión"
        >
          <span className={info.dot} />
          <span style={{ flex: 1, minWidth: 0 }}>
            <span style={{ display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: info.dot.includes('bad') ? 'var(--muted)' : 'var(--text)' }}>
              {label}
              {info.dot === 'dot run' && <span style={{ color: 'var(--muted)' }}> · en curso</span>}
            </span>
            {info.note && <span style={{ display: 'block', fontSize: 'var(--fs-secondary)', color: info.tone }}>{info.note}{th.reason ? ` · ${th.reason}` : ''}</span>}
            {th.interventionMessage && <span style={{ display: 'block', fontSize: 'var(--fs-secondary)', color: 'var(--muted)' }}>{th.interventionMessage}</span>}
            {meta.length > 0 && <span style={{ display: 'block', fontSize: 'var(--fs-label)', color: 'var(--muted)' }}>{meta.join(' · ')}</span>}
          </span>
          {th.tool && th.tool !== label && (
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-code)', color: 'var(--muted)', flexShrink: 0 }}>{th.tool}</span>
          )}
          <span style={{ fontSize: 'var(--fs-secondary)', color: 'var(--muted)', opacity: selected ? 1 : 0.0, flexShrink: 0 }} className="step-go">Ver ›</span>
        </button>
      </React.Fragment>
    );
  };

  const renderMessage = (msg) => {
    if (msg.role === 'user' || msg.role === 'operator') {
      return (
        <div key={msg.id} className="rise" style={{ alignSelf: 'flex-end', maxWidth: '82%', display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '4px' }}>
          <div
            style={{
              background: 'var(--surface)',
              border: '1px solid var(--line)',
              borderRadius: '16px 16px 4px 16px',
              padding: '9px 15px',
              fontSize: 'var(--fs-ui)',
              whiteSpace: 'pre-wrap',
              overflowWrap: 'anywhere',
            }}
          >
            {msg.text}
          </div>
          <div style={{ fontSize: 'var(--fs-label)', color: 'var(--muted)' }}>
            {msg.role === 'operator' ? 'Operador' : 'Tú'} · {msg.time}
            {msg.config && (
              <>
                {' · '}
                {(PROVIDERS.find((p) => p.value === msg.config.llm_provider)?.label || String(msg.config.llm_provider || '').toUpperCase())}
                {' · '}
                {String(msg.config.supervisor || '').toUpperCase()}
                {' · '}
                {msg.config.execution_mode === 'full_access' ? 'Acceso directo al host' : 'Sandbox'}
              </>
            )}
          </div>
        </div>
      );
    }
    if (msg.isWelcome) return null;

    const isActivelyThinking = msg.status === 'running' || (isRunning && msg === messages[messages.length - 1]);
    const thoughtsList = msg.thoughts || [];
    const stepsNode = thoughtsList.map((th, idx) => renderStep(th, idx, msg, isActivelyThinking && idx === thoughtsList.length - 1));
    const milestonesNode = msg.milestones?.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', width: 'min(100%, 540px)' }}>
            <div style={{ fontSize: 'var(--fs-label)', color: 'var(--muted)', letterSpacing: '0.07em', textTransform: 'uppercase', marginBottom: '4px' }}>
              Hitos del flujo
            </div>
            {msg.milestones.map((m) => (
              <details key={m.key} style={{ borderTop: '1px solid var(--line)', padding: '8px 0' }}>
                <summary style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap', listStyle: 'none' }}>
                  <span className="dot" />
                  <span>{m.nodeName}</span>
                  <span style={{ color: 'var(--muted)', fontSize: 'var(--fs-secondary)' }}>
                    {m.agentName ? `· ${m.agentName}` : ''}
                    {m.agentRole ? ` (${m.agentRole})` : ''}
                    {m.iteration > 1 ? ` · pasada ${m.iteration}` : ''}
                  </span>
                </summary>
                <div style={{ marginTop: '8px', fontSize: 'var(--fs-secondary)', color: 'var(--muted)', lineHeight: 1.5, maxHeight: '220px', overflowY: 'auto' }}>
                  <Markdown text={presentAnswer(m.summary).text} compact />
                </div>
              </details>
            ))}
          </div>
    );
    // Con la respuesta final entregada, el razonamiento se pliega en un único bloque desplegable
    const collapsed = Boolean(msg.finalAnswer) && !isActivelyThinking && (thoughtsList.length > 0 || msg.milestones?.length > 0);
    return (
      <React.Fragment key={msg.id}>
        {!collapsed && stepsNode}

        {isActivelyThinking && thoughtsList.length === 0 && (
          <div className="rise" style={{ display: 'flex', alignItems: 'center', gap: '9px', color: 'var(--muted)', fontSize: 'var(--fs-chat)' }}>
            <span className="pulse-dot" />
            <span>{msg.currentThought || 'Procesando la misión…'}</span>
          </div>
        )}

        {msg.activeNode && msg.status !== 'completed' && (
          <div className="rise" style={{ display: 'flex', alignItems: 'center', gap: '9px', color: 'var(--muted)', fontSize: 'var(--fs-secondary)' }}>
            <span className="pulse-dot" />
            <span>
              <strong style={{ color: 'var(--text)', fontWeight: 500 }}>{msg.activeNode.agent_name}</strong>
              {msg.activeNode.handoff_from ? ` recibe la tarea de ${msg.activeNode.handoff_from}` : ' toma el control'}
              {' · '}«{msg.activeNode.node_name || msg.activeNode.node_id}»
              {msg.activeNode.iteration > 1 ? ` (pasada ${msg.activeNode.iteration})` : ''}
              {msg.activeNode.retry_reason ? ' · reintento automático tras una interrupción' : ''}
            </span>
          </div>
        )}

        {!collapsed && milestonesNode}

        {collapsed && (
          <details className="rise" style={{ width: 'min(100%, 540px)', border: '1px solid var(--line)', borderRadius: 'var(--r-card)', padding: '9px 13px' }}>
            <summary style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '10px', listStyle: 'none', color: 'var(--muted)', fontSize: 'var(--fs-secondary)' }}>
              <span className="dot idle" />
              <span>Razonamiento · {thoughtsList.length} {thoughtsList.length === 1 ? 'paso' : 'pasos'}{msg.milestones?.length > 0 ? ` · ${msg.milestones.length} ${msg.milestones.length === 1 ? 'hito' : 'hitos'}` : ''}{metrics.blocked > 0 ? ` · ${metrics.blocked} bloqueado${metrics.blocked === 1 ? '' : 's'}` : ''}</span>
              <span style={{ marginLeft: 'auto' }}>Ver paso a paso</span>
            </summary>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '14px', marginTop: '14px' }}>
              {stepsNode}
              {milestonesNode}
            </div>
          </details>
        )}

        {msg.finalAnswer && (
          <div className="rise" style={{ display: 'flex', flexDirection: 'column', gap: '8px', maxWidth: '64ch' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px', color: 'var(--muted)', fontSize: 'var(--fs-label)', letterSpacing: '0.07em', textTransform: 'uppercase' }}>
              <span>Respuesta de la misión</span>
              <button
                type="button"
                onClick={() => handleCopyAnswer(presentAnswer(msg.finalAnswer).text)}
                style={{ background: 'none', border: 0, color: 'var(--muted)', display: 'inline-flex', alignItems: 'center', gap: '4px', textTransform: 'none', letterSpacing: 0, fontSize: 'var(--fs-secondary)' }}
                title="Copiar la respuesta"
              >
                {copied ? <Check size={12} /> : <Copy size={12} />}
                <span>{copied ? 'Copiado' : 'Copiar'}</span>
              </button>
            </div>
            {(() => {
              const { text, verdict } = presentAnswer(msg.finalAnswer);
              return (
                <>
                  {verdict && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: 'var(--fs-secondary)', color: verdict === 'approved' ? 'var(--ok)' : 'var(--bad)' }}>
                      <span className={verdict === 'approved' ? 'dot' : 'dot bad'} />
                      <span>{verdict === 'approved' ? 'Veredicto: aprobado' : 'Veredicto: rechazado'}</span>
                    </div>
                  )}
                  <div style={{ fontSize: 'var(--fs-chat)', lineHeight: 1.65 }}><Markdown text={text} /></div>
                </>
              );
            })()}
          </div>
        )}

        {msg.status === 'completed' && (
          <div className="rise" style={{ fontSize: 'var(--fs-secondary)', color: 'var(--muted)', display: 'flex', gap: '10px', flexWrap: 'wrap', alignItems: 'center' }}>
            <span>{thoughtsList.length} {thoughtsList.length === 1 ? 'paso' : 'pasos'}</span>
            {metrics.blocked > 0 && <><span>·</span><span>{metrics.blocked} bloqueado{metrics.blocked === 1 ? '' : 's'}</span></>}
            {metrics.review > 0 && <><span>·</span><span>{metrics.review} en revisión</span></>}
            <span>·</span>
            <button type="button" onClick={() => onOpenDrawer?.('events')} style={{ background: 'none', border: 0, color: 'var(--muted)', textDecoration: 'underline', textUnderlineOffset: '3px' }}>
              {events.length} eventos
            </button>
          </div>
        )}
      </React.Fragment>
    );
  };

  const fullAccessBlocked = executionMode === 'full_access' && !fullAccessConfirmed;
  const sendDisabled = !inputText.trim() || fullAccessBlocked;
  const selectStyle = { background: 'transparent', border: 0, color: 'var(--muted)', padding: '3px 2px', fontSize: 'var(--fs-secondary)', maxWidth: '140px', minWidth: 0, textOverflow: 'ellipsis', cursor: isRunning ? 'not-allowed' : 'pointer' };
  const fieldLabel = { fontSize: 'var(--fs-secondary)', color: 'var(--muted)', display: 'block', marginBottom: '4px' };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minWidth: 0, overflow: 'hidden' }}>
      <div ref={scrollRef} role="log" aria-live="polite" aria-label="Conversación con el agente" style={{ flex: 1, overflowY: 'auto' }}>
        <div
          style={{
            width: 'min(720px, 100%)',
            margin: '0 auto',
            padding: `30px 24px ${pendingApprovals.length > 0 ? '130px' : '20px'}`,
            display: 'flex',
            flexDirection: 'column',
            gap: '18px',
          }}
        >
          {messages.filter((m) => !m.isWelcome).length === 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', maxWidth: '56ch', marginTop: '8vh' }}>
              <h1 style={{ fontSize: 'var(--fs-title)', fontWeight: 600, letterSpacing: '-0.01em' }}>Dale una misión al agente</h1>
              <p style={{ color: 'var(--muted)', fontSize: 'var(--fs-chat)', lineHeight: 1.65 }}>
                Cada acción que proponga pasa por la supervisión de PRAXEON antes de ejecutarse. Verás aquí su razonamiento paso a paso,
                y el detalle de cada decisión está a un clic.
              </p>
            </div>
          )}
          {messages.map(renderMessage)}
          <div ref={messagesEndRef} />
        </div>
      </div>

      <div style={{ padding: '0 24px 20px', position: 'relative', flex: 'none' }}>
        <ApprovalPopup
          approvals={pendingApprovals}
          onApprove={onApprove}
          onReject={onReject}
          onViewDetail={onViewApproval}
        />

        <form
          onSubmit={handleSend}
          style={{
            width: 'min(720px, 100%)',
            margin: '0 auto',
            background: 'var(--surface)',
            border: '1px solid var(--line)',
            borderRadius: 'var(--r-composer)',
            padding: '13px 13px 10px 18px',
            display: 'flex',
            flexDirection: 'column',
            gap: '8px',
          }}
        >
          {showAdvanced && (
            <div
              style={{
                borderBottom: '1px solid var(--line)',
                paddingBottom: '12px',
                marginRight: '5px',
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fit, minmax(210px, 1fr))',
                gap: '12px',
              }}
            >
              <div style={{ gridColumn: '1 / -1' }}>
                <span style={fieldLabel}>Carpeta de trabajo de esta misión</span>
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
                <label htmlFor="mc-api-key" style={fieldLabel}>Clave de API o token</label>
                <input
                  id="mc-api-key"
                  type="password"
                  value={apiKey}
                  onChange={(e) => updateConfig({ apiKey: e.target.value })}
                  placeholder="gsk_… / sk-…"
                  disabled={isRunning}
                  autoComplete="off"
                  style={{ width: '100%' }}
                />
              </div>
              <div>
                <label htmlFor="mc-base-url" style={fieldLabel}>URL base (opcional)</label>
                <input
                  id="mc-base-url"
                  type="text"
                  value={baseUrl}
                  onChange={(e) => updateConfig({ baseUrl: e.target.value })}
                  placeholder="http://localhost:11434"
                  disabled={isRunning}
                  style={{ width: '100%' }}
                />
              </div>
              <div>
                <span style={{ ...fieldLabel, display: 'flex', justifyContent: 'space-between' }}>
                  <span>Límite de pasos</span>
                  <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text)' }}>{Number(maxSteps) === 0 ? 'sin límite' : `${maxSteps} pasos`}</span>
                </span>
                <div style={{ display: 'flex', gap: '6px', alignItems: 'center' }}>
                  <input
                    type="number"
                    min={1}
                    max={500}
                    value={Number(maxSteps) === 0 ? '' : maxSteps}
                    placeholder="∞"
                    aria-label="Límite de pasos"
                    onChange={(e) => updateConfig({ maxSteps: Math.max(1, Math.min(500, Number(e.target.value) || 1)) })}
                    disabled={isRunning}
                    style={{ width: '72px', textAlign: 'center', fontFamily: 'var(--font-mono)' }}
                  />
                  <div style={{ display: 'flex', gap: '4px', flex: 1 }}>
                    {[10, 25, 50, 100, 0].map((preset) => (
                      <button
                        key={preset}
                        type="button"
                        onClick={() => updateConfig({ maxSteps: preset })}
                        disabled={isRunning}
                        className="btn"
                        style={{
                          flex: 1,
                          padding: '4px 0',
                          fontSize: 'var(--fs-secondary)',
                          color: maxSteps === preset ? 'var(--text)' : 'var(--muted)',
                          borderColor: maxSteps === preset ? 'var(--accent)' : 'var(--line)',
                        }}
                      >
                        {preset === 0 ? '∞' : preset}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
              {activeWorkflows.length === 0 && (
                <div style={{ gridColumn: '1 / -1', fontSize: 'var(--fs-secondary)', color: 'var(--muted)' }}>
                  No hay flujos activos: actívalos en la vista Flujos para usarlos aquí.
                </div>
              )}
            </div>
          )}

          {fullAccessBlocked || executionMode === 'full_access' ? (
            <div role="alert" style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap', color: 'var(--bad)', fontSize: 'var(--fs-secondary)' }}>
              <AlertTriangle size={14} style={{ flexShrink: 0 }} />
              <span style={{ fontWeight: 600 }}>Acceso completo: sin aislamiento del sistema operativo.</span>
              <label style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', color: 'var(--text)', cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={fullAccessConfirmed}
                  onChange={(e) => updateConfig({ fullAccessConfirmed: e.target.checked })}
                  disabled={isRunning}
                />
                <span>Confirmo la ejecución directa sobre el host</span>
              </label>
            </div>
          ) : null}

          <textarea
            ref={inputRef}
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            onKeyDown={(e) => {
              // Enter envía; Mayús+Enter inserta un salto de línea
              if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                if (!sendDisabled && !isRunning) handleSend(e);
              }
            }}
            placeholder={isRunning ? 'Misión en curso… puedes pausarla o detenerla abajo.' : 'Escribe un mensaje o da una nueva misión…'}
            aria-label="Mensaje para el agente"
            disabled={isRunning}
            rows={1}
            style={{
              width: '100%',
              display: 'block',
              resize: 'none',
              border: 0,
              background: 'transparent',
              padding: '2px 4px 2px 0',
              outline: 'none',
              fontSize: 'var(--fs-chat)',
              lineHeight: 1.5,
              minHeight: '26px',
              maxHeight: inputExpanded ? '60vh' : '140px',
              overflowY: 'auto',
            }}
          />

          <div style={{ display: 'flex', alignItems: 'center', gap: '6px', color: 'var(--muted)' }}>
            <select
              aria-label="Proveedor del modelo"
              value={llmProvider}
              onChange={(e) => updateConfig({ llmProvider: e.target.value, customModel: savedChoice(e.target.value) })}
              disabled={isRunning}
              style={selectStyle}
              title="Proveedor del modelo (el modelo exacto se elige en los ajustes de la misión)"
            >
              {PROVIDERS.map((p) => (
                <option key={p.value} value={p.value}>{p.label}</option>
              ))}
            </select>
            <select
              aria-label="Supervisor"
              value={supervisor}
              onChange={(e) => updateConfig({ supervisor: e.target.value })}
              disabled={isRunning}
              style={selectStyle}
              title="Supervisor cognitivo del runtime"
            >
              <option value="laya">LAYA (sistema 1)</option>
              <option value="kev">KEV (sistema 1)</option>
              <option value="typesafe">TypeSafe AI (AST)</option>
              <option value="cascade">Cascada (configurable en Proveedores)</option>
            </select>
            <select
              aria-label="Modo de ejecución"
              value={executionMode}
              onChange={(e) => {
                const val = e.target.value;
                updateConfig({
                  executionMode: val,
                  ...(val !== 'full_access' ? { fullAccessConfirmed: false } : {}),
                });
              }}
              disabled={isRunning}
              style={{ ...selectStyle, color: executionMode === 'full_access' ? 'var(--bad)' : 'var(--muted)' }}
              title="Entorno y modo de contención"
            >
              <option value="local_restricted">Local restringido</option>
              <option value="container">Contenedor (Docker)</option>
              <option value="full_access">Acceso completo ⚠</option>
            </select>
            <select
              aria-label="Flujo que gobierna la tarea"
              value={workflowId}
              onChange={(e) => updateConfig({ workflowId: e.target.value })}
              onFocus={loadActiveWorkflows}
              disabled={isRunning}
              title="Con un flujo activo, la tarea sigue sus nodos, agentes y bucles (se ven en la vista Flujos)"
              style={{ ...selectStyle, color: workflowId ? 'var(--text)' : 'var(--muted)' }}
            >
              <option value="">Sin flujo</option>
              {activeWorkflows.map((w) => (
                <option key={w.workflow_id} value={w.workflow_id}>{w.name}</option>
              ))}
            </select>
            <button
              type="button"
              onClick={() => setShowAdvanced(!showAdvanced)}
              className="btn-icon"
              style={{ width: '28px', height: '28px', color: showAdvanced ? 'var(--text)' : undefined }}
              aria-expanded={showAdvanced}
              title="Ajustes de la misión: carpeta, modelo, clave de API y pasos máximos"
              aria-label="Ajustes de la misión"
            >
              <Settings size={14} strokeWidth={1.6} />
            </button>
            <button
              type="button"
              onClick={() => setInputExpanded((v) => !v)}
              className="btn-icon"
              style={{ width: '28px', height: '28px' }}
              title={inputExpanded ? 'Reducir el campo' : 'Ampliar el campo para ver todo el mensaje'}
              aria-label={inputExpanded ? 'Reducir el campo de texto' : 'Ampliar el campo de texto'}
            >
              {inputExpanded ? <Minimize2 size={13} /> : <Maximize2 size={13} />}
            </button>

            <span style={{ flex: 1 }} />

            {!isRunning ? (
              <button type="submit" className="btn btn-primary" disabled={sendDisabled} style={{ borderRadius: 'var(--r-card)' }} title="Iniciar misión supervisada">
                Enviar
              </button>
            ) : (
              <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                {isPaused ? (
                  <button type="button" onClick={onResumeMission} className="btn btn-secondary" title="Reanudar la misión">
                    <Play size={13} /> Reanudar
                  </button>
                ) : (
                  <button type="button" onClick={onPauseMission} className="btn btn-secondary" title="Pausar la misión">
                    <Pause size={13} /> Pausar
                  </button>
                )}
                <button type="button" onClick={onStopMission} className="btn btn-danger" title="Detener la misión">
                  <Square size={12} /> Detener
                </button>
              </div>
            )}
          </div>
        </form>
      </div>
    </div>
  );
}
