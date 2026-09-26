import React from 'react';
import { Bot, Shield, AlertTriangle, CheckCircle, Clock, ArrowRight } from 'lucide-react';

export default function AgentsView({ onSelectSession }) {
  const agents = [
    {
      id: 'agent_coding',
      name: 'CodingAgent',
      type: 'Autonomous Code Assistant',
      model: 'Claude-3.5-sonnet',
      status: 'Active',
      currentGoal: 'Fix authentication bug in the API',
      sessionId: '7f3a2c',
      decisionsTotal: 12,
      interventions: 1,
      loopsPrevented: 2,
      lastSeen: '14:32:44 UTC',
    },
    {
      id: 'agent_audit',
      name: 'SecurityAuditAgent',
      type: 'Vulnerability Scanner',
      model: 'GPT-4o',
      status: 'Idle',
      currentGoal: 'Audit dependencies and secret exposure',
      sessionId: '8b19ca',
      decisionsTotal: 45,
      interventions: 4,
      loopsPrevented: 0,
      lastSeen: '12:15:20 UTC',
    },
    {
      id: 'agent_deploy',
      name: 'DeployBot',
      type: 'CI/CD Operator',
      model: 'Llama-3.3-70b',
      status: 'Standby',
      currentGoal: 'Canary release validation',
      sessionId: '1a90fe',
      decisionsTotal: 8,
      interventions: 2,
      loopsPrevented: 1,
      lastSeen: '09:40:11 UTC',
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
          Agentes Supervisados
        </h1>
        <p style={{ fontSize: '12px', color: '#73849c', marginTop: '4px' }}>
          Monitorización del estado cognitivo, prevención de bucles y tasas de intervención por agente.
        </p>
      </div>

      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))',
        gap: '16px',
      }}>
        {agents.map((agent) => (
          <div
            key={agent.id}
            style={{
              backgroundColor: '#121926',
              borderRadius: '8px',
              border: '1px solid #1e2a3c',
              padding: '18px',
              display: 'flex',
              flexDirection: 'column',
              gap: '14px',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                <div style={{
                  width: '32px',
                  height: '32px',
                  borderRadius: '6px',
                  backgroundColor: '#1a2436',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  color: '#38bdf8',
                }}>
                  <Bot size={18} />
                </div>
                <div>
                  <h3 style={{ fontSize: '13.5px', fontWeight: '700', color: '#f8fafc' }}>{agent.name}</h3>
                  <span style={{ fontSize: '11px', color: '#64748b' }}>{agent.model}</span>
                </div>
              </div>
              <span className={`badge ${agent.status === 'Active' ? 'badge-success' : 'badge-neutral'}`}>
                {agent.status}
              </span>
            </div>

            <div style={{
              padding: '10px 12px',
              backgroundColor: '#0b0f17',
              borderRadius: '6px',
              border: '1px solid #16202f',
              fontSize: '11.5px',
            }}>
              <span style={{ color: '#64748b', display: 'block', marginBottom: '2px' }}>Tarea actual:</span>
              <span style={{ color: '#cbd5e1' }}>{agent.currentGoal}</span>
            </div>

            <div style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(3, 1fr)',
              gap: '8px',
              textAlign: 'center',
              borderTop: '1px solid #1a2333',
              paddingTop: '12px',
            }}>
              <div>
                <div style={{ fontSize: '14px', fontWeight: '700', color: '#f8fafc' }}>
                  {agent.decisionsTotal}
                </div>
                <div style={{ fontSize: '10px', color: '#73849c', marginTop: '2px' }}>Decisiones</div>
              </div>
              <div>
                <div style={{ fontSize: '14px', fontWeight: '700', color: '#fbbf24' }}>
                  {agent.interventions}
                </div>
                <div style={{ fontSize: '10px', color: '#73849c', marginTop: '2px' }}>Intervenciones</div>
              </div>
              <div>
                <div style={{ fontSize: '14px', fontWeight: '700', color: '#34d399' }}>
                  {agent.loopsPrevented}
                </div>
                <div style={{ fontSize: '10px', color: '#73849c', marginTop: '2px' }}>Bucles Prevenidos</div>
              </div>
            </div>

            <div style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              borderTop: '1px solid #1a2333',
              paddingTop: '10px',
              fontSize: '11px',
              color: '#64748b',
            }}>
              <span>Visto: {agent.lastSeen}</span>
              <button
                onClick={() => onSelectSession?.(agent.sessionId)}
                className="btn btn-secondary"
                style={{ padding: '3px 8px', fontSize: '10.5px' }}
              >
                Ver Sesión
                <ArrowRight size={11} />
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
