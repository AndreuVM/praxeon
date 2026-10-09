import React, { useState, useEffect } from 'react';
import { Play, Pause, Square, Sparkles, Terminal, Cpu, Shield, RefreshCw, AlertTriangle, Activity, Settings2 } from 'lucide-react';
import { fetchProviders } from '../services/api';

export default function MissionLauncher({
  isRunning = false,
  isPaused = false,
  onStartMission,
  onPauseMission,
  onResumeMission,
  onStopMission,
  onLoadDemo,
}) {
  const [goal, setGoal] = useState('Fix authentication bug in the API');
  const [executionMode, setExecutionMode] = useState('local_restricted');
  const [fullAccessConfirmed, setFullAccessConfirmed] = useState(false);

  // LLM Generator state
  const [llmProvider, setLlmProvider] = useState('simulator');
  const [llmModel, setLlmModel] = useState('');
  const [customModel, setCustomModel] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [llmFailurePolicy, setLlmFailurePolicy] = useState('synthetic_fallback');

  // System-1 Decision Provider state
  const [decisionProvider, setDecisionProvider] = useState('laya');
  const [decisionBackend, setDecisionBackend] = useState('local');
  const [decisionDevice, setDecisionDevice] = useState('auto');
  const [decisionFallback, setDecisionFallback] = useState('mock');
  const [customDecisionModel, setCustomDecisionModel] = useState('');

  // General state
  const [maxSteps, setMaxSteps] = useState(25);
  const [workspaceRoot, setWorkspaceRoot] = useState('');
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [isExpanded, setIsExpanded] = useState(true);

  // Dynamic Providers Catalog loaded from GET /v1/providers
  const [providersCatalog, setProvidersCatalog] = useState({
    decision_providers: [
      {
        provider_id: 'laya',
        name: 'LAYA System-1',
        model_id: 'laya-v1',
        version: '0.3.0',
        backend: 'local',
        available: true,
        average_latency_display: 'N/A',
        fallbacks: ['mock', 'replay'],
      },
      {
        provider_id: 'typesafe',
        name: 'TypeSafe AI (Legacy)',
        model_id: 'typesafe-v1',
        version: '0.7.0',
        backend: 'api',
        available: true,
        average_latency_display: 'N/A',
        fallbacks: ['mock', 'replay'],
      },
      {
        provider_id: 'replay',
        name: 'Replay Provider',
        model_id: 'replay-v1',
        version: '1.0.0',
        backend: 'local',
        available: true,
        average_latency_display: 'N/A',
        fallbacks: ['mock'],
      },
      {
        provider_id: 'mock',
        name: 'Mock Provider',
        model_id: 'mock-v1',
        version: '1.0.0',
        backend: 'local',
        available: true,
        average_latency_display: 'N/A',
        fallbacks: [],
      },
    ],
    llm_providers: [
      { provider_id: 'simulator', name: 'Simulated Agent (Offline)', default_model: 'simulator-agent' },
      { provider_id: 'groq', name: 'Groq Cloud', default_model: 'llama-3.3-70b-versatile' },
      { provider_id: 'ollama', name: 'Ollama Local', default_model: 'qwen2.5-coder:7b' },
      { provider_id: 'gemini', name: 'Google Gemini', default_model: 'gemini-1.5-flash' },
      { provider_id: 'openai', name: 'OpenAI', default_model: 'gpt-4o-mini' },
      { provider_id: 'openrouter', name: 'OpenRouter', default_model: 'anthropic/claude-3.5-sonnet' },
    ],
  });

  useEffect(() => {
    let isMounted = true;
    async function loadCatalog() {
      try {
        const resp = await fetchProviders();
        if (isMounted && resp?.data) {
          setProvidersCatalog({
            decision_providers: resp.data.decision_providers || [],
            llm_providers: resp.data.llm_providers || [],
          });
        }
      } catch (e) {
        console.warn('Could not load dynamic provider catalog:', e);
      }
    }
    loadCatalog();
    return () => {
      isMounted = false;
    };
  }, []);

  const presetGoals = [
    'Fix authentication bug in the API',
    'Auditar y ejecutar suite de tests con pytest',
    'Inspeccionar contención de red y variables de entorno',
  ];

  const selectedDecisionMeta =
    providersCatalog.decision_providers.find((p) => p.provider_id === decisionProvider) || {};

  const handleSubmit = (e) => {
    e?.preventDefault();
    if (!goal.trim()) return;

    const isFullAccess = executionMode.startsWith('full_access');
    const isAutonomous = executionMode === 'full_access_autonomous';

    if (isFullAccess && !fullAccessConfirmed) {
      alert('Debes confirmar que comprendes que el aislamiento de procesos del SO está deshabilitado para usar Full Access.');
      return;
    }

    onStartMission?.({
      goal: goal.trim(),
      workspace_root: workspaceRoot.trim() || undefined,
      execution_mode: isFullAccess ? 'full_access' : executionMode,
      autonomous: isAutonomous,
      allow_unattended_execution: isAutonomous,
      confirmation_required_for_full_access: isFullAccess && !isAutonomous,
      llm_provider: llmProvider,
      llm_model: customModel.trim() || llmModel || undefined,
      api_key: apiKey.trim() || undefined,
      base_url: baseUrl.trim() || undefined,
      decision_model: {
        provider: decisionProvider,
        model_id: customDecisionModel.trim() || selectedDecisionMeta.model_id || `${decisionProvider}-v1`,
        backend: decisionBackend,
        device: decisionDevice,
        fallback_policy: decisionFallback,
      },
      supervisor: decisionProvider,
      max_steps: Number(maxSteps),
      llm_failure_policy: llmFailurePolicy,
    });
  };

  return (
    <div
      style={{
        backgroundColor: '#0f131c',
        borderBottom: '1px solid #1a202c',
        padding: '12px 20px',
        userSelect: 'none',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: isExpanded ? '10px' : '0' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Sparkles size={14} style={{ color: '#f0f6fc' }} />
          <span style={{ fontSize: '12.5px', fontWeight: '700', color: '#f0f6fc', letterSpacing: '0.02em' }}>
            Control de Misión Interactiva
          </span>
          <span
            style={{
              fontSize: '10.5px',
              padding: '1px 7px',
              borderRadius: '9999px',
              backgroundColor: isRunning ? 'rgba(63, 185, 80, 0.15)' : 'rgba(139, 148, 158, 0.15)',
              color: isRunning ? '#3fb950' : '#8b949e',
              border: `1px solid ${isRunning ? 'rgba(63, 185, 80, 0.3)' : '#242c3b'}`,
              fontWeight: '600',
            }}
          >
            {isRunning ? (isPaused ? 'Pausada' : 'En Ejecución') : 'Listo'}
          </span>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <button
            type="button"
            onClick={onLoadDemo}
            className="btn btn-secondary"
            style={{ fontSize: '11px', padding: '4px 9px' }}
            title="Cargar árbol de sesión de demostración visual"
          >
            <RefreshCw size={11} />
            Cargar Demo Visual
          </button>
          <button
            type="button"
            onClick={() => setIsExpanded(!isExpanded)}
            style={{
              background: 'none',
              border: 'none',
              color: '#8b949e',
              fontSize: '11px',
              cursor: 'pointer',
              padding: '4px 6px',
            }}
          >
            {isExpanded ? 'Ocultar panel ▲' : 'Configurar misión ▼'}
          </button>
        </div>
      </div>

      {isExpanded && (
        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
          {/* Prompt / Goal Input with Preset Chips */}
          <div>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '5px' }}>
              <label style={{ fontSize: '11px', color: '#8b949e', fontWeight: '500' }}>
                Prompt / Objetivo de la Misión:
              </label>
              <div style={{ display: 'flex', gap: '6px' }}>
                {presetGoals.map((p, idx) => (
                  <button
                    key={idx}
                    type="button"
                    onClick={() => setGoal(p)}
                    style={{
                      background: '#141924',
                      border: '1px solid #1e2636',
                      borderRadius: '4px',
                      color: goal === p ? '#f0f6fc' : '#8b949e',
                      fontSize: '10px',
                      padding: '2px 6px',
                      cursor: 'pointer',
                    }}
                  >
                    {p.length > 28 ? p.slice(0, 26) + '...' : p}
                  </button>
                ))}
              </div>
            </div>

            <div style={{ display: 'flex', gap: '8px' }}>
              <input
                type="text"
                value={goal}
                onChange={(e) => setGoal(e.target.value)}
                placeholder="Introduce la tarea del agente (ej. Analizar vulnerabilidades y ejecutar tests)..."
                disabled={isRunning}
                style={{
                  flex: 1,
                  backgroundColor: '#0c0f14',
                  border: '1px solid #202737',
                  borderRadius: '6px',
                  padding: '7px 12px',
                  color: '#f0f6fc',
                  fontSize: '12px',
                  fontFamily: 'var(--font-sans)',
                  outline: 'none',
                  boxShadow: 'inset 0 1px 2px rgba(0,0,0,0.5)',
                }}
              />
            </div>
          </div>

          {/* Dual Desacoplado: LLM Generador + System-1 Supervisor Selectors */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
            {/* 1. LLM Generator Provider */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <Cpu size={13} style={{ color: '#58a6ff' }} />
              <label style={{ fontSize: '11px', color: '#8b949e', fontWeight: '500' }}>Generador LLM:</label>
              <select
                value={llmProvider}
                onChange={(e) => {
                  setLlmProvider(e.target.value);
                  const found = providersCatalog.llm_providers.find((l) => l.provider_id === e.target.value);
                  if (found) setLlmModel(found.default_model);
                }}
                disabled={isRunning}
                style={{
                  backgroundColor: '#0c0f14',
                  border: '1px solid #202737',
                  borderRadius: '6px',
                  padding: '5px 8px',
                  color: '#f0f6fc',
                  fontSize: '11.5px',
                  cursor: 'pointer',
                  outline: 'none',
                }}
              >
                {providersCatalog.llm_providers.map((lp) => (
                  <option key={lp.provider_id} value={lp.provider_id}>
                    {lp.name}
                  </option>
                ))}
              </select>
            </div>

            {/* 2. System-1 Decision Provider (Supervisor) */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <Shield size={13} style={{ color: '#c084fc' }} />
              <label style={{ fontSize: '11px', color: '#8b949e', fontWeight: '500' }}>Supervisor System-1:</label>
              <select
                value={decisionProvider}
                onChange={(e) => {
                  setDecisionProvider(e.target.value);
                  const p = providersCatalog.decision_providers.find((item) => item.provider_id === e.target.value);
                  if (p?.backend) setDecisionBackend(p.backend);
                }}
                disabled={isRunning}
                style={{
                  backgroundColor: '#0c0f14',
                  border: '1px solid #202737',
                  borderRadius: '6px',
                  padding: '5px 8px',
                  color: '#f0f6fc',
                  fontSize: '11.5px',
                  cursor: 'pointer',
                  outline: 'none',
                }}
              >
                {providersCatalog.decision_providers.map((dp) => (
                  <option key={dp.provider_id} value={dp.provider_id}>
                    {dp.name} {dp.version ? `(v${dp.version})` : ''}
                  </option>
                ))}
              </select>

              {/* Observed Latency Badge (Real Telemetry / "N/A") */}
              <span
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: '4px',
                  fontSize: '10px',
                  padding: '2px 6px',
                  borderRadius: '4px',
                  backgroundColor: 'rgba(192, 132, 252, 0.12)',
                  color: '#c084fc',
                  border: '1px solid rgba(192, 132, 252, 0.25)',
                }}
                title="Latencia media observada en evaluaciones reales (N/A si no se ha medido aún)"
              >
                <Activity size={10} />
                <span>{selectedDecisionMeta.average_latency_display || 'N/A'}</span>
              </span>
            </div>

            {/* 3. Execution Environment & Full Access Selector */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <Terminal
                size={13}
                style={{
                  color:
                    executionMode === 'full_access_autonomous'
                      ? '#f87171'
                      : executionMode === 'full_access_manual'
                      ? '#fbbf24'
                      : '#8b949e',
                }}
              />
              <label style={{ fontSize: '11px', color: '#8b949e', fontWeight: '500' }}>Entorno:</label>
              <select
                value={executionMode}
                onChange={(e) => {
                  setExecutionMode(e.target.value);
                  if (!e.target.value.startsWith('full_access')) setFullAccessConfirmed(false);
                }}
                disabled={isRunning}
                style={{
                  backgroundColor: '#0c0f14',
                  border: `1px solid ${
                    executionMode === 'full_access_autonomous'
                      ? 'rgba(239, 68, 68, 0.5)'
                      : executionMode === 'full_access_manual'
                      ? 'rgba(245, 158, 11, 0.5)'
                      : '#202737'
                  }`,
                  borderRadius: '6px',
                  padding: '5px 8px',
                  color:
                    executionMode === 'full_access_autonomous'
                      ? '#f87171'
                      : executionMode === 'full_access_manual'
                      ? '#fbbf24'
                      : '#f0f6fc',
                  fontSize: '11.5px',
                  cursor: 'pointer',
                  outline: 'none',
                  fontWeight: executionMode.startsWith('full_access') ? '600' : 'normal',
                }}
              >
                <option value="local_restricted">Local Restricted Sandbox (Defecto)</option>
                <option value="container">Container Sandbox (Docker)</option>
                <option value="full_access_manual">Full Access (Host Direct - MANUAL) ⚠</option>
                <option value="full_access_autonomous">Full Access (Host Direct - AUTONOMOUS) ⚡</option>
              </select>
            </div>

            {/* 4. Steps limit */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <label style={{ fontSize: '11px', color: '#8b949e', fontWeight: '500' }}>Pasos:</label>
              <select
                value={maxSteps}
                onChange={(e) => setMaxSteps(e.target.value)}
                disabled={isRunning}
                style={{
                  backgroundColor: '#0c0f14',
                  border: '1px solid #202737',
                  borderRadius: '6px',
                  padding: '5px 8px',
                  color: '#f0f6fc',
                  fontSize: '11.5px',
                  cursor: 'pointer',
                  outline: 'none',
                }}
              >
                <option value={10}>10 pasos</option>
                <option value={20}>20 pasos</option>
                <option value={25}>25 pasos</option>
                <option value={35}>35 pasos</option>
                <option value={50}>50 pasos</option>
                <option value={100}>100 pasos</option>
              </select>
            </div>

            {/* Advanced Settings Toggle */}
            <button
              type="button"
              onClick={() => setShowAdvanced(!showAdvanced)}
              style={{
                background: 'none',
                border: 'none',
                color: showAdvanced ? '#58a6ff' : '#8b949e',
                fontSize: '11px',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: '4px',
                padding: '4px 6px',
              }}
            >
              <Settings2 size={12} />
              <span>{showAdvanced ? 'Ocultar ajustes avanzados' : 'Ajustes avanzados (Backend / Fallbacks)'}</span>
            </button>

            {/* Primary Action Button */}
            <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: '8px' }}>
              {!isRunning ? (
                <button
                  type="submit"
                  disabled={executionMode.startsWith('full_access') && !fullAccessConfirmed}
                  className="btn btn-primary"
                  style={{
                    padding: '6px 14px',
                    fontSize: '12px',
                    backgroundColor:
                      executionMode.startsWith('full_access') && !fullAccessConfirmed ? '#3b1c1c' : '#202837',
                    borderColor:
                      executionMode.startsWith('full_access') && !fullAccessConfirmed ? '#5c2222' : '#384558',
                    cursor: executionMode.startsWith('full_access') && !fullAccessConfirmed ? 'not-allowed' : 'pointer',
                    opacity: executionMode.startsWith('full_access') && !fullAccessConfirmed ? 0.6 : 1,
                  }}
                >
                  <Play size={13} style={{ fill: '#f0f6fc' }} />
                  Iniciar Misión en Tiempo Real
                </button>
              ) : (
                <>
                  {isPaused ? (
                    <button
                      type="button"
                      onClick={onResumeMission}
                      className="btn btn-secondary"
                      style={{ padding: '6px 12px' }}
                    >
                      <Play size={12} /> Reanudar
                    </button>
                  ) : (
                    <button
                      type="button"
                      onClick={onPauseMission}
                      className="btn btn-secondary"
                      style={{ padding: '6px 12px' }}
                    >
                      <Pause size={12} /> Pausar
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={onStopMission}
                    className="btn btn-danger"
                    style={{ padding: '6px 12px' }}
                  >
                    <Square size={12} /> Detener
                  </button>
                </>
              )}
            </div>
          </div>

          {/* Full Access Warning Banner & Confirmation */}
          {executionMode.startsWith('full_access') && (
            <div
              style={{
                backgroundColor:
                  executionMode === 'full_access_autonomous'
                    ? 'rgba(239, 68, 68, 0.09)'
                    : 'rgba(245, 158, 11, 0.08)',
                border: `1px solid ${
                  executionMode === 'full_access_autonomous'
                    ? 'rgba(239, 68, 68, 0.38)'
                    : 'rgba(245, 158, 11, 0.35)'
                }`,
                borderRadius: '6px',
                padding: '10px 14px',
                display: 'flex',
                flexDirection: 'column',
                gap: '6px',
              }}
            >
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                  color: executionMode === 'full_access_autonomous' ? '#f87171' : '#fbbf24',
                  fontSize: '12px',
                  fontWeight: '700',
                }}
              >
                <AlertTriangle size={15} />
                <span>
                  ADVERTENCIA DE SEGURIDAD:{' '}
                  {executionMode === 'full_access_autonomous'
                    ? 'Ejecución Host Direct Autónomo (Sin confirmación por paso)'
                    : 'Ejecución Host Direct Manual (Confirmación obligatoria por paso)'}
                </span>
              </div>
              <p style={{ margin: 0, fontSize: '11px', color: '#cbd5e1', lineHeight: '1.45' }}>
                En este modo el agente opera directamente sobre el sistema operativo anfitrión sin contenedor ni chroot.
                {executionMode === 'full_access_autonomous' ? (
                  <span>
                    {' '}
                    El modo <strong>AUTÓNOMO</strong> ejecutará acciones aprobadas por política sin solicitar confirmación humana interactiva.
                  </span>
                ) : (
                  <span>
                    {' '}
                    El modo <strong>MANUAL</strong> detendrá toda acción en estado REVIEW para requerir la aprobación criptográfica de un operador.
                  </span>
                )}
              </p>
              <label
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                  fontSize: '11.5px',
                  color: executionMode === 'full_access_autonomous' ? '#fca5a5' : '#fde68a',
                  cursor: 'pointer',
                  marginTop: '4px',
                }}
              >
                <input
                  type="checkbox"
                  checked={fullAccessConfirmed}
                  onChange={(e) => setFullAccessConfirmed(e.target.checked)}
                  style={{ cursor: 'pointer', width: '14px', height: '14px' }}
                />
                <span style={{ fontWeight: '600' }}>
                  Entiendo y autorizo la ejecución directa sobre el sistema operativo anfitrión.
                </span>
              </label>
            </div>
          )}

          {/* Advanced Configuration Panel: System-1 Details & LLM Endpoints */}
          {showAdvanced && (
            <div
              style={{
                padding: '10px 14px',
                borderRadius: '6px',
                backgroundColor: '#121620',
                border: '1px solid #1a2232',
                fontSize: '11px',
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
                gap: '12px',
              }}
            >
              {/* System-1 Backend */}
              <div>
                <label style={{ display: 'block', fontSize: '10.5px', color: '#8b949e', marginBottom: '3px' }}>
                  Backend System-1 ({decisionProvider.toUpperCase()}):
                </label>
                <select
                  value={decisionBackend}
                  onChange={(e) => setDecisionBackend(e.target.value)}
                  style={{
                    width: '100%',
                    backgroundColor: '#0c0f14',
                    border: '1px solid #202737',
                    borderRadius: '4px',
                    padding: '4px 8px',
                    color: '#f0f6fc',
                    fontSize: '11px',
                    outline: 'none',
                  }}
                >
                  <option value="local">Local (In-Process / CPU / ONNX)</option>
                  <option value="api">API Remota / HTTP Supervisor</option>
                </select>
              </div>

              {/* System-1 Device */}
              <div>
                <label style={{ display: 'block', fontSize: '10.5px', color: '#8b949e', marginBottom: '3px' }}>
                  Dispositivo de Inferencia System-1:
                </label>
                <select
                  value={decisionDevice}
                  onChange={(e) => setDecisionDevice(e.target.value)}
                  style={{
                    width: '100%',
                    backgroundColor: '#0c0f14',
                    border: '1px solid #202737',
                    borderRadius: '4px',
                    padding: '4px 8px',
                    color: '#f0f6fc',
                    fontSize: '11px',
                    outline: 'none',
                  }}
                >
                  <option value="auto">Auto (Detección Óptima)</option>
                  <option value="cpu">CPU (Aislado)</option>
                  <option value="cuda">CUDA / GPU Acelerado</option>
                </select>
              </div>

              {/* System-1 Fallback Policy */}
              <div>
                <label style={{ display: 'block', fontSize: '10.5px', color: '#8b949e', marginBottom: '3px' }}>
                  Política de Fallback de Decisión:
                </label>
                <select
                  value={decisionFallback}
                  onChange={(e) => setDecisionFallback(e.target.value)}
                  style={{
                    width: '100%',
                    backgroundColor: '#0c0f14',
                    border: '1px solid #202737',
                    borderRadius: '4px',
                    padding: '4px 8px',
                    color: '#f0f6fc',
                    fontSize: '11px',
                    outline: 'none',
                  }}
                >
                  <option value="mock">Fallback a Mock (Seguro)</option>
                  <option value="replay">Fallback a Replay (Determinista)</option>
                  <option value="none">Sin Fallback (Fail-Closed Estricto)</option>
                </select>
              </div>

              {/* LLM Failure Policy */}
              <div>
                <label style={{ display: 'block', fontSize: '10.5px', color: '#8b949e', marginBottom: '3px' }}>
                  Política ante Fallo del LLM:
                </label>
                <select
                  value={llmFailurePolicy}
                  onChange={(e) => setLlmFailurePolicy(e.target.value)}
                  style={{
                    width: '100%',
                    backgroundColor: '#0c0f14',
                    border: '1px solid #202737',
                    borderRadius: '4px',
                    padding: '4px 8px',
                    color: '#f0f6fc',
                    fontSize: '11px',
                    outline: 'none',
                  }}
                >
                  <option value="synthetic_fallback">Synthetic Fallback (Planificador Sintético ⚠)</option>
                  <option value="fail_closed">Fail-Closed (Detener Misión Inmediatamente)</option>
                </select>
              </div>

              {/* LLM API Key */}
              {llmProvider !== 'simulator' && llmProvider !== 'ollama' && (
                <div>
                  <label style={{ display: 'block', fontSize: '10.5px', color: '#8b949e', marginBottom: '3px' }}>
                    API Key {llmProvider.toUpperCase()}:
                  </label>
                  <input
                    type="password"
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                    placeholder="sk-... (o dejar en blanco para usar .env)"
                    style={{
                      width: '100%',
                      backgroundColor: '#0c0f14',
                      border: '1px solid #202737',
                      borderRadius: '4px',
                      padding: '4px 8px',
                      color: '#f0f6fc',
                      fontSize: '11px',
                      outline: 'none',
                    }}
                  />
                </div>
              )}

              {/* Base URL */}
              {llmProvider === 'ollama' && (
                <div>
                  <label style={{ display: 'block', fontSize: '10.5px', color: '#8b949e', marginBottom: '3px' }}>
                    URL Base de Ollama:
                  </label>
                  <input
                    type="text"
                    value={baseUrl}
                    onChange={(e) => setBaseUrl(e.target.value)}
                    placeholder="http://localhost:11434"
                    style={{
                      width: '100%',
                      backgroundColor: '#0c0f14',
                      border: '1px solid #202737',
                      borderRadius: '4px',
                      padding: '4px 8px',
                      color: '#f0f6fc',
                      fontSize: '11px',
                      outline: 'none',
                    }}
                  />
                </div>
              )}

              {/* Workspace Root */}
              <div>
                <label style={{ display: 'block', fontSize: '10.5px', color: '#8b949e', marginBottom: '3px' }}>
                  Workspace Root (Directorio de Trabajo):
                </label>
                <input
                  type="text"
                  value={workspaceRoot}
                  onChange={(e) => setWorkspaceRoot(e.target.value)}
                  placeholder="Directorio local (ej. ./ o C:/path)"
                  style={{
                    width: '100%',
                    backgroundColor: '#0c0f14',
                    border: '1px solid #202737',
                    borderRadius: '4px',
                    padding: '4px 8px',
                    color: '#f0f6fc',
                    fontSize: '11px',
                    outline: 'none',
                  }}
                />
              </div>
            </div>
          )}
        </form>
      )}
    </div>
  );
}
