import React, { useState, useEffect } from 'react';
import {
  Cpu,
  CheckCircle2,
  AlertCircle,
  RefreshCw,
  Server,
  Cloud,
  Shield,
  Activity,
  Layers,
} from 'lucide-react';
import * as api from '../../services/api';

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
      name: 'TypeSafe AI (Legacy / KEV)',
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
      name: 'Replay Provider',
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
      name: 'Mock Provider',
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
    { provider_id: 'ollama', name: 'Ollama Inferencia Local', default_model: 'qwen2.5-coder:7b', type: 'local' },
    { provider_id: 'groq', name: 'Groq Cloud Inference', default_model: 'llama-3.3-70b-versatile', type: 'cloud' },
    { provider_id: 'gemini', name: 'Google Gemini', default_model: 'gemini-1.5-flash', type: 'cloud' },
    { provider_id: 'openai', name: 'OpenAI', default_model: 'gpt-4o-mini', type: 'cloud' },
    { provider_id: 'openrouter', name: 'OpenRouter Cloud', default_model: 'anthropic/claude-3.5-sonnet', type: 'cloud' },
  ];

  return (
    <div style={{
      flex: 1,
      padding: '24px 32px',
      overflowY: 'auto',
      backgroundColor: '#0a0e16',
      display: 'flex',
      flexDirection: 'column',
      gap: '24px',
    }}>
      {/* Header */}
      <div style={{
        display: 'flex',
        alignItems: 'flex-start',
        justifyContent: 'space-between',
        flexWrap: 'wrap',
        gap: '16px',
      }}>
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
              color: '#3fb950',
            }}>
              <Cpu size={18} />
            </div>
            <h1 style={{ fontSize: '18px', fontWeight: '700', color: '#f8fafc', letterSpacing: '-0.01em' }}>
              Catálogo de Decision Providers y Motores de Inferencia
            </h1>
          </div>
          <p style={{ fontSize: '12px', color: '#73849c', marginTop: '6px' }}>
            Telemetría real obtenida dinámicamente vía <code>GET /v1/providers</code> (PRAXEON 1.1 Decision Model Independence).
          </p>
        </div>

        {/* Test Connection Button */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <button
            onClick={checkHealth}
            disabled={testingEndpoint}
            className="btn btn-secondary"
            style={{ padding: '8px 14px', fontSize: '12px' }}
          >
            <RefreshCw size={13} className={testingEndpoint ? 'spin' : ''} />
            Actualizar Telemetría Real
          </button>
        </div>
      </div>

      {/* Connectivity Probe Alert */}
      {testResult && (
        <div style={{
          padding: '10px 16px',
          borderRadius: '8px',
          backgroundColor: testResult.success ? 'rgba(63, 185, 80, 0.1)' : 'rgba(248, 81, 73, 0.1)',
          border: `1px solid ${testResult.success ? 'rgba(63, 185, 80, 0.3)' : 'rgba(248, 81, 73, 0.3)'}`,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            {testResult.success ? (
              <CheckCircle2 size={16} style={{ color: '#3fb950' }} />
            ) : (
              <AlertCircle size={16} style={{ color: '#f85149' }} />
            )}
            <span style={{ fontSize: '12.5px', color: '#f8fafc' }}>
              {testResult.message}
            </span>
          </div>

          {healthStatus?.latency && (
            <span style={{ fontSize: '11.5px', color: '#8b949e', fontFamily: 'var(--font-mono)' }}>
              Latencia RTT: {healthStatus.latency}ms
            </span>
          )}
        </div>
      )}

      {/* Section 1: Cognitive Decision Providers */}
      <div>
        <h2 style={{ fontSize: '14px', fontWeight: '700', color: '#f8fafc', marginBottom: '12px' }}>
          1. Supervisores System-1 (Decision Providers Registrados)
        </h2>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(360px, 1fr))', gap: '16px' }}>
          {decisionProviders.map((s) => (
            <div
              key={s.provider_id}
              style={{
                backgroundColor: '#121824',
                borderRadius: '10px',
                border: '1px solid #1e293b',
                padding: '20px',
                display: 'flex',
                flexDirection: 'column',
                gap: '14px',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <div>
                  <h3 style={{ fontSize: '14px', fontWeight: '700', color: '#f0f6fc' }}>
                    {s.name} {s.version ? `(v${s.version})` : ''}
                  </h3>
                  <span style={{ fontSize: '11.5px', color: '#8b949e' }}>
                    Modelo: <code>{s.model_id}</code> · Backend: <code>{s.backend}</code>
                  </span>
                </div>
                <span className={`badge ${s.available ? 'badge-success' : 'badge-danger'}`}>
                  {s.available ? 'Operativo' : 'No Instalado'}
                </span>
              </div>

              {/* Metrics Grid */}
              <div style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(3, 1fr)',
                gap: '8px',
                backgroundColor: '#0c111a',
                padding: '10px 12px',
                borderRadius: '6px',
                border: '1px solid #1a2333',
                textAlign: 'center',
              }}>
                <div>
                  <span style={{ fontSize: '10px', color: '#73849c', display: 'block' }}>Latencia Observada</span>
                  <span style={{ fontSize: '13px', fontWeight: '700', color: '#58a6ff', fontFamily: 'var(--font-mono)' }}>
                    {s.average_latency_display || 'N/A'}
                  </span>
                </div>
                <div>
                  <span style={{ fontSize: '10px', color: '#73849c', display: 'block' }}>Fallbacks</span>
                  <span style={{ fontSize: '11px', fontWeight: '600', color: '#a78bfa' }}>
                    {s.fallbacks && s.fallbacks.length > 0 ? s.fallbacks.join(', ') : 'Ninguno'}
                  </span>
                </div>
                <div>
                  <span style={{ fontSize: '10px', color: '#73849c', display: 'block' }}>Backends</span>
                  <span style={{ fontSize: '11px', fontWeight: '600', color: '#38bdf8' }}>
                    {s.supported_backends && s.supported_backends.length > 0 ? s.supported_backends.join(', ') : s.backend}
                  </span>
                </div>
              </div>

              {s.description && (
                <p style={{ margin: 0, fontSize: '11.5px', color: '#94a3b8', lineHeight: '1.4' }}>
                  {s.description}
                </p>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Section 2: LLM Inference Backends */}
      <div>
        <h2 style={{ fontSize: '14px', fontWeight: '700', color: '#f8fafc', marginBottom: '12px' }}>
          2. Motores de Inferencia para Agentes (LLM Backends)
        </h2>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: '16px' }}>
          {inferenceEngines.map((eng) => (
            <div
              key={eng.provider_id}
              style={{
                backgroundColor: '#121824',
                borderRadius: '10px',
                border: '1px solid #1e293b',
                padding: '18px',
                display: 'flex',
                flexDirection: 'column',
                justifyContent: 'space-between',
                gap: '14px',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                <div style={{
                  width: '34px',
                  height: '34px',
                  borderRadius: '8px',
                  backgroundColor: '#1a2336',
                  border: '1px solid #2a3c5a',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  color: '#58a6ff',
                }}>
                  {eng.type === 'local' ? <Server size={18} /> : eng.type === 'offline' ? <Cpu size={18} /> : <Cloud size={18} />}
                </div>
                <div>
                  <h3 style={{ fontSize: '13.5px', fontWeight: '700', color: '#f0f6fc' }}>
                    {eng.name}
                  </h3>
                  <span style={{ fontSize: '11px', color: '#64748b' }}>
                    Default: <code>{eng.default_model}</code>
                  </span>
                </div>
              </div>

              <div style={{
                borderTop: '1px solid #1a2436',
                paddingTop: '10px',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                fontSize: '11px',
              }}>
                <span style={{ color: '#3fb950', fontWeight: '500' }}>
                  ● Tipo: {eng.type ? eng.type.toUpperCase() : 'CLOUD'}
                </span>
                <span style={{ color: '#8b949e' }}>
                  Provider ID: {eng.provider_id}
                </span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
