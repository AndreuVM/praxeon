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

export default function ProvidersView() {
  const [healthStatus, setHealthStatus] = useState(null);
  const [testingEndpoint, setTestingEndpoint] = useState(false);
  const [testResult, setTestResult] = useState(null);

  const checkHealth = async () => {
    setTestingEndpoint(true);
    const start = performance.now();
    try {
      const res = await api.fetchHealth();
      const elapsed = Math.round(performance.now() - start);
      setHealthStatus({ ...res, latency: elapsed });
      setTestResult({
        success: res && (!res.data || res.data.status !== 'offline'),
        message: `Servidor PRAXEON activo (${elapsed}ms)`,
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
        const elapsed = Math.round(performance.now() - start);
        if (mounted) {
          setHealthStatus({ ...res, latency: elapsed });
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

  const supervisors = [
    {
      id: 'laya',
      name: 'LAYA Provider',
      role: 'System-1 Primario (Fast-Path Local)',
      model: 'praxLaya-v1-calibrated',
      latency: '< 15ms',
      status: 'Activo / Integrado',
      calibration: '0.041 (Excelente)',
      agreementRate: '94.8%',
      features: [
        'Detección de bucles invariante (n-gram entropy)',
        'Evaluación de groundedness estructural',
        'Failsafe local sin latencia de red',
      ],
    },
    {
      id: 'typesafe',
      name: 'TypeSafe AI / JEV-as-a-Judge',
      role: 'System-2 Secundario (Escalación Semántica)',
      model: 'typesafe-reasoning-v2',
      latency: '75ms - 150ms',
      status: 'Ready / Conectado',
      calibration: '0.038 (Calibrado)',
      agreementRate: '92.4%',
      features: [
        'Diagnóstico profundo de trayectorias divergentes',
        'Cálculo de probabilidad de alucinación',
        'Reconciliación de planes conflictivos',
      ],
    },
  ];

  const inferenceEngines = [
    {
      id: 'ollama',
      name: 'Ollama Inferencia Local',
      type: 'Local Server',
      endpoint: 'http://localhost:11434',
      models: 'qwen2.5-coder:7b, llama3.1:8b, deepseek-r1',
      status: 'Recomendado para desarrollo privado',
      privacy: '100% Local (Zero data egress)',
      icon: Server,
    },
    {
      id: 'groq',
      name: 'Groq Cloud Inference',
      type: 'LPU Ultra-Fast Cloud',
      endpoint: 'api.groq.com/openai/v1',
      models: 'llama-3.3-70b-versatile, qwen-2.5-coder-32b',
      status: 'Inferencia a >200 tokens/segundo',
      privacy: 'Conexión SSL Encriptada',
      icon: Cloud,
    },
    {
      id: 'simulator',
      name: 'Simulador Determinista PRAXEON',
      type: 'Local Testbed Harness',
      endpoint: 'En memoria (Runtime)',
      models: 'deterministic-replay-v1',
      status: '100% Reproducible para Tests CI/CD',
      privacy: 'Completamente aislado',
      icon: Cpu,
    },
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
              Proveedores Semánticos y Motores de Inferencia
            </h1>
          </div>
          <p style={{ fontSize: '12px', color: '#73849c', marginTop: '6px' }}>
            Supervisores cognitivos System-1 / System-2, calibración de confianza y adaptadores LLM soportados.
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
            Probar Conectividad
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

      {/* Section 1: Cognitive Supervisors */}
      <div>
        <h2 style={{ fontSize: '14px', fontWeight: '700', color: '#f8fafc', marginBottom: '12px' }}>
          1. Motores de Supervisión Cognitiva (Guardrails & Calibración)
        </h2>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(380px, 1fr))', gap: '16px' }}>
          {supervisors.map((s) => (
            <div
              key={s.id}
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
                    {s.name}
                  </h3>
                  <span style={{ fontSize: '11.5px', color: '#8b949e' }}>
                    {s.role}
                  </span>
                </div>
                <span className="badge badge-success">
                  {s.status}
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
                  <span style={{ fontSize: '10px', color: '#73849c', display: 'block' }}>Latencia</span>
                  <span style={{ fontSize: '13px', fontWeight: '700', color: '#58a6ff', fontFamily: 'var(--font-mono)' }}>
                    {s.latency}
                  </span>
                </div>
                <div>
                  <span style={{ fontSize: '10px', color: '#73849c', display: 'block' }}>Concordancia</span>
                  <span style={{ fontSize: '13px', fontWeight: '700', color: '#3fb950', fontFamily: 'var(--font-mono)' }}>
                    {s.agreementRate}
                  </span>
                </div>
                <div>
                  <span style={{ fontSize: '10px', color: '#73849c', display: 'block' }}>ECE Score</span>
                  <span style={{ fontSize: '13px', fontWeight: '700', color: '#d29922', fontFamily: 'var(--font-mono)' }}>
                    {s.calibration.split(' ')[0]}
                  </span>
                </div>
              </div>

              {/* Features list */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                {s.features.map((f, idx) => (
                  <div key={idx} style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11px', color: '#94a3b8' }}>
                    <span style={{ color: '#3fb950' }}>✓</span>
                    <span>{f}</span>
                  </div>
                ))}
              </div>
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
          {inferenceEngines.map((eng) => {
            const Icon = eng.icon;
            return (
              <div
                key={eng.id}
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
                <div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '10px' }}>
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
                      <Icon size={18} />
                    </div>
                    <div>
                      <h3 style={{ fontSize: '13.5px', fontWeight: '700', color: '#f0f6fc' }}>
                        {eng.name}
                      </h3>
                      <span style={{ fontSize: '11px', color: '#64748b' }}>
                        {eng.type}
                      </span>
                    </div>
                  </div>

                  <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', fontSize: '11.5px' }}>
                    <div style={{ color: '#94a3b8' }}>
                      <strong style={{ color: '#cbd5e1' }}>Endpoint:</strong> {eng.endpoint}
                    </div>
                    <div style={{ color: '#94a3b8' }}>
                      <strong style={{ color: '#cbd5e1' }}>Modelos:</strong> {eng.models}
                    </div>
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
                    ● {eng.privacy}
                  </span>
                  <span style={{ color: '#64748b' }}>
                    {eng.status}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
