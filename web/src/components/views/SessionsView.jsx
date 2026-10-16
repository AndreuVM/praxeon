import React, { useState } from 'react';
import WorkspaceField from '../workspace/WorkspaceField';
import {
  FolderKanban,
  Plus,
  ArrowRight,
  Search,
  RefreshCw,
  Shield,
  Bot,
  Play,
  Trash2,
} from 'lucide-react';
import { confirmDialog } from '../Dialogs';
import { ViewPage, ViewHeader, Chip, Segmented, SearchField, Field, EmptyState, Modal } from '../ui';

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
    if (!(await confirmDialog('¿Purgar todas las sesiones antiguas y finalizadas? Se eliminarán de la base de datos, la memoria y los puntos de control para liberar espacio.', { confirmLabel: 'Purgar', danger: true }))) {
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

  const STATUS_LABEL = { Active: 'Activa', Paused: 'Pausada', Completed: 'Completada' };

  return (
    <ViewPage>
      <ViewHeader
        title="Sesiones"
        description="Misiones de agentes autónomos auditadas por PRAXEON: abre una anterior o lanza una nueva."
        actions={
          <>
            <button onClick={handleClearOld} className="btn btn-danger" title="Eliminar las sesiones antiguas y finalizadas para liberar espacio">
              <Trash2 size={13} />
              Limpiar antiguas
            </button>
            <button onClick={handleRefresh} className="btn btn-secondary" title="Actualizar la lista de sesiones">
              <RefreshCw size={13} className={isRefreshing ? 'spin' : ''} />
              Actualizar
            </button>
            {onNewCleanSession && (
              <button onClick={onNewCleanSession} className="btn btn-secondary" title="Abrir una sesión limpia en la conversación">
                <Plus size={14} />
                Sesión limpia
              </button>
            )}
            <button onClick={() => setIsModalOpen(true)} className="btn btn-primary">
              <Plus size={14} />
              Nueva misión
            </button>
          </>
        }
      />

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '14px' }}>
        <SearchField value={filter} onChange={setFilter} placeholder="Buscar por id, objetivo, agente o sandbox…" icon={Search} />
        <Segmented
          label="Filtrar por estado"
          value={statusFilter}
          onChange={setStatusFilter}
          options={[
            { id: 'ALL', label: 'Todas' },
            { id: 'ACTIVE', label: 'Activas' },
            { id: 'PAUSED', label: 'Pausadas' },
            { id: 'COMPLETED', label: 'Completadas' },
          ]}
        />
      </div>

      {filteredSessions.length > 0 ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))', gap: '14px' }}>
          {filteredSessions.map((s) => {
            const isSelected = s.session_id === currentSessionId;
            const totalDecisions = s.total_decisions || s.steps_count || 0;
            const allowed = s.allowed_count || (s.total_decisions ? Math.max(0, s.total_decisions - (s.blocked_count || 0) - (s.review_count || 0)) : 0);
            const blocked = s.blocked_count || 0;
            const inReview = s.review_count || 0;
            const status = s.status || 'Active';

            return (
              <div
                key={s.session_id}
                style={{
                  background: 'var(--surface)',
                  border: `1px solid ${isSelected ? 'var(--muted)' : 'var(--line)'}`,
                  borderRadius: 'var(--r-card)',
                  padding: '16px 18px',
                  display: 'flex',
                  flexDirection: 'column',
                  justifyContent: 'space-between',
                  gap: '14px',
                  minWidth: 0,
                }}
              >
                <div style={{ minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px', marginBottom: '10px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', minWidth: 0 }}>
                      <span style={{ fontWeight: 500, fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-secondary)' }}>#{s.session_id}</span>
                      <Chip tone={status === 'Active' ? 'ok' : undefined}>{STATUS_LABEL[status] || status}</Chip>
                    </div>
                    <span style={{ fontSize: 'var(--fs-label)', color: 'var(--muted)', flexShrink: 0 }}>
                      {s.created_at ? new Date(s.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : 'Reciente'}
                    </span>
                  </div>

                  <p style={{ lineHeight: 1.45, marginBottom: '12px', display: '-webkit-box', WebkitLineClamp: 3, WebkitBoxOrient: 'vertical', overflow: 'hidden', overflowWrap: 'anywhere' }}>
                    {s.goal || 'Sin objetivo especificado'}
                  </p>

                  <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
                    <Chip icon={Bot}>{s.agent_name || 'CodingAgent'}</Chip>
                    <Chip icon={Shield} tone={s.execution_mode === 'full_access' ? 'bad' : undefined}>{s.execution_mode || 'local_restricted'}</Chip>
                    {s.workspace_root && (
                      <Chip icon={FolderKanban} title={s.workspace_root}>
                        {s.workspace_root.split(/[\\/]/).pop() || s.workspace_root}
                      </Chip>
                    )}
                  </div>
                </div>

                <div style={{ borderTop: '1px solid var(--line)', paddingTop: '12px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '10px', flexWrap: 'wrap' }}>
                  <div style={{ fontSize: 'var(--fs-secondary)', color: 'var(--muted)', display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                    <span style={{ color: 'var(--text)' }}>{totalDecisions} {totalDecisions === 1 ? 'decisión' : 'decisiones'}</span>
                    <span>·</span>
                    <span>{allowed} permitidas</span>
                    {blocked > 0 && <><span>·</span><span style={{ color: 'var(--bad)' }}>{blocked} bloqueadas</span></>}
                    {inReview > 0 && <><span>·</span><span>{inReview} en revisión</span></>}
                  </div>

                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                    {onDeleteSession && (
                      <button
                        className="btn-icon"
                        onClick={async (e) => {
                          e.stopPropagation();
                          if (await confirmDialog(`¿Eliminar permanentemente la sesión #${s.session_id}?`, { confirmLabel: 'Eliminar', danger: true })) {
                            onDeleteSession(s.session_id);
                          }
                        }}
                        title="Eliminar esta sesión"
                        aria-label={`Eliminar la sesión ${s.session_id}`}
                      >
                        <Trash2 size={14} strokeWidth={1.6} />
                      </button>
                    )}
                    <button
                      onClick={() => onSelectSession?.(s.session_id)}
                      className="btn btn-secondary"
                      style={{ padding: '4px 12px', color: isSelected ? 'var(--text)' : undefined }}
                    >
                      {isSelected ? 'En la conversación' : 'Abrir en la conversación'}
                      <ArrowRight size={12} />
                    </button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <EmptyState
          title={`No hay sesiones ${filter ? 'con ese criterio' : 'todavía'}`}
          actions={null}
        >
          Lanza una misión supervisada con «Nueva misión» o empieza con una de estas plantillas.
        </EmptyState>
      )}

      {filteredSessions.length === 0 && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '12px' }}>
          {starterTemplates.map((tmpl, idx) => (
            <div key={idx} style={{ border: '1px solid var(--line)', borderRadius: 'var(--r-card)', padding: '16px', display: 'flex', flexDirection: 'column', justifyContent: 'space-between', gap: '12px' }}>
              <div>
                <div style={{ fontWeight: 500, marginBottom: '6px' }}>{tmpl.title}</div>
                <p style={{ color: 'var(--muted)', fontSize: 'var(--fs-secondary)', lineHeight: 1.45 }}>{tmpl.goal}</p>
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
                style={{ alignSelf: 'flex-start', padding: '4px 12px' }}
              >
                <Play size={11} />
                Iniciar esta plantilla
              </button>
            </div>
          ))}
        </div>
      )}

      <Modal open={isModalOpen} title="Nueva misión supervisada" onClose={() => setIsModalOpen(false)}>
        <form onSubmit={handleCreateSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          <Field label="Objetivo de la misión (obligatorio)" htmlFor="sv-goal">
            <textarea
              id="sv-goal"
              rows={3}
              required
              placeholder="Por ejemplo: analizar el middleware de autenticación, corregir la vulnerabilidad de sesión y validarlo con pruebas unitarias."
              value={formData.goal}
              onChange={(e) => setFormData({ ...formData, goal: e.target.value })}
              style={{ width: '100%', resize: 'vertical', lineHeight: 1.45 }}
            />
          </Field>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '14px' }}>
            <Field label="Agente" htmlFor="sv-agent">
              <select id="sv-agent" value={formData.agent_name} onChange={(e) => setFormData({ ...formData, agent_name: e.target.value })} style={{ width: '100%' }}>
                <option value="CodingAgent">CodingAgent (ReAct + herramientas)</option>
                <option value="SecurityAuditAgent">SecurityAuditAgent</option>
                <option value="ExplorerAgent">ExplorerAgent</option>
              </select>
            </Field>
            <Field label="Aislamiento (sandbox)" htmlFor="sv-mode">
              <select id="sv-mode" value={formData.execution_mode} onChange={(e) => setFormData({ ...formData, execution_mode: e.target.value })} style={{ width: '100%' }}>
                <option value="local_restricted">Local confinado (recomendado)</option>
                <option value="container">Contenedor Docker</option>
                <option value="full_access">Acceso directo al host (requiere confirmación)</option>
              </select>
            </Field>
          </div>

          <Field label="Carpeta de trabajo (workspace)" hint="Por defecto, la del proyecto">
            <WorkspaceField value={formData.workspace_root || ''} onChange={(path) => setFormData({ ...formData, workspace_root: path })} />
          </Field>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '14px' }}>
            <Field label="Proveedor del modelo" htmlFor="sv-provider">
              <select id="sv-provider" value={formData.llm_provider} onChange={(e) => setFormData({ ...formData, llm_provider: e.target.value })} style={{ width: '100%' }}>
                <option value="simulator">Simulador determinista</option>
                <option value="ollama">Ollama local (http://localhost:11434)</option>
                <option value="groq">Groq Cloud (Llama 3.3 / Qwen)</option>
                <option value="openai">OpenAI (GPT-4o)</option>
                <option value="gemini">Google Gemini</option>
                <option value="anthropic">Claude (API de Anthropic)</option>
                <option value="claude_cli">Claude CLI (suscripción)</option>
              </select>
            </Field>
            <Field label="Supervisor cognitivo" htmlFor="sv-supervisor">
              <select id="sv-supervisor" value={formData.supervisor} onChange={(e) => setFormData({ ...formData, supervisor: e.target.value })} style={{ width: '100%' }}>
                <option value="laya">LAYA (ruta rápida local, sistema 1)</option>
                <option value="kev">KEV (local, sistema 1)</option>
                <option value="typesafe">TypeSafe AI (escalado profundo)</option>
                <option value="cascade">Cascada (configurable en Proveedores)</option>
              </select>
            </Field>
          </div>

          <Field label={`Límite de pasos (${formData.max_steps})`} htmlFor="sv-steps">
            <input
              id="sv-steps"
              type="range"
              min="5"
              max="100"
              step="5"
              value={formData.max_steps}
              onChange={(e) => setFormData({ ...formData, max_steps: Number(e.target.value) })}
              style={{ width: '100%', cursor: 'pointer' }}
            />
          </Field>

          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px', borderTop: '1px solid var(--line)', paddingTop: '16px' }}>
            <button type="button" onClick={() => setIsModalOpen(false)} className="btn btn-secondary">Cancelar</button>
            <button type="submit" className="btn btn-primary">
              <Play size={13} />
              Iniciar supervisión
            </button>
          </div>
        </form>
      </Modal>
    </ViewPage>
  );
}
