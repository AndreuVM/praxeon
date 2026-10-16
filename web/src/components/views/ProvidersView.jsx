import React, { useState, useEffect } from 'react';
import {
  Cpu,
  CheckCircle2,
  AlertCircle,
  RefreshCw,
  Server,
  Cloud,
} from 'lucide-react';
import * as api from '../../services/api';
import { ViewPage, ViewHeader, Chip } from '../ui';
import CascadeEditor from './CascadeEditor';

export default function ProvidersView() {
  const [healthStatus, setHealthStatus] = useState(null);
  const [testingEndpoint, setTestingEndpoint] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [dynamicProviders, setDynamicProviders] = useState(null);

  const checkHealth = async () => {
    setTestingEndpoint(true);
    const start = performance.now();
    try {
      const res = await api.fetchHealth();
      const provs = await api.fetchProviders();
      const elapsed = Math.round(performance.now() - start);
      setHealthStatus({ ...res, latency: elapsed });
      if (provs?.data) {
        setDynamicProviders(provs.data);
      }
      setTestResult({
        success: res && (!res.data || res.data.status !== 'offline'),
        message: `Servidor PRAXEON activo (${elapsed}ms) — Catálogo de Decision Providers sincronizado`,
      });
    } catch (err) {
      setTestResult({
        success: false,
        message: `Fallo de conexión: ${err.message}`,
      });
    } finally {
      setTestingEndpoint(false);
    }
  };

  useEffect(() => {
    let mounted = true;
    (async () => {
      const start = performance.now();
      try {
        const res = await api.fetchHealth();
        const provs = await api.fetchProviders();
        const elapsed = Math.round(performance.now() - start);
        if (mounted) {
          setHealthStatus({ ...res, latency: elapsed });
          if (provs?.data) {
            setDynamicProviders(provs.data);
          }
          setTestResult({
            success: res && (!res.data || res.data.status !== 'offline'),
            message: `Servidor PRAXEON activo (${elapsed}ms)`,
          });
        }
      } catch (err) {
        if (mounted) {
          setTestResult({
            success: false,
            message: `Fallo de conexión: ${err.message}`,
          });
        }
      }
    })();
    return () => {
      mounted = false;
    };
  }, []);

  const decisionProviders = dynamicProviders?.decision_providers || [
    {
      provider_id: 'laya',
      name: 'LAYA System-1',
      model_id: 'laya-v1',
      version: '0.3.0',
      backend: 'local',
      supported_backends: ['local', 'api'],
      description: 'Evaluación semántica ultrarrápida in-process sin latencia de red',
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
      supported_backends: ['api'],
      description: 'Adaptador de compatibilidad histórica y supervisión remota',
      available: true,
      average_latency_display: 'N/A',
      fallbacks: ['mock', 'replay'],
    },
    {
      provider_id: 'replay',
      name: 'Proveedor de reproducción',
      model_id: 'replay-v1',
      version: '1.0.0',
      backend: 'local',
      supported_backends: ['local'],
      description: 'Ejecución determinista para benchmarks científicos reproducibles',
      available: true,
      average_latency_display: 'N/A',
      fallbacks: ['mock'],
    },
    {
      provider_id: 'mock',
      name: 'Proveedor simulado',
      model_id: 'mock-v1',
      version: '1.0.0',
      backend: 'local',
      supported_backends: ['local'],
      description: 'Supervisor de tests unitarios y CI/CD',
      available: true,
      average_latency_display: 'N/A',
      fallbacks: [],
    },
  ];

  const inferenceEngines = dynamicProviders?.llm_providers || [
    { provider_id: 'simulator', name: 'Simulador Determinista PRAXEON', default_model: 'deterministic-replay-v1', type: 'offline' },
    { provider_id: 'ollama', name: 'Ollama (inferencia local)', default_model: 'qwen2.5-coder:7b', type: 'local' },
    { provider_id: 'groq', name: 'Groq Cloud Inference', default_model: 'llama-3.3-70b-versatile', type: 'cloud' },
    { provider_id: 'gemini', name: 'Google Gemini', default_model: 'gemini-1.5-flash', type: 'cloud' },
    { provider_id: 'openai', name: 'OpenAI', default_model: 'gpt-4o-mini', type: 'cloud' },
    { provider_id: 'anthropic', name: 'Anthropic Claude (API)', default_model: 'claude-sonnet-5-5', type: 'cloud' },
    { provider_id: 'claude_cli', name: 'Claude CLI (suscripción)', default_model: 'sonnet', type: 'local' },
    { provider_id: 'openrouter', name: 'OpenRouter Cloud', default_model: 'anthropic/claude-3.5-sonnet', type: 'cloud' },
  ];

  const TYPE_LABEL = { local: 'Local', offline: 'Sin conexión', cloud: 'Nube' };
  const sectionTitle = { fontSize: 'var(--fs-chat)', fontWeight: 600, marginBottom: '12px' };
  const miniLabel = { fontSize: 'var(--fs-label)', color: 'var(--muted)', display: 'block' };

  return (
    <ViewPage>
      <ViewHeader
        title="Proveedores"
        description="Supervisores y motores de inferencia disponibles, con la telemetría real que devuelve el servidor (GET /v1/providers)."
        actions={
          <button onClick={checkHealth} disabled={testingEndpoint} className="btn btn-secondary">
            <RefreshCw size={13} className={testingEndpoint ? 'spin' : ''} />
            Actualizar telemetría
          </button>
        }
      />

      {testResult && (
        <div
          role="status"
          style={{
            padding: '10px 14px',
            borderRadius: 'var(--r-ui)',
            border: `1px solid ${testResult.success ? 'color-mix(in srgb, var(--ok) 40%, transparent)' : 'var(--bad)'}`,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: '12px',
            flexWrap: 'wrap',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: testResult.success ? 'var(--ok)' : 'var(--bad)' }}>
            {testResult.success ? <CheckCircle2 size={15} /> : <AlertCircle size={15} />}
            <span>{testResult.message}</span>
          </div>
          {healthStatus?.latency && (
            <span style={{ fontSize: 'var(--fs-code)', color: 'var(--muted)', fontFamily: 'var(--font-mono)' }}>Latencia (RTT): {healthStatus.latency} ms</span>
          )}
        </div>
      )}

      <section>
        <h2 style={sectionTitle}>Supervisores registrados (sistema 1)</h2>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(340px, 1fr))', gap: '14px' }}>
          {decisionProviders.map((s) => (
            <div key={s.provider_id} style={{ background: 'var(--surface)', borderRadius: 'var(--r-card)', border: '1px solid var(--line)', padding: '18px', display: 'flex', flexDirection: 'column', gap: '14px' }}>
              <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '10px' }}>
                <div style={{ minWidth: 0 }}>
                  <h3 style={{ fontSize: 'var(--fs-ui)', fontWeight: 600 }}>{s.name} {s.version ? `(v${s.version})` : ''}</h3>
                  <span style={{ fontSize: 'var(--fs-secondary)', color: 'var(--muted)' }}>
                    Modelo: <code>{s.model_id}</code> · Motor: <code>{s.backend}</code>
                  </span>
                </div>
                <Chip tone={s.available ? 'ok' : 'bad'}>{s.available ? 'Operativo' : 'No instalado'}</Chip>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '8px', borderTop: '1px solid var(--line)', borderBottom: '1px solid var(--line)', padding: '10px 0' }}>
                <div>
                  <span style={miniLabel}>Latencia observada</span>
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-code)' }}>{s.average_latency_display || 'N/D'}</span>
                </div>
                <div>
                  <span style={miniLabel}>Respaldos</span>
                  <span style={{ fontSize: 'var(--fs-secondary)' }}>{s.fallbacks && s.fallbacks.length > 0 ? s.fallbacks.join(', ') : 'Ninguno'}</span>
                </div>
                <div>
                  <span style={miniLabel}>Motores</span>
                  <span style={{ fontSize: 'var(--fs-secondary)' }}>{s.supported_backends && s.supported_backends.length > 0 ? s.supported_backends.join(', ') : s.backend}</span>
                </div>
              </div>

              {s.description && <p style={{ color: 'var(--muted)', lineHeight: 1.4 }}>{s.description}</p>}
            </div>
          ))}
        </div>
      </section>

      <CascadeEditor decisionProviders={dynamicProviders?.decision_providers} />

      <section>
        <h2 style={sectionTitle}>Motores de inferencia para agentes</h2>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: '14px' }}>
          {inferenceEngines.map((eng) => (
            <div key={eng.provider_id} style={{ background: 'var(--surface)', borderRadius: 'var(--r-card)', border: '1px solid var(--line)', padding: '16px 18px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                <span style={{ color: 'var(--muted)', display: 'grid', placeItems: 'center' }}>
                  {eng.type === 'local' ? <Server size={18} strokeWidth={1.6} /> : eng.type === 'offline' ? <Cpu size={18} strokeWidth={1.6} /> : <Cloud size={18} strokeWidth={1.6} />}
                </span>
                <div style={{ minWidth: 0 }}>
                  <h3 style={{ fontSize: 'var(--fs-ui)', fontWeight: 600 }}>{eng.name}</h3>
                  <span style={{ fontSize: 'var(--fs-secondary)', color: 'var(--muted)' }}>
                    Modelo por defecto: <code>{eng.default_model}</code>
                  </span>
                </div>
              </div>
              <div style={{ borderTop: '1px solid var(--line)', paddingTop: '10px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', fontSize: 'var(--fs-secondary)', color: 'var(--muted)' }}>
                <span>Tipo: {TYPE_LABEL[eng.type] || (eng.type ? eng.type : 'Nube')}</span>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-code)' }}>{eng.provider_id}</span>
              </div>
            </div>
          ))}
        </div>
      </section>
    </ViewPage>
  );
}
