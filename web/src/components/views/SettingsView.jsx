import React, { useState, useEffect } from 'react';
import {
  Settings,
  Save,
  Check,
  RotateCcw,
  Sliders,
  Shield,
  Cpu,
  Bot,
  Terminal,
  Lock,
  Zap,
  AlertTriangle,
  Layers,
} from 'lucide-react';

const STORAGE_KEY = 'praxeon_runtime_settings_v1';

const DEFAULT_SETTINGS = {
  // LLM & Provider settings
  defaultProvider: 'ollama',
  defaultModel: 'qwen2.5-coder:7b',
  ollamaBaseUrl: 'http://localhost:11434',
  groqApiKey: '',
  openaiApiKey: '',
  typesafeApiKey: '',

  // Supervisor & Cognitive Guardrails
  defaultSupervisor: 'laya',
  layaBackend: 'auto',
  loopThreshold: 0.65,
  minGroundedThreshold: 0.35,
  evaluationChunkSize: 3,

  // Sandbox & Execution Policies
  defaultExecutionMode: 'local_restricted',
  defaultNetworkPolicy: 'isolated',
  requireConfirmationForFullAccess: true,
  maxStepsDefault: 25,
  stepDelayMs: 800,

  // Circuit Breaker & Safety
  failClosed: true,
  capabilityTtl: 300,
  circuitBreakerFailures: 3,
  circuitRecoverySeconds: 30,
};

export default function SettingsView({
  missionConfig = {},
  onConfigChange,
}) {
  const [settings, setSettings] = useState(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (saved) {
        return { ...DEFAULT_SETTINGS, ...JSON.parse(saved) };
      }
    } catch (e) {
      console.warn('Error reading settings from localStorage:', e);
    }
    return DEFAULT_SETTINGS;
  });

  const [saved, setSaved] = useState(false);
  const [activeTab, setActiveTab] = useState('supervisor');

  const handleChange = (key, value) => {
    setSettings((prev) => ({ ...prev, [key]: value }));
  };

  const handleSave = (e) => {
    e.preventDefault();
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));

      // Synchronize with missionConfig if callback available
      if (onConfigChange) {
        onConfigChange((prev) => ({
          ...prev,
          llm_provider: settings.defaultProvider,
          llm_model: settings.defaultModel,
          base_url: settings.ollamaBaseUrl,
          supervisor: settings.defaultSupervisor,
          execution_mode: settings.defaultExecutionMode,
          max_steps: settings.maxStepsDefault,
          step_delay_ms: settings.stepDelayMs,
        }));
      }

      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } catch (err) {
      console.error('Failed to save settings:', err);
    }
  };

  const handleReset = () => {
    if (window.confirm('¿Deseas restablecer todos los parámetros a los valores canónicos predeterminados?')) {
      setSettings(DEFAULT_SETTINGS);
      localStorage.setItem(STORAGE_KEY, JSON.stringify(DEFAULT_SETTINGS));
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    }
  };

  return (
    <div style={{
      flex: 1,
      padding: '24px 32px',
      overflowY: 'auto',
      backgroundColor: '#0a0e16',
      display: 'flex',
      flexDirection: 'column',
      gap: '20px',
    }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '16px' }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <div style={{
              width: '32px',
              height: '32px',
              borderRadius: '8px',
              backgroundColor: '#161c28',
              border: '1px solid #243044',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: '#58a6ff',
            }}>
              <Settings size={18} />
            </div>
            <h1 style={{ fontSize: '18px', fontWeight: '700', color: '#f8fafc', letterSpacing: '-0.01em' }}>
              Configuración del Runtime de Supervisión
            </h1>
          </div>
          <p style={{ fontSize: '12px', color: '#73849c', marginTop: '6px' }}>
            Parámetros operativos de PRAXEON 1.0: umbrales cognitivos, backends LLM, políticas de sandbox y resiliencia.
          </p>
        </div>

        {/* Action Buttons */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <button
            type="button"
            onClick={handleReset}
            className="btn btn-secondary"
            style={{ padding: '7px 12px', fontSize: '12px' }}
          >
            <RotateCcw size={13} />
            Restablecer Valores
          </button>

          <button
            onClick={handleSave}
            className="btn btn-primary"
            style={{ padding: '7px 16px', fontSize: '12px' }}
          >
            <Save size={13} />
            Guardar Cambios
          </button>
        </div>
      </div>

      {/* Save Toast Notification */}
      {saved && (
        <div style={{
          padding: '10px 16px',
          borderRadius: '8px',
          backgroundColor: 'rgba(63, 185, 80, 0.15)',
          border: '1px solid rgba(63, 185, 80, 0.35)',
          color: '#3fb950',
          fontSize: '12px',
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
        }}>
          <Check size={16} />
          <span>Configuración aplicada con éxito al runtime y guardada localmente.</span>
        </div>
      )}

      {/* Settings Navigation Tabs */}
      <div style={{
        display: 'flex',
        borderBottom: '1px solid #1e293b',
        gap: '4px',
      }}>
        {[
          { id: 'supervisor', label: 'Supervisor Cognitivo & Bucles', icon: Sliders },
          { id: 'models', label: 'Proveedores LLM & Endpoints', icon: Cpu },
          { id: 'sandbox', label: 'Sandbox & Aislamiento Físico', icon: Shield },
          { id: 'circuit', label: 'Circuit Breaker & Resiliencia', icon: Zap },
        ].map((tab) => {
          const Icon = tab.icon;
          const isActive = activeTab === tab.id;
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                padding: '10px 16px',
                border: 'none',
                background: 'none',
                borderBottom: isActive ? '2px solid #58a6ff' : '2px solid transparent',
                color: isActive ? '#f8fafc' : '#73849c',
                fontSize: '12.5px',
                fontWeight: isActive ? '600' : '400',
                cursor: 'pointer',
                transition: 'all 0.15s ease',
              }}
            >
              <Icon size={14} style={{ color: isActive ? '#58a6ff' : '#64748b' }} />
              {tab.label}
            </button>
          );
        })}
      </div>

      {/* Settings Tab Content */}
      <form onSubmit={handleSave} style={{ maxWidth: '800px' }}>
        {/* Tab 1: Supervisor Cognitivo */}
        {activeTab === 'supervisor' && (
          <div style={{
            backgroundColor: '#121824',
            borderRadius: '10px',
            border: '1px solid #1e293b',
            padding: '24px',
            display: 'flex',
            flexDirection: 'column',
            gap: '22px',
          }}>
            <div>
              <label style={{ fontSize: '13px', fontWeight: '600', color: '#f8fafc', display: 'block', marginBottom: '6px' }}>
                Supervisor Cognitivo Predeterminado
              </label>
              <p style={{ fontSize: '11.5px', color: '#73849c', marginBottom: '10px' }}>
                Define qué motor ejecuta la evaluación fast-path de propuestas y decisiones de replanificación.
              </p>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
                <div
                  onClick={() => handleChange('defaultSupervisor', 'laya')}
                  style={{
                    backgroundColor: settings.defaultSupervisor === 'laya' ? '#162234' : '#0c111a',
                    border: `1px solid ${settings.defaultSupervisor === 'laya' ? '#58a6ff' : '#1e293b'}`,
                    borderRadius: '8px',
                    padding: '14px',
                    cursor: 'pointer',
                  }}
                >
                  <div style={{ fontSize: '13px', fontWeight: '700', color: '#f8fafc' }}>LAYA (System-1)</div>
                  <div style={{ fontSize: '11px', color: '#8b949e', marginTop: '4px' }}>
                    Fast-path local ultra rápido (&lt;15ms), detector de bucles de invariantes n-gram y groundedness sintáctico.
                  </div>
                </div>

                <div
                  onClick={() => handleChange('defaultSupervisor', 'typesafe')}
                  style={{
                    backgroundColor: settings.defaultSupervisor === 'typesafe' ? '#162234' : '#0c111a',
                    border: `1px solid ${settings.defaultSupervisor === 'typesafe' ? '#58a6ff' : '#1e293b'}`,
                    borderRadius: '8px',
                    padding: '14px',
                    cursor: 'pointer',
                  }}
                >
                  <div style={{ fontSize: '13px', fontWeight: '700', color: '#f8fafc' }}>TypeSafe AI (System-2)</div>
                  <div style={{ fontSize: '11px', color: '#8b949e', marginTop: '4px' }}>
                    Escalación semántica profunda, diagnóstico de trayectorias divergentes y corte de alucinaciones.
                  </div>
                </div>
              </div>
            </div>

            {/* Loop Detection Threshold Slider */}
            <div>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '6px' }}>
                <label style={{ fontSize: '12.5px', fontWeight: '600', color: '#f8fafc' }}>
                  Umbral de Detección de Bucles Cognitivos (Loop Threshold)
                </label>
                <span style={{ fontSize: '13px', fontWeight: '700', color: '#58a6ff', fontFamily: 'var(--font-mono)' }}>
                  {settings.loopThreshold}
                </span>
              </div>
              <p style={{ fontSize: '11.5px', color: '#73849c', marginBottom: '8px' }}>
                Si la probabilidad de repetición o entropía de invariantes supera este umbral, el supervisor interviene automáticamente cortando el bucle con REPLAN.
              </p>
              <input
                type="range"
                min="0.40"
                max="0.95"
                step="0.05"
                value={settings.loopThreshold}
                onChange={(e) => handleChange('loopThreshold', parseFloat(e.target.value))}
                style={{ width: '100%', accentColor: '#388bfd', cursor: 'pointer' }}
              />
            </div>

            {/* Min Grounded Threshold Slider */}
            <div>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '6px' }}>
                <label style={{ fontSize: '12.5px', fontWeight: '600', color: '#f8fafc' }}>
                  Umbral Mínimo de Fundamentación (Min Grounded Threshold)
                </label>
                <span style={{ fontSize: '13px', fontWeight: '700', color: '#3fb950', fontFamily: 'var(--font-mono)' }}>
                  {settings.minGroundedThreshold}
                </span>
              </div>
              <p style={{ fontSize: '11.5px', color: '#73849c', marginBottom: '8px' }}>
                Acciones dirigidas a archivos o rutas inexistentes sin observación previa se consideran alucinaciones y reciben veto determinista si su score es inferior a este valor.
              </p>
              <input
                type="range"
                min="0.10"
                max="0.80"
                step="0.05"
                value={settings.minGroundedThreshold}
                onChange={(e) => handleChange('minGroundedThreshold', parseFloat(e.target.value))}
                style={{ width: '100%', accentColor: '#3fb950', cursor: 'pointer' }}
              />
            </div>

            {/* Chunk Size */}
            <div>
              <label style={{ fontSize: '12.5px', fontWeight: '600', color: '#f8fafc', display: 'block', marginBottom: '6px' }}>
                Tamaño de Bloque de Candidatos (Evaluation Chunk Size)
              </label>
              <select
                value={settings.evaluationChunkSize}
                onChange={(e) => handleChange('evaluationChunkSize', parseInt(e.target.value, 10))}
                style={{
                  width: '180px',
                  padding: '8px 12px',
                  borderRadius: '6px',
                  backgroundColor: '#0c111a',
                  border: '1px solid #1e293b',
                  color: '#f8fafc',
                  fontSize: '12px',
                  outline: 'none',
                }}
              >
                <option value={1}>1 candidato (Paso a paso estricto)</option>
                <option value={2}>2 candidatos por bloque</option>
                <option value={3}>3 candidatos (Recomendado)</option>
                <option value={5}>5 candidatos (Mayor paralelismo)</option>
              </select>
            </div>
          </div>
        )}

        {/* Tab 2: Proveedores LLM & Endpoints */}
        {activeTab === 'models' && (
          <div style={{
            backgroundColor: '#121824',
            borderRadius: '10px',
            border: '1px solid #1e293b',
            padding: '24px',
            display: 'flex',
            flexDirection: 'column',
            gap: '20px',
          }}>
            <div>
              <label style={{ fontSize: '12.5px', fontWeight: '600', color: '#f8fafc', display: 'block', marginBottom: '6px' }}>
                Proveedor de Inferencia LLM Predeterminado
              </label>
              <select
                value={settings.defaultProvider}
                onChange={(e) => handleChange('defaultProvider', e.target.value)}
                style={{
                  width: '100%',
                  padding: '9px 12px',
                  borderRadius: '6px',
                  backgroundColor: '#0c111a',
                  border: '1px solid #1e293b',
                  color: '#f8fafc',
                  fontSize: '12px',
                  outline: 'none',
                }}
              >
                <option value="ollama">Ollama (Servidor Local Seguro - Zero Data Egress)</option>
                <option value="groq">Groq Cloud (LPU Ultra-Fast &gt;200 t/s)</option>
                <option value="simulator">Simulador Determinista PRAXEON (Pruebas reproducibles)</option>
                <option value="openai">OpenAI API (GPT-4o / GPT-4o-mini)</option>
                <option value="gemini">Google Gemini API</option>
              </select>
            </div>

            <div>
              <label style={{ fontSize: '12.5px', fontWeight: '600', color: '#f8fafc', display: 'block', marginBottom: '6px' }}>
                Identificador de Modelo Predeterminado
              </label>
              <input
                type="text"
                value={settings.defaultModel}
                onChange={(e) => handleChange('defaultModel', e.target.value)}
                placeholder="Ej: qwen2.5-coder:7b, llama-3.3-70b-versatile, gpt-4o..."
                style={{
                  width: '100%',
                  padding: '9px 12px',
                  borderRadius: '6px',
                  backgroundColor: '#0c111a',
                  border: '1px solid #1e293b',
                  color: '#f8fafc',
                  fontSize: '12px',
                  fontFamily: 'var(--font-mono)',
                  outline: 'none',
                }}
              />
            </div>

            <div>
              <label style={{ fontSize: '12.5px', fontWeight: '600', color: '#f8fafc', display: 'block', marginBottom: '6px' }}>
                URL Base de Ollama Local
              </label>
              <input
                type="text"
                value={settings.ollamaBaseUrl}
                onChange={(e) => handleChange('ollamaBaseUrl', e.target.value)}
                placeholder="http://localhost:11434"
                style={{
                  width: '100%',
                  padding: '9px 12px',
                  borderRadius: '6px',
                  backgroundColor: '#0c111a',
                  border: '1px solid #1e293b',
                  color: '#f8fafc',
                  fontSize: '12px',
                  fontFamily: 'var(--font-mono)',
                  outline: 'none',
                }}
              />
            </div>

            <div style={{ borderTop: '1px solid #1a2333', paddingTop: '16px' }}>
              <h3 style={{ fontSize: '13px', fontWeight: '600', color: '#cbd5e1', marginBottom: '12px' }}>
                Claves de API Cloud (Opcionales para proveedores externos)
              </h3>

              <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                <div>
                  <label style={{ fontSize: '11.5px', color: '#8b949e', display: 'block', marginBottom: '4px' }}>
                    Groq API Key (gsk_...)
                  </label>
                  <input
                    type="password"
                    value={settings.groqApiKey}
                    onChange={(e) => handleChange('groqApiKey', e.target.value)}
                    placeholder="••••••••••••••••••••••••"
                    style={{
                      width: '100%',
                      padding: '8px 12px',
                      borderRadius: '6px',
                      backgroundColor: '#0c111a',
                      border: '1px solid #1e293b',
                      color: '#f8fafc',
                      fontSize: '12px',
                      fontFamily: 'var(--font-mono)',
                      outline: 'none',
                    }}
                  />
                </div>

                <div>
                  <label style={{ fontSize: '11.5px', color: '#8b949e', display: 'block', marginBottom: '4px' }}>
                    TypeSafe AI API Key
                  </label>
                  <input
                    type="password"
                    value={settings.typesafeApiKey}
                    onChange={(e) => handleChange('typesafeApiKey', e.target.value)}
                    placeholder="••••••••••••••••••••••••"
                    style={{
                      width: '100%',
                      padding: '8px 12px',
                      borderRadius: '6px',
                      backgroundColor: '#0c111a',
                      border: '1px solid #1e293b',
                      color: '#f8fafc',
                      fontSize: '12px',
                      fontFamily: 'var(--font-mono)',
                      outline: 'none',
                    }}
                  />
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Tab 3: Sandbox & Aislamiento */}
        {activeTab === 'sandbox' && (
          <div style={{
            backgroundColor: '#121824',
            borderRadius: '10px',
            border: '1px solid #1e293b',
            padding: '24px',
            display: 'flex',
            flexDirection: 'column',
            gap: '20px',
          }}>
            <div>
              <label style={{ fontSize: '12.5px', fontWeight: '600', color: '#f8fafc', display: 'block', marginBottom: '6px' }}>
                Modo de Confinamiento de Sandbox Predeterminado
              </label>
              <select
                value={settings.defaultExecutionMode}
                onChange={(e) => handleChange('defaultExecutionMode', e.target.value)}
                style={{
                  width: '100%',
                  padding: '9px 12px',
                  borderRadius: '6px',
                  backgroundColor: '#0c111a',
                  border: '1px solid #1e293b',
                  color: '#f8fafc',
                  fontSize: '12px',
                  outline: 'none',
                }}
              >
                <option value="local_restricted">local_restricted - Espacio Confinado (Recomendado)</option>
                <option value="container">container - Contenedor Aislado</option>
                <option value="full_access">full_access - Direct Host (Requiere confirmación explícita)</option>
              </select>
            </div>

            <div style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              padding: '12px 14px',
              borderRadius: '8px',
              backgroundColor: '#0c111a',
              border: '1px solid #1a2333',
            }}>
              <div>
                <div style={{ fontSize: '12.5px', fontWeight: '600', color: '#f8fafc' }}>
                  Confirmación Obligatoria para Modo Direct Host (Full Access)
                </div>
                <div style={{ fontSize: '11px', color: '#73849c', marginTop: '2px' }}>
                  Exige confirmación criptográfica del operador antes de permitir cualquier mutación sin sandbox.
                </div>
              </div>
              <input
                type="checkbox"
                checked={settings.requireConfirmationForFullAccess}
                onChange={(e) => handleChange('requireConfirmationForFullAccess', e.target.checked)}
                style={{ width: '18px', height: '18px', cursor: 'pointer', accentColor: '#388bfd' }}
              />
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
              <div>
                <label style={{ fontSize: '12.5px', fontWeight: '600', color: '#f8fafc', display: 'block', marginBottom: '6px' }}>
                  Límite Máximo de Pasos por Misión
                </label>
                <input
                  type="number"
                  min="5"
                  max="200"
                  value={settings.maxStepsDefault}
                  onChange={(e) => handleChange('maxStepsDefault', parseInt(e.target.value, 10) || 25)}
                  style={{
                    width: '100%',
                    padding: '8px 12px',
                    borderRadius: '6px',
                    backgroundColor: '#0c111a',
                    border: '1px solid #1e293b',
                    color: '#f8fafc',
                    fontSize: '12px',
                    outline: 'none',
                  }}
                />
              </div>

              <div>
                <label style={{ fontSize: '12.5px', fontWeight: '600', color: '#f8fafc', display: 'block', marginBottom: '6px' }}>
                  Retardo entre Pasos (ms)
                </label>
                <input
                  type="number"
                  min="100"
                  max="5000"
                  step="100"
                  value={settings.stepDelayMs}
                  onChange={(e) => handleChange('stepDelayMs', parseInt(e.target.value, 10) || 800)}
                  style={{
                    width: '100%',
                    padding: '8px 12px',
                    borderRadius: '6px',
                    backgroundColor: '#0c111a',
                    border: '1px solid #1e293b',
                    color: '#f8fafc',
                    fontSize: '12px',
                    outline: 'none',
                  }}
                />
              </div>
            </div>
          </div>
        )}

        {/* Tab 4: Circuit Breaker & Resiliencia */}
        {activeTab === 'circuit' && (
          <div style={{
            backgroundColor: '#121824',
            borderRadius: '10px',
            border: '1px solid #1e293b',
            padding: '24px',
            display: 'flex',
            flexDirection: 'column',
            gap: '20px',
          }}>
            <div style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              padding: '12px 14px',
              borderRadius: '8px',
              backgroundColor: '#0c111a',
              border: '1px solid #1a2333',
            }}>
              <div>
                <div style={{ fontSize: '12.5px', fontWeight: '600', color: '#f8fafc' }}>
                  Modo Fail-Closed (Seguridad por Defecto)
                </div>
                <div style={{ fontSize: '11px', color: '#73849c', marginTop: '2px' }}>
                  Si el evaluador semántico o la red fallan, bloquear incondicionalmente cualquier acción que mute el estado.
                </div>
              </div>
              <input
                type="checkbox"
                checked={settings.failClosed}
                onChange={(e) => handleChange('failClosed', e.target.checked)}
                style={{ width: '18px', height: '18px', cursor: 'pointer', accentColor: '#f85149' }}
              />
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
              <div>
                <label style={{ fontSize: '12.5px', fontWeight: '600', color: '#f8fafc', display: 'block', marginBottom: '6px' }}>
                  TTL de Capability Token (segundos)
                </label>
                <input
                  type="number"
                  min="30"
                  max="3600"
                  value={settings.capabilityTtl}
                  onChange={(e) => handleChange('capabilityTtl', parseInt(e.target.value, 10) || 300)}
                  style={{
                    width: '100%',
                    padding: '8px 12px',
                    borderRadius: '6px',
                    backgroundColor: '#0c111a',
                    border: '1px solid #1e293b',
                    color: '#f8fafc',
                    fontSize: '12px',
                    outline: 'none',
                  }}
                />
                <span style={{ fontSize: '11px', color: '#64748b', marginTop: '4px', display: 'block' }}>
                  Expiración automática del nonce HMAC (def: 300s).
                </span>
              </div>

              <div>
                <label style={{ fontSize: '12.5px', fontWeight: '600', color: '#f8fafc', display: 'block', marginBottom: '6px' }}>
                  Fallos Consecutivos para Abrir Circuito
                </label>
                <input
                  type="number"
                  min="1"
                  max="10"
                  value={settings.circuitBreakerFailures}
                  onChange={(e) => handleChange('circuitBreakerFailures', parseInt(e.target.value, 10) || 3)}
                  style={{
                    width: '100%',
                    padding: '8px 12px',
                    borderRadius: '6px',
                    backgroundColor: '#0c111a',
                    border: '1px solid #1e293b',
                    color: '#f8fafc',
                    fontSize: '12px',
                    outline: 'none',
                  }}
                />
                <span style={{ fontSize: '11px', color: '#64748b', marginTop: '4px', display: 'block' }}>
                  Umbral antes de activar circuito de recuperación (def: 3).
                </span>
              </div>
            </div>
          </div>
        )}
      </form>
    </div>
  );
}
