import React, { useState } from 'react';
import WorkspaceField from '../workspace/WorkspaceField';
import {
  Save,
  Check,
  RotateCcw,
  Key,
  Eye,
  EyeOff,
  CheckCircle2,
  AlertCircle,
  Trash2,
} from 'lucide-react';
import { getApiKey, setApiKey, clearApiKey, fetchHealth } from '../../services/api';
import { confirmDialog } from '../Dialogs';
import { ViewPage, ViewHeader } from '../ui';

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
  defaultWorkspaceRoot: '',
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
  _missionConfig = {},
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

  const [praxeonApiKey, setPraxeonApiKey] = useState(() => getApiKey() || '');
  const [showPraxeonKey, setShowPraxeonKey] = useState(false);
  const [testingAuth, setTestingAuth] = useState(false);
  const [authStatus, setAuthStatus] = useState(null);

  const [saved, setSaved] = useState(false);
  const [activeTab, setActiveTab] = useState('auth');

  const handleChange = (key, value) => {
    setSettings((prev) => ({ ...prev, [key]: value }));
  };

  const handleTestAuth = async () => {
    if (!praxeonApiKey.trim()) {
      setAuthStatus({ success: false, message: 'Introduce una clave de API antes de probar.' });
      return;
    }
    setTestingAuth(true);
    setAuthStatus(null);
    const prevKey = getApiKey();
    setApiKey(praxeonApiKey.trim());
    try {
      const res = await fetchHealth();
      if (res && res.status !== 'offline') {
        setAuthStatus({ success: true, message: 'Autenticación verificada. El servidor responde.' });
      } else {
        setApiKey(prevKey);
        setAuthStatus({ success: false, message: 'Fallo de autenticación (401/403) o servidor sin conexión.' });
      }
    } catch (err) {
      setApiKey(prevKey);
      setAuthStatus({ success: false, message: `Error de conexión: ${err.message}` });
    } finally {
      setTestingAuth(false);
    }
  };

  const handleClearAuth = () => {
    clearApiKey();
    setPraxeonApiKey('');
    setAuthStatus({ success: true, message: 'Credenciales de PRAXEON eliminadas.' });
    setTimeout(() => setAuthStatus(null), 3000);
  };

  const handleSave = (e) => {
    e.preventDefault();
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));

      // Guardar API Key maestra de PRAXEON en servicio centralizado
      if (praxeonApiKey && praxeonApiKey.trim()) {
        setApiKey(praxeonApiKey.trim());
      } else {
        clearApiKey();
      }

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

  const handleReset = async () => {
    if (await confirmDialog('¿Restablecer todos los parámetros a los valores predeterminados?', { confirmLabel: 'Restablecer', danger: true })) {
      setSettings(DEFAULT_SETTINGS);
      localStorage.setItem(STORAGE_KEY, JSON.stringify(DEFAULT_SETTINGS));
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    }
  };

  return (
    <ViewPage maxWidth={900} gap={20}>
      <ViewHeader
        title="Ajustes"
        description="Parámetros del runtime: umbrales cognitivos, motores de modelo, políticas de sandbox y resiliencia."
        actions={
          <>
            <button type="button" onClick={handleReset} className="btn btn-secondary">
              <RotateCcw size={13} />
              Restablecer valores
            </button>
            <button onClick={handleSave} className="btn btn-primary">
              <Save size={13} />
              Guardar cambios
            </button>
          </>
        }
      />

      {saved && (
        <div role="status" style={{ padding: '10px 14px', borderRadius: 'var(--r-ui)', border: '1px solid color-mix(in srgb, var(--ok) 40%, transparent)', color: 'var(--ok)', display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Check size={15} />
          <span>Configuración aplicada al runtime y guardada localmente.</span>
        </div>
      )}

      <div className="tabs" role="tablist" aria-label="Secciones de ajustes" style={{ borderBottom: '1px solid var(--line)', flexWrap: 'wrap' }}>
        {[
          { id: 'auth', label: 'Autenticación' },
          { id: 'supervisor', label: 'Supervisor y bucles' },
          { id: 'models', label: 'Modelos y endpoints' },
          { id: 'sandbox', label: 'Sandbox y aislamiento' },
          { id: 'circuit', label: 'Resiliencia' },
        ].map((tab) => (
          <button key={tab.id} type="button" role="tab" aria-selected={activeTab === tab.id} className={activeTab === tab.id ? 'on' : ''} onClick={() => setActiveTab(tab.id)}>
            {tab.label}
          </button>
        ))}
      </div>

      {/* Settings Tab Content */}
      <form onSubmit={handleSave} style={{ maxWidth: '800px' }}>
        {/* Tab 0: Autenticación & Clave Maestra */}
        {activeTab === 'auth' && (
          <div style={{
            backgroundColor: 'var(--surface)',
            borderRadius: '9px',
            border: '1px solid var(--line)',
            padding: '24px',
            display: 'flex',
            flexDirection: 'column',
            gap: '22px',
          }}>
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '6px' }}>
                <Key size={16} style={{ color: 'var(--accent)' }} />
                <label style={{ fontSize: '13px', fontWeight: '600', color: 'var(--text)' }}>
                  Clave maestra de acceso (PRAXEON_API_KEY)
                </label>
              </div>
              <p style={{ fontSize: '12.5px', color: 'var(--muted)', marginBottom: '14px', lineHeight: '1.5' }}>
                Requerida para comunicar la interfaz web con el servidor cuando el entorno se ejecuta en perfil de producción (<code>PRAXEON_PROFILE=production</code>) o tiene la variable de entorno <code>PRAXEON_API_KEY</code> configurada.
              </p>

              <div style={{ position: 'relative', display: 'flex', alignItems: 'center', marginBottom: '12px' }}>
                <input
                  type={showPraxeonKey ? 'text' : 'password'}
                  value={praxeonApiKey}
                  onChange={(e) => {
                    setPraxeonApiKey(e.target.value);
                    setAuthStatus(null);
                  }}
                  placeholder="Introduce tu PRAXEON_API_KEY…"
                  style={{
                    width: '100%',
                    padding: '10px 42px 10px 14px',
                    borderRadius: '9px',
                    backgroundColor: 'var(--bg)',
                    border: '1px solid var(--line)',
                    color: 'var(--text)',
                    fontSize: '12.5px',
                    fontFamily: 'var(--font-mono)',
                    outline: 'none',
                    boxSizing: 'border-box',
                  }}
                />
                <button
                  type="button"
                  onClick={() => setShowPraxeonKey(!showPraxeonKey)}
                  style={{
                    position: 'absolute',
                    right: '12px',
                    background: 'none',
                    border: 'none',
                    color: 'var(--muted)',
                    cursor: 'pointer',
                    padding: '2px',
                    display: 'flex',
                  }}
                >
                  {showPraxeonKey ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>

              {authStatus && (
                <div style={{
                  padding: '10px 14px',
                  borderRadius: '9px',
                  backgroundColor: authStatus.success ? 'color-mix(in srgb, var(--ok) 12%, transparent)' : 'color-mix(in srgb, var(--bad) 12%, transparent)',
                  border: `1px solid ${authStatus.success ? 'color-mix(in srgb, var(--ok) 30%, transparent)' : 'color-mix(in srgb, var(--bad) 30%, transparent)'}`,
                  color: authStatus.success ? 'var(--ok)' : 'var(--bad)',
                  fontSize: '12.5px',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                  marginBottom: '14px',
                }}>
                  {authStatus.success ? <CheckCircle2 size={16} /> : <AlertCircle size={16} />}
                  <span>{authStatus.message}</span>
                </div>
              )}

              <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
                <button
                  type="button"
                  onClick={handleTestAuth}
                  disabled={testingAuth || !praxeonApiKey.trim()}
                  className="btn btn-secondary"
                  style={{ padding: '8px 14px', fontSize: '12.5px', display: 'flex', alignItems: 'center', gap: '6px' }}
                >
                  <Key size={13} />
                  {testingAuth ? 'Comprobando...' : 'Probar la conexión'}
                </button>

                {praxeonApiKey && (
                  <button
                    type="button"
                    onClick={handleClearAuth}
                    className="btn btn-secondary"
                    style={{ padding: '8px 14px', fontSize: '12.5px', color: 'var(--bad)', display: 'flex', alignItems: 'center', gap: '6px' }}
                  >
                    <Trash2 size={13} />
                    Borrar la credencial
                  </button>
                )}
              </div>
            </div>

            <div style={{
              borderTop: '1px solid var(--line)',
              paddingTop: '18px',
              display: 'flex',
              flexDirection: 'column',
              gap: '10px',
            }}>
              <h3 style={{ fontSize: '12.5px', fontWeight: '600', color: 'var(--text)', margin: 0 }}>
                Seguridad y transmisión en tiempo real
              </h3>
              <ul style={{ fontSize: '12.5px', color: 'var(--muted)', margin: 0, paddingLeft: '18px', lineHeight: '1.6' }}>
                <li><strong>Inyección automática:</strong> Las llamadas REST envían la clave en las cabeceras <code>X-API-Key</code> y <code>Authorization: Bearer</code>.</li>
                <li><strong>Ticket efímero en WebSocket:</strong> La SPA solicita tokens de streaming efímeros de un solo uso (<code>/v1/sessions/:id/ws-ticket</code>) con TTL de 60s, evitando la exposición de claves maestras en URLs de WebSockets y registros de proxies.</li>
                <li><strong>Almacenamiento local seguro:</strong> La clave se guarda solo en memoria y en <code>sessionStorage</code> (se borra al cerrar la ventana); no se escribe en disco ni en <code>localStorage</code>.</li>
              </ul>
            </div>
          </div>
        )}

        {/* Tab 1: Supervisor Cognitivo */}
        {activeTab === 'supervisor' && (
          <div style={{
            backgroundColor: 'var(--surface)',
            borderRadius: '9px',
            border: '1px solid var(--line)',
            padding: '24px',
            display: 'flex',
            flexDirection: 'column',
            gap: '22px',
          }}>
            <div>
              <label style={{ fontSize: '13px', fontWeight: '600', color: 'var(--text)', display: 'block', marginBottom: '6px' }}>
                Supervisor cognitivo predeterminado
              </label>
              <p style={{ fontSize: '12.5px', color: 'var(--muted)', marginBottom: '10px' }}>
                Define qué motor ejecuta la evaluación fast-path de propuestas y decisiones de replanificación.
              </p>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
                <div
                  onClick={() => handleChange('defaultSupervisor', 'laya')}
                  style={{
                    backgroundColor: settings.defaultSupervisor === 'laya' ? 'var(--line)' : 'var(--bg)',
                    border: `1px solid ${settings.defaultSupervisor === 'laya' ? 'var(--accent)' : 'var(--line)'}`,
                    borderRadius: '9px',
                    padding: '14px',
                    cursor: 'pointer',
                  }}
                >
                  <div style={{ fontSize: '13px', fontWeight: '700', color: 'var(--text)' }}>LAYA (sistema 1)</div>
                  <div style={{ fontSize: '11px', color: 'var(--muted)', marginTop: '4px' }}>
                    Fast-path local ultra rápido (&lt;15ms), detector de bucles de invariantes n-gram y groundedness sintáctico.
                  </div>
                </div>

                <div
                  onClick={() => handleChange('defaultSupervisor', 'typesafe')}
                  style={{
                    backgroundColor: settings.defaultSupervisor === 'typesafe' ? 'var(--line)' : 'var(--bg)',
                    border: `1px solid ${settings.defaultSupervisor === 'typesafe' ? 'var(--accent)' : 'var(--line)'}`,
                    borderRadius: '9px',
                    padding: '14px',
                    cursor: 'pointer',
                  }}
                >
                  <div style={{ fontSize: '13px', fontWeight: '700', color: 'var(--text)' }}>TypeSafe AI (sistema 2)</div>
                  <div style={{ fontSize: '11px', color: 'var(--muted)', marginTop: '4px' }}>
                    Escalación semántica profunda, diagnóstico de trayectorias divergentes y corte de alucinaciones.
                  </div>
                </div>
              </div>
            </div>

            {/* Loop Detection Threshold Slider */}
            <div>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '6px' }}>
                <label style={{ fontSize: '12.5px', fontWeight: '600', color: 'var(--text)' }}>
                  Umbral de detección de bucles cognitivos
                </label>
                <span style={{ fontSize: '13px', fontWeight: '700', color: 'var(--accent)', fontFamily: 'var(--font-mono)' }}>
                  {settings.loopThreshold}
                </span>
              </div>
              <p style={{ fontSize: '12.5px', color: 'var(--muted)', marginBottom: '8px' }}>
                Si la probabilidad de repetición o entropía de invariantes supera este umbral, el supervisor interviene automáticamente cortando el bucle con REPLAN.
              </p>
              <input
                type="range"
                min="0.40"
                max="0.95"
                step="0.05"
                value={settings.loopThreshold}
                onChange={(e) => handleChange('loopThreshold', parseFloat(e.target.value))}
                style={{ width: '100%', accentColor: 'var(--accent)', cursor: 'pointer' }}
              />
            </div>

            {/* Min Grounded Threshold Slider */}
            <div>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '6px' }}>
                <label style={{ fontSize: '12.5px', fontWeight: '600', color: 'var(--text)' }}>
                  Umbral mínimo de fundamentación
                </label>
                <span style={{ fontSize: '13px', fontWeight: '700', color: 'var(--ok)', fontFamily: 'var(--font-mono)' }}>
                  {settings.minGroundedThreshold}
                </span>
              </div>
              <p style={{ fontSize: '12.5px', color: 'var(--muted)', marginBottom: '8px' }}>
                Acciones dirigidas a archivos o rutas inexistentes sin observación previa se consideran alucinaciones y reciben veto determinista si su score es inferior a este valor.
              </p>
              <input
                type="range"
                min="0.10"
                max="0.80"
                step="0.05"
                value={settings.minGroundedThreshold}
                onChange={(e) => handleChange('minGroundedThreshold', parseFloat(e.target.value))}
                style={{ width: '100%', accentColor: 'var(--ok)', cursor: 'pointer' }}
              />
            </div>

            {/* Chunk Size */}
            <div>
              <label style={{ fontSize: '12.5px', fontWeight: '600', color: 'var(--text)', display: 'block', marginBottom: '6px' }}>
                Tamaño del bloque de candidatos
              </label>
              <select
                value={settings.evaluationChunkSize}
                onChange={(e) => handleChange('evaluationChunkSize', parseInt(e.target.value, 10))}
                style={{
                  width: '180px',
                  padding: '8px 12px',
                  borderRadius: '9px',
                  backgroundColor: 'var(--bg)',
                  border: '1px solid var(--line)',
                  color: 'var(--text)',
                  fontSize: '12.5px',
                  outline: 'none',
                }}
              >
                <option value={1}>1 candidato (paso a paso estricto)</option>
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
            backgroundColor: 'var(--surface)',
            borderRadius: '9px',
            border: '1px solid var(--line)',
            padding: '24px',
            display: 'flex',
            flexDirection: 'column',
            gap: '20px',
          }}>
            <div>
              <label style={{ fontSize: '12.5px', fontWeight: '600', color: 'var(--text)', display: 'block', marginBottom: '6px' }}>
                Proveedor de modelo predeterminado
              </label>
              <select
                value={settings.defaultProvider}
                onChange={(e) => handleChange('defaultProvider', e.target.value)}
                style={{
                  width: '100%',
                  padding: '9px 12px',
                  borderRadius: '9px',
                  backgroundColor: 'var(--bg)',
                  border: '1px solid var(--line)',
                  color: 'var(--text)',
                  fontSize: '12.5px',
                  outline: 'none',
                }}
              >
                <option value="ollama">Ollama (servidor local, sin salida de datos)</option>
                <option value="groq">Groq Cloud (muy rápido, más de 200 t/s)</option>
                <option value="simulator">Simulador determinista de PRAXEON (pruebas reproducibles)</option>
                <option value="openai">OpenAI API (GPT-4o / GPT-4o-mini)</option>
                <option value="gemini">Google Gemini API</option>
                <option value="anthropic">Claude (API de Anthropic)</option>
                <option value="claude_cli">Claude CLI (suscripción)</option>
              </select>
            </div>

            <div>
              <label style={{ fontSize: '12.5px', fontWeight: '600', color: 'var(--text)', display: 'block', marginBottom: '6px' }}>
                Modelo predeterminado
              </label>
              <input
                type="text"
                value={settings.defaultModel}
                onChange={(e) => handleChange('defaultModel', e.target.value)}
                placeholder="Por ejemplo: qwen2.5-coder:7b, llama-3.3-70b-versatile, gpt-4o"
                style={{
                  width: '100%',
                  padding: '9px 12px',
                  borderRadius: '9px',
                  backgroundColor: 'var(--bg)',
                  border: '1px solid var(--line)',
                  color: 'var(--text)',
                  fontSize: '12.5px',
                  fontFamily: 'var(--font-mono)',
                  outline: 'none',
                }}
              />
            </div>

            <div>
              <label style={{ fontSize: '12.5px', fontWeight: '600', color: 'var(--text)', display: 'block', marginBottom: '6px' }}>
                URL base de Ollama local
              </label>
              <input
                type="text"
                value={settings.ollamaBaseUrl}
                onChange={(e) => handleChange('ollamaBaseUrl', e.target.value)}
                placeholder="http://localhost:11434"
                style={{
                  width: '100%',
                  padding: '9px 12px',
                  borderRadius: '9px',
                  backgroundColor: 'var(--bg)',
                  border: '1px solid var(--line)',
                  color: 'var(--text)',
                  fontSize: '12.5px',
                  fontFamily: 'var(--font-mono)',
                  outline: 'none',
                }}
              />
            </div>

            <div style={{ borderTop: '1px solid var(--line)', paddingTop: '16px' }}>
              <h3 style={{ fontSize: '13px', fontWeight: '600', color: 'var(--text)', marginBottom: '12px' }}>
                Claves de API en la nube (opcionales para proveedores externos)
              </h3>

              <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                <div>
                  <label style={{ fontSize: '12.5px', color: 'var(--muted)', display: 'block', marginBottom: '4px' }}>
                    Clave de API de Groq (gsk_…)
                  </label>
                  <input
                    type="password"
                    value={settings.groqApiKey}
                    onChange={(e) => handleChange('groqApiKey', e.target.value)}
                    placeholder="••••••••••••••••••••••••"
                    style={{
                      width: '100%',
                      padding: '8px 12px',
                      borderRadius: '9px',
                      backgroundColor: 'var(--bg)',
                      border: '1px solid var(--line)',
                      color: 'var(--text)',
                      fontSize: '12.5px',
                      fontFamily: 'var(--font-mono)',
                      outline: 'none',
                    }}
                  />
                </div>

                <div>
                  <label style={{ fontSize: '12.5px', color: 'var(--muted)', display: 'block', marginBottom: '4px' }}>
                    Clave de API de TypeSafe AI
                  </label>
                  <input
                    type="password"
                    value={settings.typesafeApiKey}
                    onChange={(e) => handleChange('typesafeApiKey', e.target.value)}
                    placeholder="••••••••••••••••••••••••"
                    style={{
                      width: '100%',
                      padding: '8px 12px',
                      borderRadius: '9px',
                      backgroundColor: 'var(--bg)',
                      border: '1px solid var(--line)',
                      color: 'var(--text)',
                      fontSize: '12.5px',
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
            backgroundColor: 'var(--surface)',
            borderRadius: '9px',
            border: '1px solid var(--line)',
            padding: '24px',
            display: 'flex',
            flexDirection: 'column',
            gap: '20px',
          }}>
            <div>
              <label style={{ fontSize: '12.5px', fontWeight: '600', color: 'var(--text)', display: 'block', marginBottom: '6px' }}>
                Modo de confinamiento predeterminado
              </label>
              <select
                value={settings.defaultExecutionMode}
                onChange={(e) => handleChange('defaultExecutionMode', e.target.value)}
                style={{
                  width: '100%',
                  padding: '9px 12px',
                  borderRadius: '9px',
                  backgroundColor: 'var(--bg)',
                  border: '1px solid var(--line)',
                  color: 'var(--text)',
                  fontSize: '12.5px',
                  outline: 'none',
                }}
              >
                <option value="local_restricted">local_restricted: espacio confinado (recomendado)</option>
                <option value="container">container: contenedor aislado</option>
                <option value="full_access">full_access: acceso directo al host (requiere confirmación explícita)</option>
              </select>
            </div>

            <div>
              <label style={{ fontSize: '12.5px', fontWeight: '600', color: 'var(--text)', display: 'flex', justifyContent: 'space-between', marginBottom: '6px' }}>
                <span>Workspace del proyecto</span>
                <span style={{ fontSize: '11px', color: 'var(--accent)' }}>Confinamiento de ruta física</span>
              </label>
              <WorkspaceField global />
              <p style={{ fontSize: '11px', color: 'var(--muted)', marginTop: '4px' }}>
                Carpeta sobre la que trabajan todas las tareas nuevas: el sandbox y el supervisor solo permiten operar dentro de ella. Se guarda en el servidor y también puedes cambiarla desde el selector de workspace del riel de navegación. Cada tarea puede usar otra carpeta si lo necesita.
              </p>
            </div>

            <div style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              padding: '12px 14px',
              borderRadius: '9px',
              backgroundColor: 'var(--bg)',
              border: '1px solid var(--line)',
            }}>
              <div>
                <div style={{ fontSize: '12.5px', fontWeight: '600', color: 'var(--text)' }}>
                  Confirmación obligatoria para el acceso directo al host (Full Access)
                </div>
                <div style={{ fontSize: '11px', color: 'var(--muted)', marginTop: '2px' }}>
                  Exige confirmación criptográfica del operador antes de permitir cualquier mutación sin sandbox.
                </div>
              </div>
              <input
                type="checkbox"
                checked={settings.requireConfirmationForFullAccess}
                onChange={(e) => handleChange('requireConfirmationForFullAccess', e.target.checked)}
                style={{ width: '18px', height: '18px', cursor: 'pointer', accentColor: 'var(--accent)' }}
              />
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
              <div>
                <label style={{ fontSize: '12.5px', fontWeight: '600', color: 'var(--text)', display: 'block', marginBottom: '6px' }}>
                  Límite máximo de pasos por misión
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
                    borderRadius: '9px',
                    backgroundColor: 'var(--bg)',
                    border: '1px solid var(--line)',
                    color: 'var(--text)',
                    fontSize: '12.5px',
                    outline: 'none',
                  }}
                />
              </div>

              <div>
                <label style={{ fontSize: '12.5px', fontWeight: '600', color: 'var(--text)', display: 'block', marginBottom: '6px' }}>
                  Retardo entre pasos (ms)
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
                    borderRadius: '9px',
                    backgroundColor: 'var(--bg)',
                    border: '1px solid var(--line)',
                    color: 'var(--text)',
                    fontSize: '12.5px',
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
            backgroundColor: 'var(--surface)',
            borderRadius: '9px',
            border: '1px solid var(--line)',
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
              borderRadius: '9px',
              backgroundColor: 'var(--bg)',
              border: '1px solid var(--line)',
            }}>
              <div>
                <div style={{ fontSize: '12.5px', fontWeight: '600', color: 'var(--text)' }}>
                  Modo de fallo cerrado (seguridad por defecto)
                </div>
                <div style={{ fontSize: '11px', color: 'var(--muted)', marginTop: '2px' }}>
                  Si el evaluador semántico o la red fallan, bloquear incondicionalmente cualquier acción que mute el estado.
                </div>
              </div>
              <input
                type="checkbox"
                checked={settings.failClosed}
                onChange={(e) => handleChange('failClosed', e.target.checked)}
                style={{ width: '18px', height: '18px', cursor: 'pointer', accentColor: 'var(--bad)' }}
              />
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
              <div>
                <label style={{ fontSize: '12.5px', fontWeight: '600', color: 'var(--text)', display: 'block', marginBottom: '6px' }}>
                  Vida del token de capacidad (segundos)
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
                    borderRadius: '9px',
                    backgroundColor: 'var(--bg)',
                    border: '1px solid var(--line)',
                    color: 'var(--text)',
                    fontSize: '12.5px',
                    outline: 'none',
                  }}
                />
                <span style={{ fontSize: '11px', color: 'var(--muted)', marginTop: '4px', display: 'block' }}>
                  Expiración automática del nonce HMAC (por defecto: 300 s).
                </span>
              </div>

              <div>
                <label style={{ fontSize: '12.5px', fontWeight: '600', color: 'var(--text)', display: 'block', marginBottom: '6px' }}>
                  Fallos consecutivos para abrir el circuito
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
                    borderRadius: '9px',
                    backgroundColor: 'var(--bg)',
                    border: '1px solid var(--line)',
                    color: 'var(--text)',
                    fontSize: '12.5px',
                    outline: 'none',
                  }}
                />
                <span style={{ fontSize: '11px', color: 'var(--muted)', marginTop: '4px', display: 'block' }}>
                  Umbral antes de activar circuito de recuperación (por defecto: 3).
                </span>
              </div>
            </div>
          </div>
        )}
      </form>
    </ViewPage>
  );
}
