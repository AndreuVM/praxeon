import React, { useState } from 'react';
import { Play, Pause, Square, Sparkles, Terminal, Cpu, Shield, RefreshCw, AlertTriangle, Box } from 'lucide-react';

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
  const [llmProvider, setLlmProvider] = useState('simulator');
  const [supervisor, setSupervisor] = useState('laya');
  const [maxSteps, setMaxSteps] = useState(6);
  const [apiKey, setApiKey] = useState('');
  const [customModel, setCustomModel] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [isExpanded, setIsExpanded] = useState(true);

  const presetGoals = [
    'Fix authentication bug in the API',
    'Auditar y ejecutar suite de tests con pytest',
    'Inspeccionar contención de red y variables de entorno',
  ];

  const handleSubmit = (e) => {
    e?.preventDefault();
    if (!goal.trim()) return;
    if (executionMode === 'full_access' && !fullAccessConfirmed) {
      alert('Debes confirmar que comprendes que el aislamiento de proceso del SO está deshabilitado para usar Full Access.');
      return;
    }
    onStartMission?.({
      goal: goal.trim(),
      execution_mode: executionMode,
      llm_provider: llmProvider,
      llm_model: customModel.trim() || undefined,
      api_key: apiKey.trim() || undefined,
      base_url: baseUrl.trim() || undefined,
      supervisor: supervisor,
      max_steps: Number(maxSteps),
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

          {/* Model and Supervisor Selectors */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
            {/* LLM Provider / Model */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <Cpu size={13} style={{ color: '#8b949e' }} />
              <label style={{ fontSize: '11px', color: '#8b949e', fontWeight: '500' }}>Modelo LLM:</label>
              <select
                value={llmProvider}
                onChange={(e) => setLlmProvider(e.target.value)}
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
                <option value="simulator">Simulated Agent (Offline)</option>
                <option value="groq">Groq Cloud (llama-3.3-70b)</option>
                <option value="ollama">Ollama Local (qwen2.5-coder)</option>
                <option value="gemini">Google Gemini (gemini-1.5-flash)</option>
                <option value="openai">OpenAI (gpt-4o-mini)</option>
                <option value="openrouter">OpenRouter (claude-3.5-sonnet)</option>
              </select>
            </div>

            {/* Supervisor Engine */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <Shield size={13} style={{ color: '#8b949e' }} />
              <label style={{ fontSize: '11px', color: '#8b949e', fontWeight: '500' }}>Supervisión:</label>
              <select
                value={supervisor}
                onChange={(e) => setSupervisor(e.target.value)}
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
                <option value="laya">LAYA System-1 (Semántico Rápido)</option>
                <option value="typesafe">TypeSafe AI (Reglas Formales & AST)</option>
                <option value="cascade">Cascade JEV (Dual LAYA + TypeSafe)</option>
              </select>
            </div>

            {/* Execution Environment Selector */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <Terminal size={13} style={{ color: executionMode === 'full_access' ? '#f87171' : '#8b949e' }} />
              <label style={{ fontSize: '11px', color: '#8b949e', fontWeight: '500' }}>Entorno:</label>
              <select
                value={executionMode}
                onChange={(e) => {
                  setExecutionMode(e.target.value);
                  if (e.target.value !== 'full_access') setFullAccessConfirmed(false);
                }}
                disabled={isRunning}
                style={{
                  backgroundColor: '#0c0f14',
                  border: `1px solid ${executionMode === 'full_access' ? 'rgba(239, 68, 68, 0.5)' : '#202737'}`,
                  borderRadius: '6px',
                  padding: '5px 8px',
                  color: executionMode === 'full_access' ? '#f87171' : '#f0f6fc',
                  fontSize: '11.5px',
                  cursor: 'pointer',
                  outline: 'none',
                  fontWeight: executionMode === 'full_access' ? '600' : 'normal',
                }}
              >
                <option value="local_restricted">Local Restricted Sandbox (Defecto)</option>
                <option value="container">Container Sandbox (Docker)</option>
                <option value="full_access">Full Access (Host Direct) ⚠</option>
              </select>
            </div>

            {/* Steps limit */}
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
                <option value={4}>4 pasos</option>
                <option value={6}>6 pasos</option>
                <option value={8}>8 pasos</option>
                <option value={12}>12 pasos</option>
              </select>
            </div>

            {/* Toggle Configuración Avanzada de LLM */}
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
              <span>⚙</span>
              <span>{showAdvanced ? 'Ocultar ajustes LLM' : 'Ajustes LLM / API Key'}</span>
            </button>

            {/* Primary Action Button */}
            <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: '8px' }}>
              {!isRunning ? (
                <button
                  type="submit"
                  disabled={executionMode === 'full_access' && !fullAccessConfirmed}
                  className="btn btn-primary"
                  style={{
                    padding: '6px 14px',
                    fontSize: '12px',
                    backgroundColor: executionMode === 'full_access' && !fullAccessConfirmed ? '#3b1c1c' : '#202837',
                    borderColor: executionMode === 'full_access' && !fullAccessConfirmed ? '#5c2222' : '#384558',
                    cursor: executionMode === 'full_access' && !fullAccessConfirmed ? 'not-allowed' : 'pointer',
                    opacity: executionMode === 'full_access' && !fullAccessConfirmed ? 0.6 : 1,
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
          {executionMode === 'full_access' && (
            <div style={{
              backgroundColor: 'rgba(239, 68, 68, 0.08)',
              border: '1px solid rgba(239, 68, 68, 0.35)',
              borderRadius: '6px',
              padding: '10px 14px',
              display: 'flex',
              flexDirection: 'column',
              gap: '6px',
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#f87171', fontSize: '12px', fontWeight: '700' }}>
                <AlertTriangle size={15} />
                <span>ADVERTENCIA DE SEGURIDAD: Ejecución Host Direct (Full Access)</span>
              </div>
              <p style={{ margin: 0, fontSize: '11px', color: '#cbd5e1', lineHeight: '1.45' }}>
                En este modo, el agente opera directamente sobre el sistema operativo anfitrión sin aislamiento de proceso ni chroot. Aunque la cadena de custodia formal de PRAXEON supervisa cada acción con capabilities criptográficas, <strong>no existe contención de red ni de sistema de archivos</strong>.
              </p>
              <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '11.5px', color: '#fca5a5', cursor: 'pointer', marginTop: '4px' }}>
                <input
                  type="checkbox"
                  checked={fullAccessConfirmed}
                  onChange={(e) => setFullAccessConfirmed(e.target.checked)}
                  style={{ cursor: 'pointer', width: '14px', height: '14px' }}
                />
                <span style={{ fontWeight: '600' }}>Entiendo y acepto que el aislamiento de procesos del SO está deshabilitado.</span>
              </label>
            </div>
          )}

          {/* Provider Info Banner & Advanced Config */}
          <div
            style={{
              padding: '6px 10px',
              borderRadius: '6px',
              backgroundColor: '#121620',
              border: '1px solid #1a2232',
              fontSize: '11px',
              display: 'flex',
              flexDirection: 'column',
              gap: '8px',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <span style={{ color: '#8b949e' }}>
                {llmProvider === 'simulator' && '⚡ Modo Simulado Dinámico: Genera pasos y decisiones coherentes adaptadas estrictamente a tu prompt.'}
                {llmProvider === 'ollama' && '🦙 Ollama Local: Ejecuta llamadas LLM reales offline hacia tu servidor local.'}
                {llmProvider === 'groq' && '⚡ Groq Cloud: Ejecuta llamadas LLM reales de alta velocidad (usa GROQ_API_KEY o introduce tu clave abajo).'}
                {llmProvider === 'openai' && '🧠 OpenAI Oficial: Ejecuta llamadas LLM reales a la API de OpenAI (usa OPENAI_API_KEY o introduce clave).'}
                {llmProvider === 'gemini' && '✨ Google Gemini: Ejecuta llamadas LLM reales a la API de Gemini (usa GEMINI_API_KEY o introduce clave).'}
                {llmProvider === 'openrouter' && '🌐 OpenRouter: Catálogo multimodelo con inferencia en la nube (usa OPENROUTER_API_KEY o introduce clave).'}
              </span>
            </div>

            {showAdvanced && (
              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
                  gap: '8px',
                  paddingTop: '6px',
                  borderTop: '1px solid #1c2436',
                }}
              >
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

                <div>
                  <label style={{ display: 'block', fontSize: '10.5px', color: '#8b949e', marginBottom: '3px' }}>
                    Modelo Específico (opcional):
                  </label>
                  <input
                    type="text"
                    value={customModel}
                    onChange={(e) => setCustomModel(e.target.value)}
                    placeholder={
                      llmProvider === 'groq' ? 'llama-3.3-70b-versatile' :
                      llmProvider === 'ollama' ? 'qwen2.5-coder:7b' :
                      llmProvider === 'gemini' ? 'gemini-1.5-flash' :
                      llmProvider === 'openai' ? 'gpt-4o-mini' : 'modelo personalizado'
                    }
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
              </div>
            )}
          </div>
        </form>
      )}
    </div>
  );
}
