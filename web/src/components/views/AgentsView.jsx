import React, { useState } from 'react';
import {
  Bot,
  Shield,
  AlertTriangle,
  CheckCircle,
  Clock,
  ArrowRight,
  Terminal,
  FileCode,
  Globe,
  Sliders,
  Play,
  Cpu,
  RefreshCw,
  Lock,
  Layers,
  Sparkles,
} from 'lucide-react';

export default function AgentsView({
  session = {},
  sessionsList = [],
  missionConfig = {},
  isRunning = false,
  isPaused = false,
  onSelectSession,
  onLaunchAgentMission,
}) {
  const [selectedAgentProfile, setSelectedAgentProfile] = useState('CodingAgent');

  // Agent profiles definitions
  const agentProfiles = [
    {
      id: 'CodingAgent',
      name: 'CodingAgent',
      type: 'Agente Autónomo de Desarrollo (ReAct)',
      description: 'Especializado en diagnóstico, edición de archivos y ejecución de pruebas dentro del workspace.',
      defaultMode: 'local_restricted',
      recommendedModel: 'qwen2.5-coder:7b / Llama-3.3-70b',
      allowedTools: ['read_file', 'write_file', 'list_dir', 'run_command', 'git_status', 'git_diff'],
      reviewTools: ['delete_file', 'patch_file'],
      blockedTools: ['rm -rf', 'format', 'exfiltration'],
    },
    {
      id: 'SecurityAuditAgent',
      name: 'SecurityAuditAgent',
      type: 'Auditor de Seguridad y Secretos',
      description: 'Inspecciona dependencias, detecta credenciales expuestas y valida barreras de contención.',
      defaultMode: 'local_restricted',
      recommendedModel: 'Llama-3.3-70b / GPT-4o',
      allowedTools: ['read_file', 'list_dir', 'search_web', 'ast_grep'],
      reviewTools: ['run_command'],
      blockedTools: ['write_file', 'delete_file'],
    },
    {
      id: 'ExplorerAgent',
      name: 'ExplorerAgent',
      type: 'Explorador y Analizador de Solo Lectura',
      description: 'Navega y cartografía la arquitectura del repositorio sin realizar ninguna mutación en disco.',
      defaultMode: 'local_restricted',
      recommendedModel: 'qwen2.5-coder:7b',
      allowedTools: ['read_file', 'list_dir', 'search_web'],
      reviewTools: [],
      blockedTools: ['write_file', 'delete_file', 'run_command'],
    },
  ];

  // Active session stats
  const activeAgentName = session.agent_name || missionConfig.agent_name || 'CodingAgent';
  const activeMode = session.execution_mode || missionConfig.execution_mode || 'local_restricted';
  const activeGoal = session.goal || 'Sin misión activa';
  const totalDecisions = session.kpis?.totalDecisions || session.total_decisions || 0;
  const blockedCount = session.kpis?.blockedDecisions || session.blocked_count || 0;
  const reviewCount = session.kpis?.reviewDecisions || session.review_count || 0;
  const loopsPrevented = session.kpis?.loopsPrevented || 0;

  // Tools registry matrix
  const toolRegistry = [
    {
      name: 'read_file',
      category: 'Filesystem',
      policy: 'AUTO-ALLOW',
      policyColor: '#3fb950',
      description: 'Lectura de archivos confinados dentro del workspace root.',
    },
    {
      name: 'list_dir',
      category: 'Filesystem',
      policy: 'AUTO-ALLOW',
      policyColor: '#3fb950',
      description: 'Listado de archivos y subdirectorios del repositorio.',
    },
    {
      name: 'write_file',
      category: 'Filesystem',
      policy: 'VERIFIED SANDBOX',
      policyColor: '#58a6ff',
      description: 'Escritura de código con verificación canónica os.path.realpath.',
    },
    {
      name: 'run_command',
      category: 'Terminal',
      policy: 'RESTRICTED SANDBOX',
      policyColor: '#d29922',
      description: 'Ejecución en shell con intercepción de comandos destructivos.',
    },
    {
      name: 'delete_file',
      category: 'Filesystem',
      policy: 'HUMAN REVIEW REQUIRED',
      policyColor: '#d29922',
      description: 'Eliminación física de archivos protegida por compuerta HITL.',
    },
    {
      name: 'search_web',
      category: 'Network',
      policy: 'EGRESS BARRIER',
      policyColor: '#3fb950',
      description: 'Búsqueda web con aislamiento contra SSRF y cloud metadata.',
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
            color: '#c084fc',
          }}>
            <Bot size={18} />
          </div>
          <h1 style={{ fontSize: '18px', fontWeight: '700', color: '#f8fafc', letterSpacing: '-0.01em' }}>
            Supervisión Cognitiva de Agentes
          </h1>
        </div>
        <p style={{ fontSize: '12px', color: '#73849c', marginTop: '6px' }}>
          Monitoreo en tiempo real de agentes autónomos, control de tool registry y prevención de bucles cognitivos.
        </p>
      </div>

      {/* Active Agent Spotlight Card */}
      <div style={{
        backgroundColor: '#121824',
        borderRadius: '12px',
        border: '1px solid #233147',
        padding: '22px',
        display: 'flex',
        flexDirection: 'column',
        gap: '18px',
        boxShadow: 'var(--shadow-clay-sm)',
      }}>
        {/* Spotlight Top Bar */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          flexWrap: 'wrap',
          gap: '12px',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <div style={{
              width: '42px',
              height: '42px',
              borderRadius: '10px',
              backgroundColor: '#1a2336',
              border: '1px solid #2a3c5a',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: '#58a6ff',
            }}>
              <Bot size={22} />
            </div>

            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <h2 style={{ fontSize: '16px', fontWeight: '700', color: '#f8fafc' }}>
                  {activeAgentName}
                </h2>
                <span
                  style={{
                    fontSize: '11px',
                    fontWeight: '600',
                    padding: '2px 8px',
                    borderRadius: '12px',
                    backgroundColor: isRunning ? 'rgba(63, 185, 80, 0.15)' : isPaused ? 'rgba(210, 153, 34, 0.15)' : 'rgba(139, 148, 158, 0.15)',
                    color: isRunning ? '#3fb950' : isPaused ? '#d29922' : '#8b949e',
                    border: `1px solid ${isRunning ? 'rgba(63, 185, 80, 0.35)' : isPaused ? 'rgba(210, 153, 34, 0.35)' : 'rgba(139, 148, 158, 0.35)'}`,
                  }}
                >
                  {isRunning ? '● Ejecutando Misión' : isPaused ? '❚❚ En Pausa' : '○ Standby / Listo'}
                </span>
              </div>
              <span style={{ fontSize: '11.5px', color: '#73849c' }}>
                Modelo: {missionConfig.llm_model || 'qwen2.5-coder:7b'} · Sandbox: {activeMode}
              </span>
            </div>
          </div>

          <button
            onClick={() => onSelectSession?.(session.sessionId)}
            className="btn btn-primary"
            style={{ padding: '6px 14px', fontSize: '12px' }}
          >
            Ver Lienzo Live
            <ArrowRight size={13} />
          </button>
        </div>

        {/* Current Mission Box */}
        <div style={{
          backgroundColor: '#0c111a',
          borderRadius: '8px',
          border: '1px solid #1a2333',
          padding: '12px 16px',
        }}>
          <span style={{ fontSize: '11px', color: '#73849c', display: 'block', marginBottom: '4px' }}>
            Objetivo Actual de la Misión:
          </span>
          <p style={{ fontSize: '12.5px', color: '#e2e8f0', lineHeight: '1.45' }}>
            {activeGoal}
          </p>
        </div>

        {/* Real-time Supervision KPIs */}
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))',
          gap: '10px',
          textAlign: 'center',
          backgroundColor: '#0c111a',
          padding: '12px',
          borderRadius: '8px',
          border: '1px solid #1a2333',
        }}>
          <div>
            <div style={{ fontSize: '16px', fontWeight: '700', color: '#f8fafc', fontFamily: 'var(--font-mono)' }}>
              {totalDecisions}
            </div>
            <div style={{ fontSize: '10.5px', color: '#73849c', marginTop: '2px' }}>Decisiones Totales</div>
          </div>

          <div>
            <div style={{ fontSize: '16px', fontWeight: '700', color: '#3fb950', fontFamily: 'var(--font-mono)' }}>
              {Math.max(0, totalDecisions - blockedCount - reviewCount)}
            </div>
            <div style={{ fontSize: '10.5px', color: '#73849c', marginTop: '2px' }}>Autorizadas</div>
          </div>

          <div>
            <div style={{ fontSize: '16px', fontWeight: '700', color: '#d29922', fontFamily: 'var(--font-mono)' }}>
              {reviewCount}
            </div>
            <div style={{ fontSize: '10.5px', color: '#73849c', marginTop: '2px' }}>HITL Reviews</div>
          </div>

          <div>
            <div style={{ fontSize: '16px', fontWeight: '700', color: '#f85149', fontFamily: 'var(--font-mono)' }}>
              {blockedCount}
            </div>
            <div style={{ fontSize: '10.5px', color: '#73849c', marginTop: '2px' }}>Vetos de Política</div>
          </div>

          <div>
            <div style={{ fontSize: '16px', fontWeight: '700', color: '#c084fc', fontFamily: 'var(--font-mono)' }}>
              {loopsPrevented}
            </div>
            <div style={{ fontSize: '10.5px', color: '#73849c', marginTop: '2px' }}>Bucles Prevenidos</div>
          </div>
        </div>
      </div>

      {/* Two Column Layout: Tool Registry & Agent Profiles */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(420px, 1fr))',
        gap: '20px',
      }}>
        {/* Tool Registry & Permissions Matrix */}
        <div style={{
          backgroundColor: '#121824',
          borderRadius: '10px',
          border: '1px solid #1e293b',
          padding: '20px',
          display: 'flex',
          flexDirection: 'column',
          gap: '14px',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <Shield size={16} style={{ color: '#3fb950' }} />
            <h3 style={{ fontSize: '14px', fontWeight: '700', color: '#f8fafc' }}>
              Tool Registry y Confinamiento de Sandbox
            </h3>
          </div>
          <p style={{ fontSize: '11.5px', color: '#73849c' }}>
            Capacidades del agente restringidas por el PolicyEngine determinista en modo {activeMode}.
          </p>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            {toolRegistry.map((t) => (
              <div
                key={t.name}
                style={{
                  backgroundColor: '#0c111a',
                  borderRadius: '6px',
                  border: '1px solid #1a2333',
                  padding: '10px 14px',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  gap: '12px',
                }}
              >
                <div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                    <span style={{ fontSize: '12px', fontWeight: '600', color: '#f0f6fc', fontFamily: 'var(--font-mono)' }}>
                      {t.name}
                    </span>
                    <span style={{ fontSize: '10px', color: '#64748b' }}>
                      ({t.category})
                    </span>
                  </div>
                  <span style={{ fontSize: '11px', color: '#8b949e', marginTop: '2px', display: 'block' }}>
                    {t.description}
                  </span>
                </div>

                <span
                  style={{
                    fontSize: '10px',
                    fontWeight: '700',
                    padding: '2px 8px',
                    borderRadius: '4px',
                    backgroundColor: `${t.policyColor}18`,
                    color: t.policyColor,
                    border: `1px solid ${t.policyColor}35`,
                    whiteSpace: 'nowrap',
                  }}
                >
                  {t.policy}
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* Available Agent Profiles */}
        <div style={{
          backgroundColor: '#121824',
          borderRadius: '10px',
          border: '1px solid #1e293b',
          padding: '20px',
          display: 'flex',
          flexDirection: 'column',
          gap: '14px',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <Layers size={16} style={{ color: '#58a6ff' }} />
            <h3 style={{ fontSize: '14px', fontWeight: '700', color: '#f8fafc' }}>
              Perfiles de Agente Soportados
            </h3>
          </div>
          <p style={{ fontSize: '11.5px', color: '#73849c' }}>
            Configuraciones de roles y límites operacionales configurables para misiones en PRAXEON.
          </p>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
            {agentProfiles.map((p) => {
              const isSelected = selectedAgentProfile === p.id;
              return (
                <div
                  key={p.id}
                  onClick={() => setSelectedAgentProfile(p.id)}
                  style={{
                    backgroundColor: isSelected ? '#162030' : '#0c111a',
                    border: `1px solid ${isSelected ? '#388bfd' : '#1a2333'}`,
                    borderRadius: '8px',
                    padding: '14px',
                    cursor: 'pointer',
                    transition: 'all 0.15s ease',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '4px' }}>
                    <span style={{ fontSize: '13px', fontWeight: '700', color: isSelected ? '#58a6ff' : '#f0f6fc' }}>
                      {p.name}
                    </span>
                    <span style={{ fontSize: '10.5px', color: '#64748b' }}>
                      {p.recommendedModel}
                    </span>
                  </div>

                  <p style={{ fontSize: '11.5px', color: '#8b949e', marginBottom: '8px' }}>
                    {p.description}
                  </p>

                  <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
                    {p.allowedTools.slice(0, 4).map((tool) => (
                      <span
                        key={tool}
                        style={{
                          fontSize: '10px',
                          color: '#34d399',
                          backgroundColor: 'rgba(52, 211, 153, 0.1)',
                          padding: '1px 6px',
                          borderRadius: '3px',
                          fontFamily: 'var(--font-mono)',
                        }}
                      >
                        +{tool}
                      </span>
                    ))}
                    {p.blockedTools.slice(0, 2).map((tool) => (
                      <span
                        key={tool}
                        style={{
                          fontSize: '10px',
                          color: '#f87171',
                          backgroundColor: 'rgba(248, 113, 113, 0.1)',
                          padding: '1px 6px',
                          borderRadius: '3px',
                          fontFamily: 'var(--font-mono)',
                        }}
                      >
                        -{tool}
                      </span>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
