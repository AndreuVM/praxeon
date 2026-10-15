import React, { useState, useEffect, useCallback } from 'react';
import {
  GitFork,
  Search,
  ArrowRight,
  Copy,
  Check,
  RefreshCw,
  Terminal,
  FileCode,
  Globe,
} from 'lucide-react';
import * as api from '../../services/api';
import { ViewPage, ViewHeader, Chip, Segmented, SearchField, Modal } from '../ui';

export default function DecisionsView({
  decisions: propDecisions = [],
  currentSessionId,
  sessions = [],
  onSelectDecision,
}) {
  const [decisions, setDecisions] = useState(propDecisions);
  const [loading, setLoading] = useState(false);
  const [selectedSession, setSelectedSession] = useState(currentSessionId || 'ALL');
  const [filterText, setFilterText] = useState('');
  const [statusFilter, setStatusFilter] = useState('ALL');
  const [riskFilter, setRiskFilter] = useState('ALL');
  const [copiedId, setCopiedId] = useState(null);
  const [activeReceiptModal, setActiveReceiptModal] = useState(null);

  // Fetch decisions from backend
  const loadDecisions = useCallback(async (sid) => {
    await Promise.resolve();
    setLoading(true);
    try {
      const targetSid = sid === 'ALL' ? null : sid;
      const res = await api.fetchDecisions(targetSid);
      if (res && res.data) {
        setDecisions(res.data);
      } else if (propDecisions && propDecisions.length > 0) {
        setDecisions(propDecisions);
      } else {
        setDecisions([]);
      }
    } catch (err) {
      console.warn('Error loading decisions in DecisionsView:', err);
      if (propDecisions && propDecisions.length > 0) {
        setDecisions(propDecisions);
      }
    } finally {
      setLoading(false);
    }
  }, [propDecisions]);

  useEffect(() => {
    let mounted = true;
    const sid = (currentSessionId && selectedSession === 'ALL') ? currentSessionId : selectedSession;
    if (currentSessionId && selectedSession === 'ALL') {
      queueMicrotask(() => setSelectedSession(currentSessionId));
    }
    (async () => {
      try {
        const targetSid = sid === 'ALL' ? null : sid;
        const res = await api.fetchDecisions(targetSid);
        if (mounted) {
          if (res && res.data) {
            setDecisions(res.data);
          } else if (propDecisions && propDecisions.length > 0) {
            setDecisions(propDecisions);
          } else {
            setDecisions([]);
          }
        }
      } catch {
        if (mounted && propDecisions && propDecisions.length > 0) {
          setDecisions(propDecisions);
        }
      }
    })();
    return () => {
      mounted = false;
    };
  }, [selectedSession, currentSessionId, propDecisions]);

  // Synchronize when propDecisions change
  useEffect(() => {
    if (propDecisions && propDecisions.length > 0 && decisions.length === 0) {
      queueMicrotask(() => {
        setDecisions(propDecisions);
      });
    }
  }, [propDecisions, decisions.length]);

  const copyToClipboard = (text, id) => {
    navigator.clipboard?.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 1800);
  };

  // KPIs
  const totalCount = decisions.length;
  const allowCount = decisions.filter((d) => d.status === 'ALLOW').length;
  const reviewCount = decisions.filter((d) => d.status === 'REVIEW').length;
  const replanCount = decisions.filter((d) => d.status === 'REPLAN').length;
  const blockedCount = decisions.filter((d) => d.status === 'BLOCKED' || d.status === 'BLOCK').length;

  const filtered = decisions.filter((d) => {
    const text = filterText.toLowerCase();
    const matchesText =
      (d.command || '').toLowerCase().includes(text) ||
      (d.tool || '').toLowerCase().includes(text) ||
      (d.decision_id || d.id || '').toLowerCase().includes(text) ||
      (d.session_id || '').toLowerCase().includes(text) ||
      (d.action_hash || '').toLowerCase().includes(text);

    const matchesStatus =
      statusFilter === 'ALL' ||
      (d.status || '').toUpperCase() === statusFilter.toUpperCase() ||
      (statusFilter === 'BLOCKED' && d.status === 'BLOCK');

    const matchesRisk =
      riskFilter === 'ALL' ||
      (d.risk_level || d.risk || '').toUpperCase() === riskFilter.toUpperCase();

    return matchesText && matchesStatus && matchesRisk;
  });

  const getToolIcon = (toolName = '') => {
    const lower = toolName.toLowerCase();
    if (lower.includes('terminal') || lower.includes('command') || lower.includes('exec')) {
      return <Terminal size={13} style={{ color: 'var(--muted)' }} />;
    }
    if (lower.includes('file') || lower.includes('read') || lower.includes('write')) {
      return <FileCode size={13} style={{ color: 'var(--muted)' }} />;
    }
    if (lower.includes('web') || lower.includes('search') || lower.includes('fetch')) {
      return <Globe size={13} style={{ color: 'var(--muted)' }} />;
    }
    return <GitFork size={13} style={{ color: 'var(--muted)' }} />;
  };

  const STATUS_VIEW = {
    ALLOW: { label: 'Permitida', tone: 'ok' },
    REVIEW: { label: 'En revisión', tone: 'accent' },
    REPLAN: { label: 'Replanificada', tone: undefined },
    BLOCKED: { label: 'Bloqueada', tone: 'bad' },
    BLOCK: { label: 'Bloqueada', tone: 'bad' },
  };
  const RISK_VIEW = { LOW: 'Bajo', MEDIUM: 'Medio', HIGH: 'Alto', CRITICAL: 'Crítico' };

  const stat = (label, value, color) => (
    <div style={{ border: '1px solid var(--line)', borderRadius: 'var(--r-card)', padding: '12px 16px' }}>
      <span style={{ fontSize: 'var(--fs-label)', color: 'var(--muted)', display: 'block' }}>{label}</span>
      <span style={{ fontSize: 'var(--fs-title)', fontWeight: 500, fontFamily: 'var(--font-mono)', color: value > 0 && color ? color : 'var(--text)' }}>{value}</span>
    </div>
  );

  const th = { padding: '10px 14px 10px 0', fontWeight: 500, fontSize: 'var(--fs-label)', letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--muted)', textAlign: 'left', borderBottom: '1px solid var(--line)' };
  const td = { padding: '10px 14px 10px 0', borderBottom: '1px solid var(--line)', verticalAlign: 'top' };

  return (
    <ViewPage maxWidth={1240}>
      <ViewHeader
        title="Decisiones"
        description="Registro auditado de las propuestas evaluadas, con las políticas deterministas y los recibos criptográficos HMAC-SHA256."
        actions={
          <>
            <select aria-label="Sesión" value={selectedSession} onChange={(e) => setSelectedSession(e.target.value)} style={{ maxWidth: '240px' }}>
              <option value="ALL">Todas las sesiones</option>
              {sessions.map((s) => (
                <option key={s.session_id} value={s.session_id}>
                  #{s.session_id} ({s.agent_name || 'CodingAgent'})
                </option>
              ))}
            </select>
            <button onClick={() => loadDecisions(selectedSession)} className="btn btn-secondary" title="Actualizar las decisiones">
              <RefreshCw size={13} className={loading ? 'spin' : ''} />
              Actualizar
            </button>
          </>
        }
      />

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: '12px' }}>
        {stat('Evaluadas', totalCount)}
        {stat('Permitidas', allowCount, 'var(--ok)')}
        {stat('En revisión', reviewCount, 'var(--accent)')}
        {stat('Replanificadas', replanCount)}
        {stat('Bloqueadas', blockedCount, 'var(--bad)')}
      </div>

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '14px' }}>
        <SearchField value={filterText} onChange={setFilterText} placeholder="Buscar por comando, herramienta, id o hash…" icon={Search} />
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
          <select aria-label="Nivel de riesgo" value={riskFilter} onChange={(e) => setRiskFilter(e.target.value)}>
            <option value="ALL">Todos los riesgos</option>
            <option value="LOW">Riesgo bajo</option>
            <option value="MEDIUM">Riesgo medio</option>
            <option value="HIGH">Riesgo alto</option>
            <option value="CRITICAL">Riesgo crítico</option>
          </select>
          <Segmented
            label="Filtrar por veredicto"
            value={statusFilter}
            onChange={setStatusFilter}
            options={[
              { id: 'ALL', label: 'Todas' },
              { id: 'ALLOW', label: 'Permitidas' },
              { id: 'REVIEW', label: 'En revisión' },
              { id: 'REPLAN', label: 'Replanificadas' },
              { id: 'BLOCKED', label: 'Bloqueadas' },
            ]}
          />
        </div>
      </div>

      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: '900px' }}>
          <thead>
            <tr>
              <th style={{ ...th, width: '80px' }}>Id / sec.</th>
              <th style={{ ...th, width: '130px' }}>Veredicto</th>
              <th style={{ ...th, width: '130px' }}>Herramienta</th>
              <th style={th}>Acción o comando</th>
              <th style={{ ...th, width: '80px' }}>Riesgo</th>
              <th style={{ ...th, width: '140px' }}>Supervisor</th>
              <th style={{ ...th, width: '130px' }}>Recibo HMAC</th>
              <th style={{ ...th, width: '80px', textAlign: 'right', paddingRight: 0 }} />
            </tr>
          </thead>
          <tbody>
            {filtered.length > 0 ? (
              filtered.map((d, idx) => {
                const decId = d.decision_id || d.id || `node-${idx + 1}`;
                const status = (d.status || 'ALLOW').toUpperCase();
                const risk = (d.risk_level || d.risk || 'LOW').toUpperCase();
                const shortId = decId.length > 12 ? `${decId.slice(0, 10)}…` : decId;
                const sig = d.signature || d.action_hash || '';
                const sv = STATUS_VIEW[status] || STATUS_VIEW.BLOCKED;

                return (
                  <tr key={decId}>
                    <td style={{ ...td, fontFamily: 'var(--font-mono)', color: 'var(--muted)' }}>
                      <span title={decId}>#{d.sequence || shortId}</span>
                    </td>
                    <td style={td}>
                      <Chip tone={sv.tone} title={status}>{sv.label}</Chip>
                    </td>
                    <td style={td}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '7px' }}>
                        {getToolIcon(d.tool)}
                        <span>{d.tool || 'system'}</span>
                      </div>
                    </td>
                    <td style={td}>
                      <div
                        style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-code)', maxWidth: '420px', maxHeight: '65px', overflow: 'auto', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', lineHeight: 1.4 }}
                        title={d.command || d.description || ''}
                      >
                        {d.command || d.description || '(acción sin comando especificado)'}
                      </div>
                    </td>
                    <td style={td}>
                      <span style={{ color: risk === 'CRITICAL' || risk === 'HIGH' ? 'var(--bad)' : risk === 'MEDIUM' ? 'var(--accent)' : 'var(--ok)' }}>
                        {RISK_VIEW[risk] || risk}
                      </span>
                    </td>
                    <td style={td}>
                      <div>{d.provider || 'PolicyEngine'}</div>
                      {d.grounding_score !== undefined && d.grounding_score !== null && (
                        <div style={{ fontSize: 'var(--fs-label)', color: 'var(--muted)' }}>Fundamentada: {(Number(d.grounding_score) * 100).toFixed(0)}%</div>
                      )}
                    </td>
                    <td style={td}>
                      {sig ? (
                        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                          <button
                            onClick={() => setActiveReceiptModal(d)}
                            title="Ver el recibo criptográfico firmado"
                            style={{ background: 'none', border: 0, padding: 0, fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-code)', color: 'var(--text)', textDecoration: 'underline', textUnderlineOffset: '3px' }}
                          >
                            {sig.slice(0, 10)}…
                          </button>
                          <button
                            className="btn-icon"
                            style={{ width: '24px', height: '24px', color: copiedId === decId ? 'var(--ok)' : undefined }}
                            onClick={() => copyToClipboard(sig, decId)}
                            title="Copiar el hash HMAC"
                            aria-label="Copiar el hash HMAC"
                          >
                            {copiedId === decId ? <Check size={12} /> : <Copy size={12} />}
                          </button>
                        </div>
                      ) : (
                        <span style={{ color: 'var(--muted)', fontSize: 'var(--fs-secondary)' }}>Sin firma</span>
                      )}
                    </td>
                    <td style={{ ...td, textAlign: 'right', paddingRight: 0 }}>
                      <button onClick={() => onSelectDecision?.(decId, d.session_id)} className="btn btn-secondary" style={{ padding: '3px 10px' }} title="Abrir el detalle de esta decisión en la conversación">
                        Ver
                        <ArrowRight size={11} />
                      </button>
                    </td>
                  </tr>
                );
              })
            ) : (
              <tr>
                <td colSpan={8} style={{ padding: '40px 16px', textAlign: 'center', color: 'var(--muted)' }}>
                  {loading ? 'Cargando las decisiones del repositorio…' : 'No hay decisiones registradas que coincidan con el filtro. Aparecerán aquí a medida que los agentes propongan acciones.'}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <Modal open={Boolean(activeReceiptModal)} title="Recibo criptográfico HMAC-SHA256" maxWidth={520} onClose={() => setActiveReceiptModal(null)}>
        {activeReceiptModal && (
          <>
            {[
              ['Identificador de decisión', activeReceiptModal.decision_id || activeReceiptModal.id, false],
              ['Firma HMAC', activeReceiptModal.signature || 'Firma generada en tiempo de ejecución', true],
              ['Hash de la acción', activeReceiptModal.action_hash || 'SHA256(carga canónica de la acción)', true],
            ].map(([label, value, wrap]) => (
              <div key={label} style={{ borderBottom: '1px solid var(--line)', paddingBottom: '10px' }}>
                <span style={{ color: 'var(--muted)', fontSize: 'var(--fs-secondary)', display: 'block' }}>{label}</span>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-code)', overflowWrap: wrap ? 'anywhere' : undefined, userSelect: 'text' }}>{value}</span>
              </div>
            ))}
            <div>
              <span style={{ color: 'var(--muted)', fontSize: 'var(--fs-secondary)', display: 'block' }}>Modo de sandbox y estado</span>
              <span>
                {activeReceiptModal.execution_mode || 'local_restricted'} · {(STATUS_VIEW[(activeReceiptModal.status || '').toUpperCase()] || STATUS_VIEW.BLOCKED).label}
              </span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
              <button
                onClick={() => {
                  const id = activeReceiptModal.decision_id || activeReceiptModal.id;
                  const sid = activeReceiptModal.session_id;
                  setActiveReceiptModal(null);
                  onSelectDecision?.(id, sid);
                }}
                className="btn btn-primary"
              >
                Abrir el detalle de la decisión
                <ArrowRight size={12} />
              </button>
            </div>
          </>
        )}
      </Modal>
    </ViewPage>
  );
}
