import React, { useState } from 'react';
import {
  FolderKanban,
  Plus,
  Clock,
  Cpu,
  CheckCircle,
  XCircle,
  AlertTriangle,
  ArrowRight,
  Search,
  RefreshCw,
  Shield,
  Bot,
  Play,
  Pause,
  Terminal,
  Layers,
  Sparkles,
  ExternalLink,
  Trash2,
} from 'lucide-react';

export default function SessionsView({
  sessions = [],
  currentSessionId,
  onSelectSession,
  onCreateSession,
  onRefreshSessions,
  onDeleteSession,
  onClearOldSessions,
  onNewCleanSession,
}) {
  const [filter, setFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('ALL');
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);

  // New session form state
  const [formData, setFormData] = useState({
    goal: '',
    agent_name: 'CodingAgent',
    execution_mode: 'local_restricted',
    workspace_root: '',
    llm_provider: 'simulator',
    llm_model: 'qwen2.5-coder:7b',
    supervisor: 'laya',
    max_steps: 25,
  });

  const handleRefresh = async () => {
    setIsRefreshing(true);
    try {
      if (onRefreshSessions) {
        await onRefreshSessions();
      }
    } finally {
      setTimeout(() => setIsRefreshing(false), 400);
    }
  };

  const handleClearOld = async () => {
    if (!window.confirm('¿Deseas purgar todas las sesiones antiguas/finalizadas? Se eliminarán de base de datos, memoria y checkpoints para liberar espacio.')) {
      return;
    }
    if (onClearOldSessions) {
      await onClearOldSessions();
    }
  };

  const handleCreateSubmit = (e) => {
    e.preventDefault();
    if (!formData.goal.trim()) return;

    onCreateSession?.({
      goal: formData.goal.trim(),
      agent_name: formData.agent_name,
      execution_mode: formData.execution_mode,
      workspace_root: formData.workspace_root.trim() || undefined,
      llm_provider: formData.llm_provider,
      llm_model: formData.llm_model,
      supervisor: formData.supervisor,
      max_steps: Number(formData.max_steps) || 25,
    });

    setIsModalOpen(false);
    setFormData((prev) => ({ ...prev, goal: '', workspace_root: '' }));
  };

  const starterTemplates = [
    {
      title: 'Auditar vulnerabilidades de autenticación',
      goal: 'Analizar middleware de autenticación, verificar que no existan bypasses ni tokens hardcodeados, y ejecutar suite de tests.',
      provider: 'simulator',
      mode: 'local_restricted',
    },
    {
      title: 'Refactorizar consultas y evitar bucles',
      goal: 'Identificar rutas de ejecución redundantes en el módulo de persistencia y proponer un plan de optimización determinista.',
      provider: 'simulator',
      mode: 'local_restricted',
    },
    {
      title: 'Verificar contención de sandbox y variables de entorno',
      goal: 'Probar que intentos de acceso a .env y rutas fuera del workspace sean interceptados por el PolicyEngine con recibos HMAC.',
      provider: 'simulator',
      mode: 'local_restricted',
    },
  ];

  const filteredSessions = sessions.filter((s) => {
    const text = filter.toLowerCase();
    const matchesText =
      (s.goal || '').toLowerCase().includes(text) ||
      (s.session_id || '').toLowerCase().includes(text) ||
      (s.agent_name || '').toLowerCase().includes(text) ||
      (s.execution_mode || '').toLowerCase().includes(text);

    const matchesStatus =
      statusFilter === 'ALL' ||
      (s.status || 'Active').toUpperCase() === statusFilter.toUpperCase();

    return matchesText && matchesStatus;
  });

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
      {/* View Header */}
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
              color: '#58a6ff',
            }}>
              <FolderKanban size={18} />
            </div>
            <h1 style={{ fontSize: '18px', fontWeight: '700', color: '#f8fafc', letterSpacing: '-0.01em' }}>
              Sesiones de Supervisión
            </h1>
          </div>
          <p style={{ fontSize: '12px', color: '#73849c', marginTop: '6px' }}>
            Gestión, aislamiento y trazabilidad inmutable de misiones de agentes autónomos auditadas por PRAXEON.
          </p>
        </div>

        {/* Action Buttons */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <button
            onClick={handleClearOld}
            className="btn btn-secondary"
            title="Eliminar sesiones antiguas y finalizadas para liberar espacio"
            style={{
              padding: '8px 12px',
              fontSize: '12px',
              color: '#f87171',
              borderColor: 'rgba(239, 68, 68, 0.35)',
              backgroundColor: 'rgba(239, 68, 68, 0.08)',
            }}
          >
            <Trash2 size={13} />
            Limpiar antiguas
          </button>

          <button
            onClick={handleRefresh}
            className="btn btn-secondary"
            title="Refrescar sesiones"
            style={{ padding: '8px 12px', fontSize: '12px' }}
          >
            <RefreshCw size={13} className={isRefreshing ? 'spin' : ''} />
            Actualizar
          </button>

          {onNewCleanSession && (
            <button
              onClick={onNewCleanSession}
              className="btn btn-secondary"
              title="Abrir directamente una nueva sesión limpia en Live"
              style={{
                padding: '8px 14px',
                fontSize: '12px',
                color: '#58a6ff',
                borderColor: 'rgba(88, 166, 255, 0.4)',
                backgroundColor: 'rgba(88, 166, 255, 0.1)',
                fontWeight: '600',
              }}
            >
              <Plus size={14} />
              Sesión Limpia
            </button>
          )}

          <button
            onClick={() => setIsModalOpen(true)}
            className="btn btn-primary"
            style={{ padding: '8px 16px', fontSize: '12px' }}
          >
            <Plus size={14} />
            Nueva Misión
          </button>
        </div>
      </div>


      {/* Filter and Status Tab Bar */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        flexWrap: 'wrap',
        gap: '14px',
      }}>
        {/* Search */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: '10px',
          padding: '8px 14px',
          backgroundColor: '#121824',
          borderRadius: '8px',
          border: '1px solid #1e293b',
          flex: 1,
          maxWidth: '420px',
        }}>
          <Search size={14} style={{ color: '#64748b' }} />
          <input
            type="text"
            placeholder="Buscar por ID, objetivo, agente o sandbox..."
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            style={{
              background: 'none',
              border: 'none',
              color: '#f1f5f9',
              fontSize: '12px',
              outline: 'none',
              width: '100%',
            }}
          />
        </div>

        {/* Status Filters */}
        <div style={{
          display: 'flex',
          backgroundColor: '#121824',
          borderRadius: '8px',
          border: '1px solid #1e293b',
          padding: '3px',
          gap: '2px',
        }}>
          {[
            { id: 'ALL', label: 'Todas' },
            { id: 'ACTIVE', label: 'Activas' },
            { id: 'PAUSED', label: 'Pausadas' },
            { id: 'COMPLETED', label: 'Completadas' },
          ].map((st) => (
            <button
              key={st.id}
              onClick={() => setStatusFilter(st.id)}
              style={{
                padding: '5px 12px',
                borderRadius: '6px',
                border: 'none',
                background: statusFilter === st.id ? '#1e2a3c' : 'transparent',
                color: statusFilter === st.id ? '#f8fafc' : '#73849c',
                fontSize: '11px',
                fontWeight: '600',
                cursor: 'pointer',
                transition: 'all 0.15s ease',
              }}
            >
              {st.label}
            </button>
          ))}
        </div>
      </div>

      {/* Sessions Grid */}
      {filteredSessions.length > 0 ? (
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(360px, 1fr))',
          gap: '16px',
        }}>
          {filteredSessions.map((s) => {
            const isSelected = s.session_id === currentSessionId;
            const totalDecisions = s.total_decisions || s.steps_count || 0;
            const allowed = s.allowed_count || (s.total_decisions ? Math.max(0, s.total_decisions - (s.blocked_count || 0) - (s.review_count || 0)) : 0);
            const blocked = s.blocked_count || 0;
            const inReview = s.review_count || 0;

            const statusColor =
              s.status === 'Active' ? '#3fb950' :
              s.status === 'Paused' ? '#d29922' :
              s.status === 'Completed' ? '#58a6ff' : '#8b949e';

            return (
              <div
                key={s.session_id}
                style={{
                  backgroundColor: '#121824',
                  border: `1px solid ${isSelected ? '#388bfd' : '#1e293b'}`,
                  borderRadius: '10px',
                  padding: '18px',
                  display: 'flex',
                  flexDirection: 'column',
                  justifyContent: 'space-between',
                  gap: '16px',
                  transition: 'all 0.2s ease',
                  boxShadow: isSelected
                    ? '0 0 14px rgba(56, 139, 253, 0.18)'
                    : 'var(--shadow-clay-sm)',
                }}
              >
                <div>
                  {/* Top line: ID, Status, Date */}
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '10px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                      <span style={{
                        fontSize: '13px',
                        fontWeight: '700',
                        color: isSelected ? '#58a6ff' : '#f0f6fc',
                        fontFamily: 'var(--font-mono)',
                      }}>
                        #{s.session_id}
                      </span>
                      <span
                        style={{
                          fontSize: '10.5px',
                          fontWeight: '600',
                          padding: '2px 8px',
                          borderRadius: '12px',
                          backgroundColor: `${statusColor}18`,
                          color: statusColor,
                          border: `1px solid ${statusColor}40`,
                        }}
                      >
                        {s.status || 'Active'}
                      </span>
                    </div>

                    <span style={{ fontSize: '11px', color: '#64748b' }}>
                      {s.created_at ? new Date(s.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : 'Reciente'}
                    </span>
                  </div>

                  {/* Goal Description */}
                  <p style={{
                    fontSize: '13px',
                    color: '#e2e8f0',
                    lineHeight: '1.45',
                    marginBottom: '14px',
                    display: '-webkit-box',
                    WebkitLineClamp: 3,
                    WebkitBoxOrient: 'vertical',
                    overflow: 'hidden',
                  }}>
                    {s.goal || 'Sin objetivo especificado'}
                  </p>

                  {/* Agent and Sandbox Tags */}
                  <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', marginBottom: '12px' }}>
                    <span style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '4px',
                      fontSize: '11px',
                      color: '#94a3b8',
                      backgroundColor: '#162030',
                      border: '1px solid #1e2c40',
                      borderRadius: '4px',
                      padding: '2px 8px',
                    }}>
                      <Bot size={11} style={{ color: '#58a6ff' }} />
                      {s.agent_name || 'CodingAgent'}
                    </span>

                    <span style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '4px',
                      fontSize: '11px',
                      color: '#94a3b8',
                      backgroundColor: '#162030',
                      border: '1px solid #1e2c40',
                      borderRadius: '4px',
                      padding: '2px 8px',
                    }}>
                      <Shield size={11} style={{ color: s.execution_mode === 'full_access' ? '#f85149' : '#3fb950' }} />
                      {s.execution_mode || 'local_restricted'}
                    </span>

                    {s.workspace_root && (
                      <span style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '4px',
                        fontSize: '11px',
                        color: '#60a5fa',
                        backgroundColor: '#131e33',
                        border: '1px solid #1e3a5f',
                        borderRadius: '4px',
                        padding: '2px 8px',
                        maxWidth: '200px',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }} title={s.workspace_root}>
                        <FolderKanban size={10} />
                        {s.workspace_root.split(/[\\/]/).pop() || s.workspace_root}
                      </span>
                    )}
                  </div>
                </div>

                {/* Bottom Stats & Button */}
                <div style={{
                  borderTop: '1px solid #1a2434',
                  paddingTop: '12px',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                }}>
                  {/* Decision breakdown */}
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '11px', color: '#64748b' }}>
                    <span style={{ color: '#cbd5e1', fontWeight: '600' }}>
                      {totalDecisions} dec.
                    </span>
                    <span>·</span>
                    <span style={{ color: '#3fb950', fontWeight: '500' }}>
                      {allowed} OK
                    </span>
                    {blocked > 0 && (
                      <>
                        <span>·</span>
                        <span style={{ color: '#f85149', fontWeight: '500' }}>
                          {blocked} Bloq.
                        </span>
                      </>
                    )}
                    {inReview > 0 && (
                      <>
                        <span>·</span>
                        <span style={{ color: '#d29922', fontWeight: '500' }}>
                          {inReview} Rev.
                        </span>
                      </>
                    )}
                  </div>

                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    {onDeleteSession && (
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          if (window.confirm(`¿Eliminar permanentemente la sesión #${s.session_id}?`)) {
                            onDeleteSession(s.session_id);
                          }
                        }}
                        title="Eliminar esta sesión"
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          width: '28px',
                          height: '28px',
                          borderRadius: '6px',
                          backgroundColor: '#161c26',
                          border: '1px solid #2a3446',
                          color: '#8b949e',
                          cursor: 'pointer',
                          transition: 'all 0.15s ease',
                        }}
                        onMouseEnter={(e) => {
                          e.currentTarget.style.borderColor = 'rgba(239, 68, 68, 0.6)';
                          e.currentTarget.style.color = '#f87171';
                          e.currentTarget.style.backgroundColor = 'rgba(239, 68, 68, 0.12)';
                        }}
                        onMouseLeave={(e) => {
                          e.currentTarget.style.borderColor = '#2a3446';
                          e.currentTarget.style.color = '#8b949e';
                          e.currentTarget.style.backgroundColor = '#161c26';
                        }}
                      >
                        <Trash2 size={13} />
                      </button>
                    )}

                    <button
                      onClick={() => onSelectSession?.(s.session_id)}
                      className="btn btn-secondary"
                      style={{
                        padding: '5px 12px',
                        fontSize: '11.5px',
                        backgroundColor: isSelected ? '#1e293b' : '#141c28',
                        borderColor: isSelected ? '#58a6ff' : '#243248',
                        color: isSelected ? '#58a6ff' : '#f0f6fc',
                        fontWeight: '600',
                      }}
                    >
                      {isSelected ? 'Inspeccionando' : 'Abrir en Live'}
                      <ArrowRight size={12} />
                    </button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        /* Empty State with Starter Templates */
        <div style={{
          backgroundColor: '#121824',
          borderRadius: '12px',
          border: '1px dashed #243248',
          padding: '40px 24px',
          textAlign: 'center',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          gap: '20px',
        }}>
          <div style={{
            width: '48px',
            height: '48px',
            borderRadius: '12px',
            backgroundColor: '#162030',
            border: '1px solid #24344d',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: '#58a6ff',
          }}>
            <FolderKanban size={24} />
          </div>

          <div>
            <h3 style={{ fontSize: '15px', fontWeight: '700', color: '#f8fafc' }}>
              No se encontraron sesiones {filter ? 'con ese criterio' : 'activas'}
            </h3>
            <p style={{ fontSize: '12px', color: '#73849c', marginTop: '6px', maxWidth: '460px' }}>
              Puedes iniciar una nueva misión supervisada ingresando un objetivo o seleccionando una de las plantillas sugeridas a continuación.
            </p>
          </div>

          {/* Starter Template Cards */}
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
            gap: '12px',
            width: '100%',
            maxWidth: '920px',
            marginTop: '8px',
            textAlign: 'left',
          }}>
            {starterTemplates.map((tmpl, idx) => (
              <div
                key={idx}
                style={{
                  backgroundColor: '#0c111a',
                  border: '1px solid #1a2436',
                  borderRadius: '8px',
                  padding: '16px',
                  display: 'flex',
                  flexDirection: 'column',
                  justifyContent: 'space-between',
                  gap: '12px',
                }}
              >
                <div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '6px' }}>
                    <Sparkles size={13} style={{ color: '#58a6ff' }} />
                    <span style={{ fontSize: '12.5px', fontWeight: '600', color: '#f0f6fc' }}>
                      {tmpl.title}
                    </span>
                  </div>
                  <p style={{ fontSize: '11.5px', color: '#8b949e', lineHeight: '1.4' }}>
                    {tmpl.goal}
                  </p>
                </div>

                <button
                  onClick={() => {
                    onCreateSession?.({
                      goal: tmpl.goal,
                      agent_name: 'CodingAgent',
                      execution_mode: tmpl.mode,
                      llm_provider: tmpl.provider,
                      supervisor: 'laya',
                      max_steps: 25,
                    });
                  }}
                  className="btn btn-secondary"
                  style={{ alignSelf: 'flex-start', padding: '4px 10px', fontSize: '11px' }}
                >
                  <Play size={11} style={{ fill: 'currentColor' }} />
                  Iniciar esta plantilla
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* New Mission Modal */}
      {isModalOpen && (
        <div style={{
          position: 'fixed',
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          backgroundColor: 'rgba(0, 0, 0, 0.75)',
          backdropFilter: 'blur(4px)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          zIndex: 1000,
          padding: '20px',
        }}>
          <div style={{
            backgroundColor: '#111722',
            border: '1px solid #233147',
            borderRadius: '12px',
            width: '100%',
            maxWidth: '560px',
            boxShadow: '0 20px 40px rgba(0, 0, 0, 0.6)',
            overflow: 'hidden',
          }}>
            {/* Modal Header */}
            <div style={{
              padding: '18px 24px',
              borderBottom: '1px solid #1a2436',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <Bot size={18} style={{ color: '#58a6ff' }} />
                <h3 style={{ fontSize: '15px', fontWeight: '700', color: '#f8fafc' }}>
                  Lanzar Nueva Misión Supervisada
                </h3>
              </div>
              <button
                onClick={() => setIsModalOpen(false)}
                style={{
                  background: 'none',
                  border: 'none',
                  color: '#64748b',
                  cursor: 'pointer',
                  fontSize: '18px',
                  padding: '4px',
                }}
              >
                ✕
              </button>
            </div>

            {/* Modal Body Form */}
            <form onSubmit={handleCreateSubmit} style={{ padding: '24px', display: 'flex', flexDirection: 'column', gap: '16px' }}>
              <div>
                <label style={{ fontSize: '12px', fontWeight: '600', color: '#cbd5e1', display: 'block', marginBottom: '6px' }}>
                  Objetivo / Instrucción de la Misión *
                </label>
                <textarea
                  rows={3}
                  required
                  placeholder="Ej: Analizar middleware de autenticación, solucionar vulnerabilidad de sesión y validar con tests unitarios..."
                  value={formData.goal}
                  onChange={(e) => setFormData({ ...formData, goal: e.target.value })}
                  style={{
                    width: '100%',
                    padding: '10px 12px',
                    borderRadius: '8px',
                    backgroundColor: '#0a0e16',
                    border: '1px solid #1e293b',
                    color: '#f8fafc',
                    fontSize: '12px',
                    lineHeight: '1.45',
                    outline: 'none',
                    resize: 'vertical',
                  }}
                />
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '14px' }}>
                <div>
                  <label style={{ fontSize: '11.5px', fontWeight: '600', color: '#cbd5e1', display: 'block', marginBottom: '5px' }}>
                    Agente Autónomo
                  </label>
                  <select
                    value={formData.agent_name}
                    onChange={(e) => setFormData({ ...formData, agent_name: e.target.value })}
                    style={{
                      width: '100%',
                      padding: '8px 10px',
                      borderRadius: '6px',
                      backgroundColor: '#0a0e16',
                      border: '1px solid #1e293b',
                      color: '#f8fafc',
                      fontSize: '12px',
                      outline: 'none',
                    }}
                  >
                    <option value="CodingAgent">CodingAgent (ReAct + Tools)</option>
                    <option value="SecurityAuditAgent">SecurityAuditAgent</option>
                    <option value="ExplorerAgent">ExplorerAgent</option>
                  </select>
                </div>

                <div>
                  <label style={{ fontSize: '11.5px', fontWeight: '600', color: '#cbd5e1', display: 'block', marginBottom: '5px' }}>
                    Aislamiento Físico Sandbox
                  </label>
                  <select
                    value={formData.execution_mode}
                    onChange={(e) => setFormData({ ...formData, execution_mode: e.target.value })}
                    style={{
                      width: '100%',
                      padding: '8px 10px',
                      borderRadius: '6px',
                      backgroundColor: '#0a0e16',
                      border: '1px solid #1e293b',
                      color: '#f8fafc',
                      fontSize: '12px',
                      outline: 'none',
                    }}
                  >
                    <option value="local_restricted">Local Confinado (Recomendado)</option>
                    <option value="container">Contenedor Docker</option>
                    <option value="full_access">Direct Host (Requiere confirmación)</option>
                  </select>
                </div>
              </div>

              <div>
                <label style={{ fontSize: '11.5px', fontWeight: '600', color: '#cbd5e1', display: 'flex', justifyContent: 'space-between', marginBottom: '5px' }}>
                  <span>Ruta Raíz del Espacio de Trabajo (Workspace Root)</span>
                  <span style={{ fontSize: '10px', color: '#64748b' }}>Opcional</span>
                </label>
                <input
                  type="text"
                  value={formData.workspace_root || ''}
                  onChange={(e) => setFormData({ ...formData, workspace_root: e.target.value })}
                  placeholder="Ruta base del proyecto (dejar vacío para auto-detectar)"
                  style={{
                    width: '100%',
                    padding: '8px 10px',
                    borderRadius: '6px',
                    backgroundColor: '#0a0e16',
                    border: '1px solid #1e293b',
                    color: '#f8fafc',
                    fontSize: '12px',
                    fontFamily: 'var(--font-mono)',
                    outline: 'none',
                  }}
                />
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '14px' }}>
                <div>
                  <label style={{ fontSize: '11.5px', fontWeight: '600', color: '#cbd5e1', display: 'block', marginBottom: '5px' }}>
                    Proveedor de Inferencia LLM
                  </label>
                  <select
                    value={formData.llm_provider}
                    onChange={(e) => setFormData({ ...formData, llm_provider: e.target.value })}
                    style={{
                      width: '100%',
                      padding: '8px 10px',
                      borderRadius: '6px',
                      backgroundColor: '#0a0e16',
                      border: '1px solid #1e293b',
                      color: '#f8fafc',
                      fontSize: '12px',
                      outline: 'none',
                    }}
                  >
                    <option value="simulator">Simulador Determinista</option>
                    <option value="ollama">Ollama Local (http://localhost:11434)</option>
                    <option value="groq">Groq Cloud (Llama 3.3 / Qwen)</option>
                    <option value="openai">OpenAI (GPT-4o)</option>
                    <option value="gemini">Google Gemini</option>
                  </select>
                </div>

                <div>
                  <label style={{ fontSize: '11.5px', fontWeight: '600', color: '#cbd5e1', display: 'block', marginBottom: '5px' }}>
                    Supervisor Cognitivo
                  </label>
                  <select
                    value={formData.supervisor}
                    onChange={(e) => setFormData({ ...formData, supervisor: e.target.value })}
                    style={{
                      width: '100%',
                      padding: '8px 10px',
                      borderRadius: '6px',
                      backgroundColor: '#0a0e16',
                      border: '1px solid #1e293b',
                      color: '#f8fafc',
                      fontSize: '12px',
                      outline: 'none',
                    }}
                  >
                    <option value="laya">LAYA (Fast-Path System-1 Local)</option>
                    <option value="typesafe">TypeSafe AI (Deep Escalation)</option>
                  </select>
                </div>
              </div>

              <div>
                <label style={{ fontSize: '11.5px', fontWeight: '600', color: '#cbd5e1', display: 'block', marginBottom: '5px' }}>
                  Límite Máximo de Pasos ({formData.max_steps})
                </label>
                <input
                  type="range"
                  min="5"
                  max="100"
                  step="5"
                  value={formData.max_steps}
                  onChange={(e) => setFormData({ ...formData, max_steps: Number(e.target.value) })}
                  style={{ width: '100%', accentColor: '#388bfd', cursor: 'pointer' }}
                />
              </div>

              {/* Modal Footer */}
              <div style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'flex-end',
                gap: '10px',
                marginTop: '10px',
                borderTop: '1px solid #1a2436',
                paddingTop: '16px',
              }}>
                <button
                  type="button"
                  onClick={() => setIsModalOpen(false)}
                  className="btn btn-secondary"
                  style={{ padding: '8px 16px' }}
                >
                  Cancelar
                </button>
                <button
                  type="submit"
                  className="btn btn-primary"
                  style={{ padding: '8px 20px' }}
                >
                  <Play size={13} style={{ fill: 'currentColor' }} />
                  Iniciar Supervisión
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
