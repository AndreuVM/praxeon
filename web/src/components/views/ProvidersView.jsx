import React from 'react';
import { Cpu, Activity, Zap, CheckCircle2, AlertCircle } from 'lucide-react';

export default function ProvidersView() {
  const providers = [
    {
      id: 'laya',
      name: 'LAYA Provider',
      role: 'System-1 Primario (Fast-Path Local)',
      model: 'praxLaya-v1-quantized',
      status: 'Online',
      latencyP50: '12ms',
      latencyP95: '28ms',
      agreementRate: '94.2%',
      eceCalibration: '0.041 (Excelente)',
      primitives: ['choice', 'score', 'noul'],
      features: ['Detección de bucles invariante', 'Evaluación rápida de groundedness'],
    },
    {
      id: 'typesafe',
      name: 'TypeSafe AI Adapter',
      role: 'System-2 Secundario (Escalación Semántica)',
      model: 'typesafe-reasoning-v2',
      status: 'Connected',
      latencyP50: '88ms',
      latencyP95: '160ms',
      agreementRate: '91.8%',
      eceCalibration: '0.038 (Calibrado)',
      primitives: ['verify_trajectory', 'detect_hallucination', 'isolate_divergence'],
      features: ['Diagnóstico de alucinaciones en lote', 'Tolerancia a timeouts con failsafe'],
    },
    {
      id: 'router',
      name: 'ConfidenceAwareRouter',
      role: 'Enrutador en Cascada (JEV-as-a-Judge)',
      model: 'CascadePolicy-1.0',
      status: 'Active',
      latencyP50: '14ms (promedio)',
      latencyP95: '95ms',
      agreementRate: '98.5%',
      eceCalibration: '0.029 (Óptimo)',
      primitives: ['evaluate_confidence', 'route_fast_path', 'escalate_unsure'],
      features: ['Aceptación rápida si confianza > 0.85', 'Escalación automática ante incertidumbre'],
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
      gap: '20px',
    }}>
      <div>
        <h1 style={{ fontSize: '18px', fontWeight: '700', color: '#f8fafc' }}>
          Proveedores Semánticos y Calibración
        </h1>
        <p style={{ fontSize: '12px', color: '#73849c', marginTop: '4px' }}>
          Estado operativo, latencias percentiles y concordancia de los motores de evaluación System-1 y System-2.
        </p>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
        {providers.map((p) => (
          <div
            key={p.id}
            style={{
              backgroundColor: '#121926',
              borderRadius: '8px',
              border: '1px solid #1e2a3c',
              padding: '20px',
              display: 'flex',
              flexDirection: 'column',
              gap: '16px',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                <div style={{
                  width: '36px',
                  height: '36px',
                  borderRadius: '8px',
                  backgroundColor: '#182438',
                  border: '1px solid #24354e',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  color: '#38bdf8',
                }}>
                  <Cpu size={20} />
                </div>
                <div>
                  <h3 style={{ fontSize: '14px', fontWeight: '700', color: '#f8fafc' }}>{p.name}</h3>
                  <span style={{ fontSize: '11.5px', color: '#8595a8' }}>{p.role} · {p.model}</span>
                </div>
              </div>

              <span className="badge badge-success">
                <CheckCircle2 size={11} />
                {p.status}
              </span>
            </div>

            <div style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(4, 1fr)',
              gap: '12px',
              backgroundColor: '#0c111a',
              padding: '12px 16px',
              borderRadius: '6px',
              border: '1px solid #16202f',
              textAlign: 'center',
            }}>
              <div>
                <span style={{ fontSize: '10.5px', color: '#73849c', display: 'block' }}>Latencia p50</span>
                <span style={{ fontSize: '14px', fontWeight: '700', color: '#38bdf8', fontFamily: 'var(--font-mono)' }}>
                  {p.latencyP50}
                </span>
              </div>
              <div>
                <span style={{ fontSize: '10.5px', color: '#73849c', display: 'block' }}>Latencia p95</span>
                <span style={{ fontSize: '14px', fontWeight: '700', color: '#e2e8f0', fontFamily: 'var(--font-mono)' }}>
                  {p.latencyP95}
                </span>
              </div>
              <div>
                <span style={{ fontSize: '10.5px', color: '#73849c', display: 'block' }}>Concordancia</span>
                <span style={{ fontSize: '14px', fontWeight: '700', color: '#34d399' }}>
                  {p.agreementRate}
                </span>
              </div>
              <div>
                <span style={{ fontSize: '10.5px', color: '#73849c', display: 'block' }}>Calibración ECE</span>
                <span style={{ fontSize: '14px', fontWeight: '700', color: '#fbbf24' }}>
                  {p.eceCalibration}
                </span>
              </div>
            </div>

            <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
              {p.features.map((f, idx) => (
                <span
                  key={idx}
                  style={{
                    fontSize: '11px',
                    padding: '3px 9px',
                    borderRadius: '4px',
                    backgroundColor: '#16202e',
                    border: '1px solid #223044',
                    color: '#94a3b8',
                  }}
                >
                  ✓ {f}
                </span>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
