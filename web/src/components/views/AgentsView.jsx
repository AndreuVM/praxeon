import React, { useState, useEffect } from 'react';
import {
  Search,
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
import McpToolPicker from '../McpToolPicker';
import AgentModelSelect from '../AgentModelSelect';
import { modelsFor } from '../../utils/modelCatalog';
import { notify, confirmDialog } from '../Dialogs';
import { ViewPage, ViewHeader, Chip, Segmented, SearchField, EmptyState } from '../ui';

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
      max_risk_level: String(agent.risk_profile?.max_risk_level || 'MEDIUM').toUpperCase(),
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
      max_risk_level: String(agent.risk_profile?.max_risk_level || 'MEDIUM').toUpperCase(),
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
    if (!(await confirmDialog(`¿Seguro que quieres ${modeDesc} al agente «${agent.name}»?`, { confirmLabel: hardDelete ? 'Eliminar' : 'Dar de baja', danger: true }))) {
      return;
    }
    try {
      await deleteAgent(agent.agent_id, hardDelete);
      await loadAgents();
    } catch (err) {
      notify(`Error al eliminar agente: ${err.message}`);
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
      case 'ACTIVE': return { label: 'Activo', tone: 'ok' };
      case 'INACTIVE': return { label: 'Inactivo', tone: undefined };
      case 'QUARANTINED': return { label: 'En cuarentena', tone: 'bad' };
      case 'TERMINATED': return { label: 'Deprecado', tone: undefined };
      default: return { label: status, tone: undefined };
    }
  };

  return (
    <ViewPage maxWidth={1240}>
      <ViewHeader
        title="Agentes"
        description="Catálogo de agentes autónomos (AgentRegistry): ciclo de vida, versionado inmutable SHA-256 y políticas de confinamiento."
        actions={
          <>
            <button onClick={loadAgents} disabled={loading} className="btn btn-secondary">
              <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
              <span>Actualizar</span>
            </button>
            <button onClick={handleOpenCreate} className="btn btn-primary">
              <Plus size={15} />
              <span>Registrar agente</span>
            </button>
          </>
        }
      />

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px', flexWrap: 'wrap' }}>
        <SearchField value={searchFilter} onChange={setSearchFilter} placeholder="Buscar agente por nombre, id, rol o capacidades…" icon={Search} maxWidth={460} />
        <Segmented
          label="Filtrar por estado"
          value={statusFilter}
          onChange={setStatusFilter}
          options={[
            { id: '', label: 'Todos' },
            { id: 'ACTIVE', label: 'Activos' },
            { id: 'INACTIVE', label: 'Inactivos' },
            { id: 'QUARANTINED', label: 'En cuarentena' },
            { id: 'TERMINATED', label: 'Deprecados' },
          ]}
        />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 380px), 1fr))', gap: '20px', alignItems: 'start' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', minWidth: 0 }}>
          <div style={{ color: 'var(--muted)', fontSize: 'var(--fs-secondary)' }}>Agentes registrados ({filteredAgents.length})</div>

          {loading ? (
            <div style={{ padding: '30px', textAlign: 'center', color: 'var(--muted)' }}>Cargando el catálogo desde el servidor…</div>
          ) : filteredAgents.length === 0 ? (
            <EmptyState title="Ningún agente coincide">Prueba con otro filtro, o registra un agente nuevo con «Registrar agente».</EmptyState>
          ) : (
            filteredAgents.map((agent) => {
              const isSelected = selectedAgent?.agent_id === agent.agent_id;
              const badge = getStatusBadge(agent.status);

              return (
                <div
                  key={agent.agent_id}
                  role="button"
                  tabIndex={0}
                  onClick={() => setSelectedAgentId(agent.agent_id)}
                  onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setSelectedAgentId(agent.agent_id); } }}
                  style={{
                    background: isSelected ? 'var(--surface)' : 'transparent',
                    borderRadius: 'var(--r-card)',
                    border: `1px solid ${isSelected ? 'var(--muted)' : 'var(--line)'}`,
                    padding: '14px 16px',
                    cursor: 'pointer',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '8px',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', minWidth: 0 }}>
                      <span style={{ fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{agent.name}</span>
                      <Chip mono>v{agent.version}</Chip>
                    </div>
                    <Chip tone={badge.tone}>{badge.label}</Chip>
                  </div>

                  <div style={{ fontSize: 'var(--fs-secondary)', color: 'var(--muted)' }}>
                    <span style={{ color: 'var(--text)' }}>{agent.role}</span> · <span>{agent.model?.provider || 'gemini'} / {agent.model?.model_name || 'gemini-1.5-pro'}</span>
                  </div>

                  {agent.description && <p style={{ color: 'var(--muted)', lineHeight: 1.4, fontSize: 'var(--fs-secondary)' }}>{agent.description}</p>}

                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px', marginTop: '2px', flexWrap: 'wrap' }}>
                    <span style={{ fontSize: 'var(--fs-code)', color: 'var(--muted)', fontFamily: 'var(--font-mono)' }}>#{agent.definition_hash?.slice(0, 8)}</span>
                    <div style={{ display: 'flex', gap: '4px', flexWrap: 'wrap' }}>
                      {(agent.capabilities || []).slice(0, 2).map((c) => <Chip key={c}>{c}</Chip>)}
                    </div>
                  </div>
                </div>
              );
            })
          )}
        </div>

        {selectedAgent ? (
          <div style={{ border: '1px solid var(--line)', borderRadius: 'var(--r-card)', padding: '20px', display: 'flex', flexDirection: 'column', gap: '18px', minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '12px', flexWrap: 'wrap' }}>
              <div style={{ minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
                  <h2 style={{ fontSize: 'var(--fs-title)', fontWeight: 600, letterSpacing: '-0.01em' }}>{selectedAgent.name}</h2>
                  <Chip tone={getStatusBadge(selectedAgent.status).tone}>{getStatusBadge(selectedAgent.status).label}</Chip>
                  <Chip mono>v{selectedAgent.version}</Chip>
                </div>
                <div style={{ fontSize: 'var(--fs-secondary)', color: 'var(--muted)', marginTop: '6px', overflowWrap: 'anywhere' }}>
                  Id: <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text)' }}>{selectedAgent.agent_id}</span> · Huella:{' '}
                  <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text)' }}>{selectedAgent.definition_hash}</span>
                </div>
              </div>

              <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
                <button onClick={() => handleOpenHistory(selectedAgent)} className="btn btn-secondary" title="Ver la trazabilidad de versiones">
                  <History size={13} /> Versiones
                </button>
                <button onClick={() => handleOpenVersion(selectedAgent)} className="btn btn-secondary" title="Crear una nueva versión inmutable">
                  <GitCommit size={13} /> Versionar
                </button>
                <button onClick={() => handleOpenEdit(selectedAgent)} className="btn btn-secondary" title="Editar la configuración">
                  <Edit2 size={13} /> Editar
                </button>
                <button onClick={() => handleDeleteAgent(selectedAgent, false)} className="btn btn-danger" title="Eliminar o dar de baja" aria-label="Eliminar o dar de baja">
                  <Trash2 size={13} />
                </button>
              </div>
            </div>

            <div>
              <span style={{ color: 'var(--muted)', fontSize: 'var(--fs-secondary)', display: 'block', marginBottom: '6px' }}>Directiva raíz (system prompt)</span>
              <div style={{ background: 'var(--surface)', borderRadius: 'var(--r-ui)', border: '1px solid var(--line)', padding: '12px', fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-code)', maxHeight: '160px', overflowY: 'auto', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', lineHeight: 1.45 }}>
                {selectedAgent.system_prompt || 'Sin directiva configurada.'}
              </div>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '12px' }}>
              <div style={{ border: '1px solid var(--line)', borderRadius: 'var(--r-ui)', padding: '10px 12px' }}>
                <span style={{ fontSize: 'var(--fs-label)', color: 'var(--muted)', display: 'block' }}>Motor de inferencia</span>
                <span style={{ fontWeight: 500, marginTop: '2px', display: 'block', overflowWrap: 'anywhere' }}>
                  {selectedAgent.model?.provider} / {selectedAgent.model?.model_name}
                </span>
                <span style={{ fontSize: 'var(--fs-secondary)', color: 'var(--muted)', marginTop: '2px', display: 'block' }}>
                  Temperatura: {selectedAgent.model?.temperature ?? 0.2} · Tokens máx.: {selectedAgent.model?.max_output_tokens ?? 4096}
                </span>
              </div>
              <div style={{ border: '1px solid var(--line)', borderRadius: 'var(--r-ui)', padding: '10px 12px' }}>
                <span style={{ fontSize: 'var(--fs-label)', color: 'var(--muted)', display: 'block' }}>Perfil de riesgo y revisión humana</span>
                <span style={{ fontWeight: 500, marginTop: '2px', display: 'block' }}>
                  Nivel máximo: {({ LOW: 'bajo', MEDIUM: 'medio', HIGH: 'alto', CRITICAL: 'crítico' })[selectedAgent.risk_profile?.max_risk_level || 'MEDIUM'] || selectedAgent.risk_profile?.max_risk_level}
                </span>
                <span style={{ fontSize: 'var(--fs-secondary)', color: 'var(--muted)', marginTop: '2px', display: 'block' }}>
                  Aprobación humana: {selectedAgent.risk_profile?.require_human_confirmation ? 'obligatoria' : 'automática'}
                </span>
              </div>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
              <div>
                <span style={{ fontSize: 'var(--fs-secondary)', color: 'var(--muted)', display: 'block', marginBottom: '6px' }}>Herramientas autorizadas</span>
                <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
                  {(selectedAgent.allowed_tools || []).map((t) => <Chip key={t} mono>{t}</Chip>)}
                </div>
              </div>
              <div>
                <span style={{ fontSize: 'var(--fs-secondary)', color: 'var(--muted)', display: 'block', marginBottom: '6px' }}>Capacidades del runtime</span>
                <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
                  {(selectedAgent.capabilities || []).map((c) => <Chip key={c}>{c}</Chip>)}
                </div>
              </div>
            </div>
          </div>
        ) : (
          <div style={{ color: 'var(--muted)', textAlign: 'center', padding: '40px' }}>Elige un agente del catálogo para ver su configuración canónica.</div>
        )}
      </div>

      {/* MODAL: Registrar un agente nuevo / Editar Agente */}
      {(showCreateModal || showEditModal || showVersionModal) && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            backgroundColor: 'var(--scrim)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 1000,
            padding: '20px',
          }}
        >
          <div
            style={{
              backgroundColor: 'var(--surface)',
              border: '1px solid var(--line)',
              borderRadius: 'var(--r-panel)',
              width: '100%',
              maxWidth: '620px',
              maxHeight: '90vh',
              overflowY: 'auto',
              padding: '24px',
              display: 'flex',
              flexDirection: 'column',
              gap: '16px',
              boxShadow: 'none',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <Bot size={18} style={{ color: 'var(--accent)' }} />
                <h3 style={{ fontSize: '14.5px', fontWeight: '700', color: 'var(--text)', margin: 0 }}>
                  {showCreateModal
                    ? 'Registrar un agente nuevo'
                    : showVersionModal
                    ? `Nueva versión inmutable (${activeAgent?.agent_id})`
                    : `Editar agente (${activeAgent?.agent_id})`}
                </h3>
              </div>
              <button
                onClick={() => {
                  setShowCreateModal(false);
                  setShowEditModal(false);
                  setShowVersionModal(false);
                }}
                style={{ background: 'none', border: 'none', color: 'var(--text)', cursor: 'pointer' }}
              >
                <X size={18} />
              </button>
            </div>

            {formError && (
              <div
                style={{
                  backgroundColor: 'color-mix(in srgb, var(--bad) 15%, transparent)',
                  border: '1px solid color-mix(in srgb, var(--bad) 40%, transparent)',
                  borderRadius: '9px',
                  padding: '8px 12px',
                  color: 'var(--bad)',
                  fontSize: '12.5px',
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
                  <label style={{ fontSize: 'var(--fs-secondary)', color: 'var(--muted)', display: 'block', marginBottom: '4px' }}>
                    Id del agente (opcional; se genera solo si se omite)
                  </label>
                  <input
                    type="text"
                    value={formData.agent_id}
                    onChange={(e) => setFormData({ ...formData, agent_id: e.target.value })}
                    placeholder="p. ej. ag_qa_specialist"
                    style={{
                      width: '100%',
                      backgroundColor: 'var(--bg)',
                      border: '1px solid var(--line)',
                      borderRadius: '9px',
                      padding: '7px 10px',
                      fontSize: '12.5px',
                      color: 'var(--text)',
                    }}
                  />
                </div>
              )}

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px' }}>
                <div>
                  <label style={{ fontSize: 'var(--fs-secondary)', color: 'var(--muted)', display: 'block', marginBottom: '4px' }}>
                    Nombre del agente
                  </label>
                  <input
                    type="text"
                    required
                    value={formData.name}
                    onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                    placeholder="p. ej. Auditor de seguridad"
                    style={{
                      width: '100%',
                      backgroundColor: 'var(--bg)',
                      border: '1px solid var(--line)',
                      borderRadius: '9px',
                      padding: '7px 10px',
                      fontSize: '12.5px',
                      color: 'var(--text)',
                    }}
                  />
                </div>

                <div>
                  <label style={{ fontSize: 'var(--fs-secondary)', color: 'var(--muted)', display: 'block', marginBottom: '4px' }}>
                    Rol funcional
                  </label>
                  <input
                    type="text"
                    required
                    value={formData.role}
                    onChange={(e) => setFormData({ ...formData, role: e.target.value })}
                    placeholder="p. ej. Auditor de seguridad"
                    style={{
                      width: '100%',
                      backgroundColor: 'var(--bg)',
                      border: '1px solid var(--line)',
                      borderRadius: '9px',
                      padding: '7px 10px',
                      fontSize: '12.5px',
                      color: 'var(--text)',
                    }}
                  />
                </div>
              </div>

              <div>
                <label style={{ fontSize: 'var(--fs-secondary)', color: 'var(--muted)', display: 'block', marginBottom: '4px' }}>
                  Descripción operativa
                </label>
                <input
                  type="text"
                  value={formData.description}
                  onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                  placeholder="Propósito funcional del agente"
                  style={{
                    width: '100%',
                    backgroundColor: 'var(--bg)',
                    border: '1px solid var(--line)',
                    borderRadius: '9px',
                    padding: '7px 10px',
                    fontSize: '12.5px',
                    color: 'var(--text)',
                  }}
                />
              </div>

              <div>
                <label style={{ fontSize: 'var(--fs-secondary)', color: 'var(--muted)', display: 'block', marginBottom: '4px' }}>
                  Directiva raíz (system prompt)
                </label>
                <textarea
                  required
                  rows={4}
                  value={formData.system_prompt}
                  onChange={(e) => setFormData({ ...formData, system_prompt: e.target.value })}
                  placeholder="Instrucciones maestras de razonamiento y contención…"
                  style={{
                    width: '100%',
                    backgroundColor: 'var(--bg)',
                    border: '1px solid var(--line)',
                    borderRadius: '9px',
                    padding: '8px 10px',
                    fontSize: '11.5px',
                    fontFamily: 'var(--font-mono)',
                    color: 'var(--text)',
                  }}
                />
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '10px' }}>
                <div>
                  <label style={{ fontSize: 'var(--fs-secondary)', color: 'var(--muted)', display: 'block', marginBottom: '4px' }}>
                    Proveedor
                  </label>
                  <select
                    value={formData.provider}
                    onChange={(e) => setFormData({ ...formData, provider: e.target.value, model_name: modelsFor(e.target.value)[0] || '' })}
                    style={{
                      width: '100%',
                      backgroundColor: 'var(--bg)',
                      border: '1px solid var(--line)',
                      borderRadius: '9px',
                      padding: '7px 10px',
                      fontSize: '12.5px',
                      color: 'var(--text)',
                    }}
                  >
                    <option value="gemini">Gemini</option>
                    <option value="openai">OpenAI</option>
                    <option value="anthropic">Anthropic</option>
                    <option value="claude_cli">Claude CLI</option>
                    <option value="ollama">Ollama</option>
                    <option value="mock">Proveedor simulado</option>
                  </select>
                </div>

                <div>
                  <label style={{ fontSize: 'var(--fs-secondary)', color: 'var(--muted)', display: 'block', marginBottom: '4px' }}>
                    Modelo
                  </label>
                  <AgentModelSelect
                    provider={formData.provider}
                    value={formData.model_name}
                    onChange={(model) => setFormData({ ...formData, model_name: model })}
                  />
                </div>

                <div>
                  <label style={{ fontSize: 'var(--fs-secondary)', color: 'var(--muted)', display: 'block', marginBottom: '4px' }}>
                    Riesgo máximo
                  </label>
                  <select
                    value={formData.max_risk_level}
                    onChange={(e) => setFormData({ ...formData, max_risk_level: e.target.value })}
                    style={{
                      width: '100%',
                      backgroundColor: 'var(--bg)',
                      border: '1px solid var(--line)',
                      borderRadius: '9px',
                      padding: '7px 10px',
                      fontSize: '12.5px',
                      color: 'var(--text)',
                    }}
                  >
                    <option value="LOW">Bajo</option>
                    <option value="MEDIUM">Medio</option>
                    <option value="HIGH">Alto</option>
                    <option value="CRITICAL">Crítico</option>
                  </select>
                </div>
              </div>

              <div>
                <label style={{ fontSize: 'var(--fs-secondary)', color: 'var(--muted)', display: 'block', marginBottom: '4px' }}>
                  Herramientas permitidas (separadas por comas; «*» para todas)
                </label>
                <input
                  type="text"
                  value={formData.allowed_tools}
                  onChange={(e) => setFormData({ ...formData, allowed_tools: e.target.value })}
                  placeholder="read_file, write_file, run_command"
                  style={{
                    width: '100%',
                    backgroundColor: 'var(--bg)',
                    border: '1px solid var(--line)',
                    borderRadius: '9px',
                    padding: '7px 10px',
                    fontSize: '12.5px',
                    color: 'var(--text)',
                  }}
                />
                <McpToolPicker
                  value={formData.allowed_tools}
                  onChange={(next) => setFormData({ ...formData, allowed_tools: next })}
                />
              </div>

              <div>
                <label style={{ fontSize: 'var(--fs-secondary)', color: 'var(--muted)', display: 'block', marginBottom: '4px' }}>
                  Capacidades operativas (separadas por comas)
                </label>
                <input
                  type="text"
                  value={formData.capabilities}
                  onChange={(e) => setFormData({ ...formData, capabilities: e.target.value })}
                  placeholder="code_editing, testing, security_audit"
                  style={{
                    width: '100%',
                    backgroundColor: 'var(--bg)',
                    border: '1px solid var(--line)',
                    borderRadius: '9px',
                    padding: '7px 10px',
                    fontSize: '12.5px',
                    color: 'var(--text)',
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
                <label htmlFor="req_human" style={{ fontSize: '12.5px', color: 'var(--text)', cursor: 'pointer' }}>
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
                    borderRadius: '9px',
                    backgroundColor: 'var(--line)',
                    border: '1px solid var(--line)',
                    color: 'var(--text)',
                    fontSize: '12.5px',
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
                    borderRadius: '9px',
                    backgroundColor: 'var(--ok)',
                    border: 'none',
                    color: 'var(--on-accent)',
                    fontSize: '12.5px',
                    fontWeight: '600',
                    cursor: 'pointer',
                  }}
                >
                  {actionLoading ? 'Guardando...' : showCreateModal ? 'Registrar agente' : 'Guardar cambios'}
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
            backgroundColor: 'var(--scrim)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 1000,
            padding: '20px',
          }}
        >
          <div
            style={{
              backgroundColor: 'var(--surface)',
              border: '1px solid var(--line)',
              borderRadius: '11px',
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
                <History size={18} style={{ color: 'var(--accent)' }} />
                <h3 style={{ fontSize: '14.5px', fontWeight: '700', color: 'var(--text)', margin: 0 }}>
                  Trazabilidad de Versiones: {activeAgent?.name}
                </h3>
              </div>
              <button
                onClick={() => setShowHistoryModal(false)}
                style={{ background: 'none', border: 'none', color: 'var(--text)', cursor: 'pointer' }}
              >
                <X size={18} />
              </button>
            </div>

            {historyLoading ? (
              <div style={{ padding: '20px', textAlign: 'center', color: 'var(--muted)', fontSize: '12.5px' }}>
                Cargando el historial inmutable…
              </div>
            ) : versionHistory.length === 0 ? (
              <div style={{ padding: '20px', textAlign: 'center', color: 'var(--muted)', fontSize: '12.5px' }}>
                Sin versiones previas registradas.
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                {versionHistory.map((item) => (
                  <div
                    key={item.version}
                    style={{
                      backgroundColor: 'var(--bg)',
                      borderRadius: '9px',
                      border: '1px solid var(--line)',
                      padding: '12px 14px',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                    }}
                  >
                    <div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <span style={{ fontSize: '13px', fontWeight: '700', color: 'var(--accent)' }}>
                          Versión {item.version}
                        </span>
                        {item.version === activeAgent?.version && (
                          <span
                            style={{
                              fontSize: '11px',
                              backgroundColor: 'color-mix(in srgb, var(--ok) 15%, transparent)',
                              color: 'var(--ok)',
                              padding: '1px 6px',
                              borderRadius: '6px',
                            }}
                          >
                            Actual
                          </span>
                        )}
                      </div>
                      <div style={{ fontSize: '11px', color: 'var(--muted)', marginTop: '2px' }}>
                        Hash SHA-256:{' '}
                        <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text)' }}>
                          {item.definition_hash}
                        </span>
                      </div>
                    </div>

                    <span style={{ fontSize: '11px', color: 'var(--muted)' }}>
                      {item.created_at ? new Date(item.created_at).toLocaleDateString() : ''}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </ViewPage>
  );
}
