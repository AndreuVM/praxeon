import React, { useState, useEffect } from 'react';
import {
  Bot,
  RefreshCw,
  Plus,
  Edit2,
  Trash2,
  History,
  GitCommit,
  X,
} from 'lucide-react';
import {
  fetchAgents,
  createAgent,
  updateAgent,
  deleteAgent,
  createAgentVersion,
  fetchAgentVersions,
} from '../../services/api';

export default function AgentsView({
  session: _session = {},
  sessionsList: _sessionsList = [],
  missionConfig: _missionConfig = {},
  isRunning: _isRunning = false,
  isPaused: _isPaused = false,
  onSelectSession: _onSelectSession,
  onLaunchAgentMission: _onLaunchAgentMission,
}) {
  const [agents, setAgents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [_error, setError] = useState(null);
  const [selectedAgentId, setSelectedAgentId] = useState(null);
  const [searchFilter, setSearchFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('');

  // Modales
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showEditModal, setShowEditModal] = useState(false);
  const [showVersionModal, setShowVersionModal] = useState(false);
  const [showHistoryModal, setShowHistoryModal] = useState(false);

  // Estados de edición / formularios
  const [activeAgent, setActiveAgent] = useState(null);
  const [versionHistory, setVersionHistory] = useState([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);
  const [formError, setFormError] = useState(null);

  // Formulario nuevo agente
  const [formData, setFormData] = useState({
    agent_id: '',
    name: '',
    role: 'Developer',
    description: '',
    system_prompt: '',
    provider: 'gemini',
    model_name: 'gemini-1.5-pro',
    temperature: 0.2,
    max_risk_level: 'MEDIUM',
    require_human_confirmation: false,
    allowed_tools: '*',
    forbidden_tools: '',
    capabilities: 'code_editing, testing',
    skills: '',
    tags: '',
  });

  const loadAgents = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchAgents();
      if (res && res.data) {
        setAgents(res.data);
        if (!selectedAgentId && res.data.length > 0) {
          setSelectedAgentId(res.data[0].agent_id);
        }
      }
    } catch (err) {
      setError(err.message || 'Error al cargar catálogo de agentes');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        const res = await fetchAgents();
        if (mounted && res && res.data) {
          setAgents(res.data);
          setSelectedAgentId((prev) => prev || (res.data.length > 0 ? res.data[0].agent_id : null));
        }
      } catch (err) {
        if (mounted) setError(err.message || 'Error al cargar catálogo de agentes');
      } finally {
        if (mounted) setLoading(false);
      }
    })();
    return () => {
      mounted = false;
    };
  }, []);

  const selectedAgent = agents.find((a) => a.agent_id === selectedAgentId) || agents[0];

  const handleOpenCreate = () => {
    setFormData({
      agent_id: '',
      name: '',
      role: 'Developer',
      description: '',
      system_prompt: 'Eres un agente autónomo de desarrollo...',
      provider: 'gemini',
      model_name: 'gemini-1.5-pro',
      temperature: 0.2,
      max_risk_level: 'MEDIUM',
      require_human_confirmation: false,
      allowed_tools: '*',
      forbidden_tools: '',
      capabilities: 'code_editing, analysis',
      skills: '',
      tags: 'custom',
    });
    setFormError(null);
    setShowCreateModal(true);
  };

  const handleOpenEdit = (agent) => {
    setActiveAgent(agent);
    setFormData({
      name: agent.name || '',
      role: agent.role || 'Developer',
      description: agent.description || '',
      system_prompt: agent.system_prompt || '',
      provider: agent.model?.provider || 'gemini',
      model_name: agent.model?.model_name || 'gemini-1.5-pro',
      temperature: agent.model?.temperature ?? 0.2,
      max_risk_level: agent.risk_profile?.max_risk_level || 'MEDIUM',
      require_human_confirmation: Boolean(agent.risk_profile?.require_human_confirmation),
      allowed_tools: (agent.allowed_tools || []).join(', '),
      forbidden_tools: (agent.forbidden_tools || []).join(', '),
      capabilities: (agent.capabilities || []).join(', '),
      skills: (agent.skills || []).join(', '),
      tags: Array.isArray(agent.metadata?.tags) ? agent.metadata.tags.join(', ') : '',
    });
    setFormError(null);
    setShowEditModal(true);
  };

  const handleOpenVersion = (agent) => {
    setActiveAgent(agent);
    setFormData({
      name: agent.name,
      role: agent.role,
      description: agent.description || '',
      system_prompt: agent.system_prompt || '',
      provider: agent.model?.provider || 'gemini',
      model_name: agent.model?.model_name || 'gemini-1.5-pro',
      temperature: agent.model?.temperature ?? 0.2,
      max_risk_level: agent.risk_profile?.max_risk_level || 'MEDIUM',
      require_human_confirmation: Boolean(agent.risk_profile?.require_human_confirmation),
      allowed_tools: (agent.allowed_tools || []).join(', '),
      forbidden_tools: (agent.forbidden_tools || []).join(', '),
      capabilities: (agent.capabilities || []).join(', '),
      skills: (agent.skills || []).join(', '),
      tags: Array.isArray(agent.metadata?.tags) ? agent.metadata.tags.join(', ') : '',
    });
    setFormError(null);
    setShowVersionModal(true);
  };

  const handleOpenHistory = async (agent) => {
    setActiveAgent(agent);
    setShowHistoryModal(true);
    setHistoryLoading(true);
    try {
      const res = await fetchAgentVersions(agent.agent_id);
      setVersionHistory(res.data || []);
    } catch {
      setVersionHistory([]);
    } finally {
      setHistoryLoading(false);
    }
  };

  const parseCsv = (str) =>
    (str || '')
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean);

  const handleSubmitCreate = async (e) => {
    e.preventDefault();
    setActionLoading(true);
    setFormError(null);
    try {
      const payload = {
        name: formData.name.trim(),
        role: formData.role.trim(),
        description: formData.description.trim(),
        system_prompt: formData.system_prompt.trim(),
        model: {
          provider: formData.provider,
          model_name: formData.model_name,
          temperature: parseFloat(formData.temperature) || 0.2,
        },
        risk_profile: {
          max_risk_level: formData.max_risk_level,
          require_human_confirmation: formData.require_human_confirmation,
        },
        allowed_tools: parseCsv(formData.allowed_tools),
        forbidden_tools: parseCsv(formData.forbidden_tools),
        capabilities: parseCsv(formData.capabilities),
        skills: parseCsv(formData.skills),
        metadata: {
          tags: parseCsv(formData.tags),
        },
      };
      if (formData.agent_id && formData.agent_id.trim()) {
        payload.agent_id = formData.agent_id.trim();
      }

      const res = await createAgent(payload);
      setShowCreateModal(false);
      await loadAgents();
      if (res.data?.agent_id) {
        setSelectedAgentId(res.data.agent_id);
      }
    } catch (err) {
      setFormError(err.message || 'Error al registrar el agente');
    } finally {
      setActionLoading(false);
    }
  };

  const handleSubmitEdit = async (e) => {
    e.preventDefault();
    if (!activeAgent) return;
    setActionLoading(true);
    setFormError(null);
    try {
      const updates = {
        name: formData.name.trim(),
        role: formData.role.trim(),
        description: formData.description.trim(),
        system_prompt: formData.system_prompt.trim(),
        model: {
          provider: formData.provider,
          model_name: formData.model_name,
          temperature: parseFloat(formData.temperature) || 0.2,
        },
        risk_profile: {
          max_risk_level: formData.max_risk_level,
          require_human_confirmation: formData.require_human_confirmation,
        },
        allowed_tools: parseCsv(formData.allowed_tools),
        forbidden_tools: parseCsv(formData.forbidden_tools),
        capabilities: parseCsv(formData.capabilities),
        skills: parseCsv(formData.skills),
        metadata: {
          tags: parseCsv(formData.tags),
        },
      };

      await updateAgent(activeAgent.agent_id, updates);
      setShowEditModal(false);
      await loadAgents();
    } catch (err) {
      setFormError(err.message || 'Error al actualizar el agente');
    } finally {
      setActionLoading(false);
    }
  };

  const handleSubmitVersion = async (e) => {
    e.preventDefault();
    if (!activeAgent) return;
    setActionLoading(true);
    setFormError(null);
    try {
      const versionData = {
        system_prompt: formData.system_prompt.trim(),
        description: formData.description.trim(),
        role: formData.role.trim(),
        model: {
          provider: formData.provider,
          model_name: formData.model_name,
          temperature: parseFloat(formData.temperature) || 0.2,
        },
        risk_profile: {
          max_risk_level: formData.max_risk_level,
          require_human_confirmation: formData.require_human_confirmation,
        },
        capabilities: parseCsv(formData.capabilities),
        skills: parseCsv(formData.skills),
      };

      await createAgentVersion(activeAgent.agent_id, versionData);
      setShowVersionModal(false);
      await loadAgents();
    } catch (err) {
      setFormError(err.message || 'Error al versionar agente');
    } finally {
      setActionLoading(false);
    }
  };

  const handleDeleteAgent = async (agent, hardDelete = false) => {
    const modeDesc = hardDelete ? 'eliminar permanentemente' : 'dar de baja lógica (TERMINATED)';
    if (!window.confirm(`¿Estás seguro de que deseas ${modeDesc} al agente '${agent.name}'?`)) {
      return;
    }
    try {
      await deleteAgent(agent.agent_id, hardDelete);
      await loadAgents();
    } catch (err) {
      alert(`Error al eliminar agente: ${err.message}`);
    }
  };

  // Filtrado de agentes
  const filteredAgents = agents.filter((a) => {
    const matchesSearch =
      !searchFilter ||
      a.name.toLowerCase().includes(searchFilter.toLowerCase()) ||
      a.agent_id.toLowerCase().includes(searchFilter.toLowerCase()) ||
      a.role.toLowerCase().includes(searchFilter.toLowerCase()) ||
      (a.capabilities || []).some((c) => c.toLowerCase().includes(searchFilter.toLowerCase()));

    const matchesStatus = !statusFilter || a.status === statusFilter;
    return matchesSearch && matchesStatus;
  });

  const getStatusBadge = (status) => {
    switch (status) {
      case 'ACTIVE':
        return { label: 'Activo', bg: 'rgba(63, 185, 80, 0.15)', text: '#3fb950', border: 'rgba(63, 185, 80, 0.4)' };
      case 'INACTIVE':
        return { label: 'Inactivo', bg: 'rgba(139, 148, 158, 0.15)', text: '#8b949e', border: 'rgba(139, 148, 158, 0.4)' };
      case 'QUARANTINED':
        return { label: 'Cuarentena', bg: 'rgba(248, 81, 73, 0.15)', text: '#f85149', border: 'rgba(248, 81, 73, 0.4)' };
      case 'TERMINATED':
        return { label: 'Deprecado', bg: 'rgba(210, 153, 34, 0.15)', text: '#d29922', border: 'rgba(210, 153, 34, 0.4)' };
      default:
        return { label: status, bg: 'rgba(139, 148, 158, 0.1)', text: '#8b949e', border: 'rgba(139, 148, 158, 0.3)' };
    }
  };

  return (
    <div
      style={{
        flex: 1,
        padding: '24px 32px',
        overflowY: 'auto',
        backgroundColor: '#0a0e16',
        display: 'flex',
        flexDirection: 'column',
        gap: '24px',
        color: '#f8fafc',
      }}
    >
      {/* Header & Acciones de Catálogo */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '16px' }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <div
              style={{
                width: '34px',
                height: '34px',
                borderRadius: '8px',
                backgroundColor: '#161c28',
                border: '1px solid #243044',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                color: '#c084fc',
              }}
            >
              <Bot size={20} />
            </div>
            <h1 style={{ fontSize: '19px', fontWeight: '700', color: '#f8fafc', letterSpacing: '-0.01em' }}>
              Catálogo de Agentes Autónomos (AgentRegistry)
            </h1>
          </div>
          <p style={{ fontSize: '12px', color: '#73849c', marginTop: '4px' }}>
            Gestión completa del ciclo de vida, versionado criptográfico inmutable SHA-256 y políticas de confinamiento.
          </p>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <button
            onClick={loadAgents}
            disabled={loading}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              padding: '7px 12px',
              borderRadius: '6px',
              backgroundColor: '#161c28',
              border: '1px solid #243044',
              color: '#94a3b8',
              fontSize: '12px',
              fontWeight: '500',
              cursor: 'pointer',
            }}
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
            <span>Refrescar</span>
          </button>

          <button
            onClick={handleOpenCreate}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              padding: '7px 14px',
              borderRadius: '6px',
              backgroundColor: '#238636',
              border: '1px solid rgba(255, 255, 255, 0.1)',
              color: '#ffffff',
              fontSize: '12.5px',
              fontWeight: '600',
              cursor: 'pointer',
              boxShadow: '0 2px 8px rgba(35, 134, 54, 0.3)',
            }}
          >
            <Plus size={15} />
            <span>Registrar Agente</span>
          </button>
        </div>
      </div>

      {/* Barra de Filtros */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          backgroundColor: '#121824',
          borderRadius: '8px',
          border: '1px solid #1e293b',
          padding: '10px 14px',
          gap: '12px',
          flexWrap: 'wrap',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flex: 1, minWidth: '240px' }}>
          <input
            type="text"
            placeholder="Buscar agente por nombre, ID, rol o capacidades..."
            value={searchFilter}
            onChange={(e) => setSearchFilter(e.target.value)}
            style={{
              flex: 1,
              backgroundColor: '#0c1017',
              border: '1px solid #243044',
              borderRadius: '6px',
              padding: '6px 12px',
              fontSize: '12px',
              color: '#f8fafc',
              outline: 'none',
            }}
          />
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span style={{ fontSize: '11.5px', color: '#73849c' }}>Estado:</span>
          {['', 'ACTIVE', 'INACTIVE', 'QUARANTINED', 'TERMINATED'].map((st) => (
            <button
              key={st}
              onClick={() => setStatusFilter(st)}
              style={{
                fontSize: '11px',
                fontWeight: statusFilter === st ? '700' : '500',
                padding: '3px 8px',
                borderRadius: '4px',
                backgroundColor: statusFilter === st ? '#1e293b' : 'transparent',
                color: statusFilter === st ? '#38bdf8' : '#73849c',
                border: `1px solid ${statusFilter === st ? '#38bdf8' : 'transparent'}`,
                cursor: 'pointer',
              }}
            >
              {st === '' ? 'Todos' : st}
            </button>
          ))}
        </div>
      </div>

      {/* Grid Principal: Lista de Agentes vs Detalle Activo */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'minmax(320px, 1fr) minmax(420px, 1.2fr)',
          gap: '20px',
          alignItems: 'start',
        }}
      >
        {/* Columna Izquierda: Tarjetas de Agentes */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <span style={{ fontSize: '12.5px', fontWeight: '700', color: '#94a3b8' }}>
              Agentes Registrados ({filteredAgents.length})
            </span>
          </div>

          {loading ? (
            <div style={{ padding: '30px', textAlign: 'center', color: '#73849c', fontSize: '12px' }}>
              Cargando catálogo desde el servidor...
            </div>
          ) : filteredAgents.length === 0 ? (
            <div
              style={{
                backgroundColor: '#121824',
                borderRadius: '8px',
                border: '1px solid #1e293b',
                padding: '30px',
                textAlign: 'center',
                color: '#73849c',
                fontSize: '12.5px',
              }}
            >
              No se encontraron agentes coincidentes con los filtros.
            </div>
          ) : (
            filteredAgents.map((agent) => {
              const isSelected = selectedAgent?.agent_id === agent.agent_id;
              const badge = getStatusBadge(agent.status);

              return (
                <div
                  key={agent.agent_id}
                  onClick={() => setSelectedAgentId(agent.agent_id)}
                  style={{
                    backgroundColor: isSelected ? '#162234' : '#121824',
                    borderRadius: '8px',
                    border: `1px solid ${isSelected ? '#388bfd' : '#1e293b'}`,
                    padding: '14px 16px',
                    cursor: 'pointer',
                    transition: 'all 0.15s ease',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '8px',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                      <span style={{ fontSize: '13.5px', fontWeight: '700', color: isSelected ? '#58a6ff' : '#f0f6fc' }}>
                        {agent.name}
                      </span>
                      <span
                        style={{
                          fontSize: '10px',
                          fontWeight: '700',
                          padding: '1px 5px',
                          borderRadius: '4px',
                          backgroundColor: '#1e293b',
                          color: '#38bdf8',
                          fontFamily: 'var(--font-mono)',
                        }}
                      >
                        v{agent.version}
                      </span>
                    </div>

                    <span
                      style={{
                        fontSize: '10px',
                        fontWeight: '700',
                        padding: '2px 7px',
                        borderRadius: '10px',
                        backgroundColor: badge.bg,
                        color: badge.text,
                        border: `1px solid ${badge.border}`,
                      }}
                    >
                      {badge.label}
                    </span>
                  </div>

                  <div style={{ fontSize: '11px', color: '#73849c' }}>
                    <span style={{ color: '#94a3b8', fontWeight: '600' }}>{agent.role}</span> ·{' '}
                    <span>{agent.model?.provider || 'gemini'} / {agent.model?.model_name || 'gemini-1.5-pro'}</span>
                  </div>

                  {agent.description && (
                    <p style={{ fontSize: '11.5px', color: '#8b949e', margin: 0, lineHeight: '1.4' }}>
                      {agent.description}
                    </p>
                  )}

                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: '4px' }}>
                    <span style={{ fontSize: '10px', color: '#64748b', fontFamily: 'var(--font-mono)' }}>
                      #{agent.definition_hash?.slice(0, 8)}
                    </span>

                    <div style={{ display: 'flex', gap: '4px' }}>
                      {(agent.capabilities || []).slice(0, 2).map((c) => (
                        <span
                          key={c}
                          style={{
                            fontSize: '9.5px',
                            backgroundColor: 'rgba(56, 189, 248, 0.1)',
                            color: '#38bdf8',
                            padding: '1px 5px',
                            borderRadius: '3px',
                          }}
                        >
                          {c}
                        </span>
                      ))}
                    </div>
                  </div>
                </div>
              );
            })
          )}
        </div>

        {/* Columna Derecha: Detalle Completo del Agente Seleccionado */}
        {selectedAgent ? (
          <div
            style={{
              backgroundColor: '#121824',
              borderRadius: '10px',
              border: '1px solid #1e293b',
              padding: '20px',
              display: 'flex',
              flexDirection: 'column',
              gap: '18px',
            }}
          >
            {/* Top Bar Detalle */}
            <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '12px' }}>
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                  <h2 style={{ fontSize: '17px', fontWeight: '700', color: '#f8fafc', margin: 0 }}>
                    {selectedAgent.name}
                  </h2>
                  <span
                    style={{
                      fontSize: '11px',
                      fontWeight: '700',
                      padding: '2px 8px',
                      borderRadius: '12px',
                      ...getStatusBadge(selectedAgent.status),
                    }}
                  >
                    {selectedAgent.status}
                  </span>
                  <span
                    style={{
                      fontSize: '11px',
                      fontWeight: '700',
                      padding: '2px 8px',
                      borderRadius: '4px',
                      backgroundColor: '#1e293b',
                      color: '#38bdf8',
                      fontFamily: 'var(--font-mono)',
                    }}
                  >
                    v{selectedAgent.version}
                  </span>
                </div>
                <div style={{ fontSize: '11.5px', color: '#73849c', marginTop: '4px' }}>
                  ID: <span style={{ fontFamily: 'var(--font-mono)', color: '#94a3b8' }}>{selectedAgent.agent_id}</span> ·
                  Fingerprint:{' '}
                  <span style={{ fontFamily: 'var(--font-mono)', color: '#38bdf8' }}>
                    {selectedAgent.definition_hash}
                  </span>
                </div>
              </div>

              {/* Botones de acción */}
              <div style={{ display: 'flex', gap: '6px' }}>
                <button
                  onClick={() => handleOpenHistory(selectedAgent)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '5px',
                    padding: '5px 10px',
                    borderRadius: '5px',
                    backgroundColor: '#161c28',
                    border: '1px solid #243044',
                    color: '#94a3b8',
                    fontSize: '11.5px',
                    cursor: 'pointer',
                  }}
                  title="Ver trazabilidad de versiones"
                >
                  <History size={13} />
                  <span>Versiones</span>
                </button>

                <button
                  onClick={() => handleOpenVersion(selectedAgent)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '5px',
                    padding: '5px 10px',
                    borderRadius: '5px',
                    backgroundColor: '#162234',
                    border: '1px solid #2a3c5a',
                    color: '#38bdf8',
                    fontSize: '11.5px',
                    cursor: 'pointer',
                  }}
                  title="Crear nueva versión inmutable"
                >
                  <GitCommit size={13} />
                  <span>Versionar</span>
                </button>

                <button
                  onClick={() => handleOpenEdit(selectedAgent)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '5px',
                    padding: '5px 10px',
                    borderRadius: '5px',
                    backgroundColor: '#1e293b',
                    border: '1px solid #334155',
                    color: '#f8fafc',
                    fontSize: '11.5px',
                    cursor: 'pointer',
                  }}
                  title="Editar configuración"
                >
                  <Edit2 size={13} />
                  <span>Editar</span>
                </button>

                <button
                  onClick={() => handleDeleteAgent(selectedAgent, false)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '5px',
                    padding: '5px 8px',
                    borderRadius: '5px',
                    backgroundColor: 'rgba(248, 81, 73, 0.1)',
                    border: '1px solid rgba(248, 81, 73, 0.3)',
                    color: '#f85149',
                    fontSize: '11.5px',
                    cursor: 'pointer',
                  }}
                  title="Eliminar o dar de baja"
                >
                  <Trash2 size={13} />
                </button>
              </div>
            </div>

            {/* Directiva raíz / System Prompt */}
            <div>
              <span style={{ fontSize: '11.5px', fontWeight: '700', color: '#94a3b8', display: 'block', marginBottom: '6px' }}>
                Directiva Raíz (System Prompt):
              </span>
              <div
                style={{
                  backgroundColor: '#0c1017',
                  borderRadius: '6px',
                  border: '1px solid #1a2333',
                  padding: '12px',
                  fontFamily: 'var(--font-mono)',
                  fontSize: '11.5px',
                  color: '#cbd5e1',
                  maxHeight: '140px',
                  overflowY: 'auto',
                  whiteSpace: 'pre-wrap',
                  lineHeight: '1.45',
                }}
              >
                {selectedAgent.system_prompt || 'Sin directiva configurada.'}
              </div>
            </div>

            {/* Configuración de Inferencia y Riesgo */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '12px' }}>
              <div style={{ backgroundColor: '#0c1017', borderRadius: '6px', border: '1px solid #1a2333', padding: '10px 12px' }}>
                <span style={{ fontSize: '11px', color: '#64748b', display: 'block' }}>Motor de Inferencia</span>
                <span style={{ fontSize: '13px', fontWeight: '600', color: '#f8fafc', marginTop: '2px', display: 'block' }}>
                  {selectedAgent.model?.provider} / {selectedAgent.model?.model_name}
                </span>
                <span style={{ fontSize: '10.5px', color: '#73849c', marginTop: '2px', display: 'block' }}>
                  Temp: {selectedAgent.model?.temperature ?? 0.2} · Max Tokens: {selectedAgent.model?.max_output_tokens ?? 4096}
                </span>
              </div>

              <div style={{ backgroundColor: '#0c1017', borderRadius: '6px', border: '1px solid #1a2333', padding: '10px 12px' }}>
                <span style={{ fontSize: '11px', color: '#64748b', display: 'block' }}>Perfil de Riesgo y HITL</span>
                <span style={{ fontSize: '13px', fontWeight: '600', color: '#d29922', marginTop: '2px', display: 'block' }}>
                  Nivel Máximo: {selectedAgent.risk_profile?.max_risk_level || 'MEDIUM'}
                </span>
                <span style={{ fontSize: '10.5px', color: '#73849c', marginTop: '2px', display: 'block' }}>
                  Aprobación Humana:{' '}
                  {selectedAgent.risk_profile?.require_human_confirmation ? 'Obligatoria' : 'Automática'}
                </span>
              </div>
            </div>

            {/* Listas de Herramientas y Capacidades */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
              <div>
                <span style={{ fontSize: '11px', fontWeight: '600', color: '#73849c', display: 'block', marginBottom: '4px' }}>
                  Herramientas Autorizadas:
                </span>
                <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
                  {(selectedAgent.allowed_tools || []).map((t) => (
                    <span
                      key={t}
                      style={{
                        fontSize: '11px',
                        backgroundColor: 'rgba(63, 185, 80, 0.1)',
                        border: '1px solid rgba(63, 185, 80, 0.3)',
                        color: '#3fb950',
                        padding: '2px 8px',
                        borderRadius: '4px',
                        fontFamily: 'var(--font-mono)',
                      }}
                    >
                      {t}
                    </span>
                  ))}
                </div>
              </div>

              <div>
                <span style={{ fontSize: '11px', fontWeight: '600', color: '#73849c', display: 'block', marginBottom: '4px' }}>
                  Capacidades del Runtime:
                </span>
                <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
                  {(selectedAgent.capabilities || []).map((c) => (
                    <span
                      key={c}
                      style={{
                        fontSize: '11px',
                        backgroundColor: 'rgba(56, 189, 248, 0.1)',
                        border: '1px solid rgba(56, 189, 248, 0.3)',
                        color: '#38bdf8',
                        padding: '2px 8px',
                        borderRadius: '4px',
                      }}
                    >
                      {c}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          </div>
        ) : (
          <div style={{ color: '#73849c', textAlign: 'center', padding: '40px' }}>
            Selecciona un agente del catálogo para inspeccionar su configuración canónica.
          </div>
        )}
      </div>

      {/* MODAL: Registrar Nuevo Agente / Editar Agente */}
      {(showCreateModal || showEditModal || showVersionModal) && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            backgroundColor: 'rgba(0, 0, 0, 0.75)',
            backdropFilter: 'blur(4px)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 1000,
            padding: '20px',
          }}
        >
          <div
            style={{
              backgroundColor: '#121824',
              border: '1px solid #233147',
              borderRadius: '12px',
              width: '100%',
              maxWidth: '620px',
              maxHeight: '90vh',
              overflowY: 'auto',
              padding: '24px',
              display: 'flex',
              flexDirection: 'column',
              gap: '16px',
              boxShadow: '0 20px 40px rgba(0, 0, 0, 0.6)',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <Bot size={18} style={{ color: '#38bdf8' }} />
                <h3 style={{ fontSize: '16px', fontWeight: '700', color: '#f8fafc', margin: 0 }}>
                  {showCreateModal
                    ? 'Registrar Nuevo Agente'
                    : showVersionModal
                    ? `Nueva Versión Inmutable (${activeAgent?.agent_id})`
                    : `Editar Agente (${activeAgent?.agent_id})`}
                </h3>
              </div>
              <button
                onClick={() => {
                  setShowCreateModal(false);
                  setShowEditModal(false);
                  setShowVersionModal(false);
                }}
                style={{ background: 'none', border: 'none', color: '#94a3b8', cursor: 'pointer' }}
              >
                <X size={18} />
              </button>
            </div>

            {formError && (
              <div
                style={{
                  backgroundColor: 'rgba(248, 81, 73, 0.15)',
                  border: '1px solid rgba(248, 81, 73, 0.4)',
                  borderRadius: '6px',
                  padding: '8px 12px',
                  color: '#f85149',
                  fontSize: '12px',
                }}
              >
                {formError}
              </div>
            )}

            <form
              onSubmit={
                showCreateModal
                  ? handleSubmitCreate
                  : showVersionModal
                  ? handleSubmitVersion
                  : handleSubmitEdit
              }
              style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}
            >
              {showCreateModal && (
                <div>
                  <label style={{ fontSize: '11px', color: '#94a3b8', display: 'block', marginBottom: '4px' }}>
                    Agent ID (opcional, autogenerado si se omite):
                  </label>
                  <input
                    type="text"
                    value={formData.agent_id}
                    onChange={(e) => setFormData({ ...formData, agent_id: e.target.value })}
                    placeholder="ej. ag_qa_specialist"
                    style={{
                      width: '100%',
                      backgroundColor: '#0c1017',
                      border: '1px solid #243044',
                      borderRadius: '6px',
                      padding: '7px 10px',
                      fontSize: '12px',
                      color: '#f8fafc',
                    }}
                  />
                </div>
              )}

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px' }}>
                <div>
                  <label style={{ fontSize: '11px', color: '#94a3b8', display: 'block', marginBottom: '4px' }}>
                    Nombre del Agente:
                  </label>
                  <input
                    type="text"
                    required
                    value={formData.name}
                    onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                    placeholder="ej. Auditor de Seguridad"
                    style={{
                      width: '100%',
                      backgroundColor: '#0c1017',
                      border: '1px solid #243044',
                      borderRadius: '6px',
                      padding: '7px 10px',
                      fontSize: '12px',
                      color: '#f8fafc',
                    }}
                  />
                </div>

                <div>
                  <label style={{ fontSize: '11px', color: '#94a3b8', display: 'block', marginBottom: '4px' }}>
                    Rol Funcional:
                  </label>
                  <input
                    type="text"
                    required
                    value={formData.role}
                    onChange={(e) => setFormData({ ...formData, role: e.target.value })}
                    placeholder="ej. Security Auditor"
                    style={{
                      width: '100%',
                      backgroundColor: '#0c1017',
                      border: '1px solid #243044',
                      borderRadius: '6px',
                      padding: '7px 10px',
                      fontSize: '12px',
                      color: '#f8fafc',
                    }}
                  />
                </div>
              </div>

              <div>
                <label style={{ fontSize: '11px', color: '#94a3b8', display: 'block', marginBottom: '4px' }}>
                  Descripción Operacional:
                </label>
                <input
                  type="text"
                  value={formData.description}
                  onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                  placeholder="Propósito funcional del agente"
                  style={{
                    width: '100%',
                    backgroundColor: '#0c1017',
                    border: '1px solid #243044',
                    borderRadius: '6px',
                    padding: '7px 10px',
                    fontSize: '12px',
                    color: '#f8fafc',
                  }}
                />
              </div>

              <div>
                <label style={{ fontSize: '11px', color: '#94a3b8', display: 'block', marginBottom: '4px' }}>
                  Directiva Raíz (System Prompt):
                </label>
                <textarea
                  required
                  rows={4}
                  value={formData.system_prompt}
                  onChange={(e) => setFormData({ ...formData, system_prompt: e.target.value })}
                  placeholder="Instrucciones maestras de razonamiento y contención..."
                  style={{
                    width: '100%',
                    backgroundColor: '#0c1017',
                    border: '1px solid #243044',
                    borderRadius: '6px',
                    padding: '8px 10px',
                    fontSize: '11.5px',
                    fontFamily: 'var(--font-mono)',
                    color: '#f8fafc',
                  }}
                />
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '10px' }}>
                <div>
                  <label style={{ fontSize: '11px', color: '#94a3b8', display: 'block', marginBottom: '4px' }}>
                    Proveedor:
                  </label>
                  <select
                    value={formData.provider}
                    onChange={(e) => setFormData({ ...formData, provider: e.target.value })}
                    style={{
                      width: '100%',
                      backgroundColor: '#0c1017',
                      border: '1px solid #243044',
                      borderRadius: '6px',
                      padding: '7px 10px',
                      fontSize: '12px',
                      color: '#f8fafc',
                    }}
                  >
                    <option value="gemini">Gemini</option>
                    <option value="openai">OpenAI</option>
                    <option value="anthropic">Anthropic</option>
                    <option value="ollama">Ollama</option>
                    <option value="mock">Mock Provider</option>
                  </select>
                </div>

                <div>
                  <label style={{ fontSize: '11px', color: '#94a3b8', display: 'block', marginBottom: '4px' }}>
                    Modelo:
                  </label>
                  <input
                    type="text"
                    value={formData.model_name}
                    onChange={(e) => setFormData({ ...formData, model_name: e.target.value })}
                    placeholder="gemini-1.5-pro"
                    style={{
                      width: '100%',
                      backgroundColor: '#0c1017',
                      border: '1px solid #243044',
                      borderRadius: '6px',
                      padding: '7px 10px',
                      fontSize: '12px',
                      color: '#f8fafc',
                    }}
                  />
                </div>

                <div>
                  <label style={{ fontSize: '11px', color: '#94a3b8', display: 'block', marginBottom: '4px' }}>
                    Riesgo Máximo:
                  </label>
                  <select
                    value={formData.max_risk_level}
                    onChange={(e) => setFormData({ ...formData, max_risk_level: e.target.value })}
                    style={{
                      width: '100%',
                      backgroundColor: '#0c1017',
                      border: '1px solid #243044',
                      borderRadius: '6px',
                      padding: '7px 10px',
                      fontSize: '12px',
                      color: '#f8fafc',
                    }}
                  >
                    <option value="LOW">LOW</option>
                    <option value="MEDIUM">MEDIUM</option>
                    <option value="HIGH">HIGH</option>
                    <option value="CRITICAL">CRITICAL</option>
                  </select>
                </div>
              </div>

              <div>
                <label style={{ fontSize: '11px', color: '#94a3b8', display: 'block', marginBottom: '4px' }}>
                  Herramientas Permitidas (separadas por comas, '*' para todas):
                </label>
                <input
                  type="text"
                  value={formData.allowed_tools}
                  onChange={(e) => setFormData({ ...formData, allowed_tools: e.target.value })}
                  placeholder="read_file, write_file, run_command"
                  style={{
                    width: '100%',
                    backgroundColor: '#0c1017',
                    border: '1px solid #243044',
                    borderRadius: '6px',
                    padding: '7px 10px',
                    fontSize: '12px',
                    color: '#f8fafc',
                  }}
                />
              </div>

              <div>
                <label style={{ fontSize: '11px', color: '#94a3b8', display: 'block', marginBottom: '4px' }}>
                  Capacidades Operativas (separadas por comas):
                </label>
                <input
                  type="text"
                  value={formData.capabilities}
                  onChange={(e) => setFormData({ ...formData, capabilities: e.target.value })}
                  placeholder="code_editing, testing, security_audit"
                  style={{
                    width: '100%',
                    backgroundColor: '#0c1017',
                    border: '1px solid #243044',
                    borderRadius: '6px',
                    padding: '7px 10px',
                    fontSize: '12px',
                    color: '#f8fafc',
                  }}
                />
              </div>

              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <input
                  type="checkbox"
                  id="req_human"
                  checked={formData.require_human_confirmation}
                  onChange={(e) => setFormData({ ...formData, require_human_confirmation: e.target.checked })}
                  style={{ cursor: 'pointer' }}
                />
                <label htmlFor="req_human" style={{ fontSize: '12px', color: '#f8fafc', cursor: 'pointer' }}>
                  Requerir confirmación humana obligatoria en operaciones de riesgo
                </label>
              </div>

              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px', marginTop: '10px' }}>
                <button
                  type="button"
                  onClick={() => {
                    setShowCreateModal(false);
                    setShowEditModal(false);
                    setShowVersionModal(false);
                  }}
                  style={{
                    padding: '8px 14px',
                    borderRadius: '6px',
                    backgroundColor: '#1e293b',
                    border: '1px solid #334155',
                    color: '#f8fafc',
                    fontSize: '12px',
                    cursor: 'pointer',
                  }}
                >
                  Cancelar
                </button>
                <button
                  type="submit"
                  disabled={actionLoading}
                  style={{
                    padding: '8px 16px',
                    borderRadius: '6px',
                    backgroundColor: '#238636',
                    border: 'none',
                    color: '#ffffff',
                    fontSize: '12px',
                    fontWeight: '600',
                    cursor: 'pointer',
                  }}
                >
                  {actionLoading ? 'Guardando...' : showCreateModal ? 'Registrar Agente' : 'Guardar Cambios'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* MODAL: Historial de Versiones */}
      {showHistoryModal && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            backgroundColor: 'rgba(0, 0, 0, 0.75)',
            backdropFilter: 'blur(4px)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 1000,
            padding: '20px',
          }}
        >
          <div
            style={{
              backgroundColor: '#121824',
              border: '1px solid #233147',
              borderRadius: '12px',
              width: '100%',
              maxWidth: '560px',
              maxHeight: '80vh',
              overflowY: 'auto',
              padding: '24px',
              display: 'flex',
              flexDirection: 'column',
              gap: '16px',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <History size={18} style={{ color: '#38bdf8' }} />
                <h3 style={{ fontSize: '15px', fontWeight: '700', color: '#f8fafc', margin: 0 }}>
                  Trazabilidad de Versiones: {activeAgent?.name}
                </h3>
              </div>
              <button
                onClick={() => setShowHistoryModal(false)}
                style={{ background: 'none', border: 'none', color: '#94a3b8', cursor: 'pointer' }}
              >
                <X size={18} />
              </button>
            </div>

            {historyLoading ? (
              <div style={{ padding: '20px', textAlign: 'center', color: '#73849c', fontSize: '12px' }}>
                Cargando historial inmutable...
              </div>
            ) : versionHistory.length === 0 ? (
              <div style={{ padding: '20px', textAlign: 'center', color: '#73849c', fontSize: '12px' }}>
                Sin versiones previas registradas.
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                {versionHistory.map((item) => (
                  <div
                    key={item.version}
                    style={{
                      backgroundColor: '#0c1017',
                      borderRadius: '6px',
                      border: '1px solid #1a2333',
                      padding: '12px 14px',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                    }}
                  >
                    <div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <span style={{ fontSize: '13px', fontWeight: '700', color: '#38bdf8' }}>
                          Versión {item.version}
                        </span>
                        {item.version === activeAgent?.version && (
                          <span
                            style={{
                              fontSize: '10px',
                              backgroundColor: 'rgba(63, 185, 80, 0.15)',
                              color: '#3fb950',
                              padding: '1px 6px',
                              borderRadius: '4px',
                            }}
                          >
                            Actual
                          </span>
                        )}
                      </div>
                      <div style={{ fontSize: '11px', color: '#73849c', marginTop: '2px' }}>
                        Hash SHA-256:{' '}
                        <span style={{ fontFamily: 'var(--font-mono)', color: '#94a3b8' }}>
                          {item.definition_hash}
                        </span>
                      </div>
                    </div>

                    <span style={{ fontSize: '11px', color: '#64748b' }}>
                      {item.created_at ? new Date(item.created_at).toLocaleDateString() : ''}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
