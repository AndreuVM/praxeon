import React, { useState, useEffect, useCallback } from 'react';
import {
  GitFork,
  Search,
  ArrowRight,
  Copy,
  Check,
  RefreshCw,
  Shield,
  Terminal,
  FileCode,
  Globe,
  Key,
} from 'lucide-react';
import * as api from '../../services/api';

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
      return <Terminal size={13} style={{ color: '#58a6ff' }} />;
    }
    if (lower.includes('file') || lower.includes('read') || lower.includes('write')) {
      return <FileCode size={13} style={{ color: '#3fb950' }} />;
    }
    if (lower.includes('web') || lower.includes('search') || lower.includes('fetch')) {
      return <Globe size={13} style={{ color: '#d29922' }} />;
    }
    return <GitFork size={13} style={{ color: '#8b949e' }} />;
  };

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
              <GitFork size={18} />
            </div>
            <h1 style={{ fontSize: '18px', fontWeight: '700', color: '#f8fafc', letterSpacing: '-0.01em' }}>
              Registro Auditado de Decisiones
            </h1>
          </div>
          <p style={{ fontSize: '12px', color: '#73849c', marginTop: '6px' }}>
            Trazabilidad inmutable de propuestas evaluadas, políticas deterministas y recibos criptográficos HMAC-SHA256.
          </p>
        </div>

        {/* Right side session selector and refresh */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <select
            value={selectedSession}
            onChange={(e) => setSelectedSession(e.target.value)}
            style={{
              padding: '7px 12px',
              borderRadius: '8px',
              backgroundColor: '#121824',
              border: '1px solid #1e293b',
              color: '#f8fafc',
              fontSize: '12px',
              outline: 'none',
              maxWidth: '220px',
            }}
          >
            <option value="ALL">Todas las sesiones</option>
            {sessions.map((s) => (
              <option key={s.session_id} value={s.session_id}>
                #{s.session_id} ({s.agent_name || 'CodingAgent'})
              </option>
            ))}
          </select>

          <button
            onClick={() => loadDecisions(selectedSession)}
            className="btn btn-secondary"
            title="Refrescar decisiones"
            style={{ padding: '7px 12px', fontSize: '12px' }}
          >
            <RefreshCw size={13} className={loading ? 'spin' : ''} />
            Actualizar
          </button>
        </div>
      </div>

      {/* Top KPI Summary Cards */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))',
        gap: '12px',
      }}>
        <div style={{
          backgroundColor: '#121824',
          borderRadius: '8px',
          border: '1px solid #1e293b',
          padding: '14px 16px',
        }}>
          <span style={{ fontSize: '11px', color: '#8b949e', display: 'block' }}>Decisiones Evaluadas</span>
          <span style={{ fontSize: '20px', fontWeight: '700', color: '#f0f6fc', fontFamily: 'var(--font-mono)' }}>
            {totalCount}
          </span>
        </div>

        <div style={{
          backgroundColor: '#121824',
          borderRadius: '8px',
          border: '1px solid rgba(63, 185, 80, 0.25)',
          padding: '14px 16px',
        }}>
          <span style={{ fontSize: '11px', color: '#3fb950', display: 'block' }}>Autorizadas (ALLOW)</span>
          <span style={{ fontSize: '20px', fontWeight: '700', color: '#3fb950', fontFamily: 'var(--font-mono)' }}>
            {allowCount}
          </span>
        </div>

        <div style={{
          backgroundColor: '#121824',
          borderRadius: '8px',
          border: '1px solid rgba(210, 153, 34, 0.25)',
          padding: '14px 16px',
        }}>
          <span style={{ fontSize: '11px', color: '#d29922', display: 'block' }}>Revisión HITL (REVIEW)</span>
          <span style={{ fontSize: '20px', fontWeight: '700', color: '#d29922', fontFamily: 'var(--font-mono)' }}>
            {reviewCount}
          </span>
        </div>

        <div style={{
          backgroundColor: '#121824',
          borderRadius: '8px',
          border: '1px solid rgba(192, 132, 252, 0.25)',
          padding: '14px 16px',
        }}>
          <span style={{ fontSize: '11px', color: '#c084fc', display: 'block' }}>Replaniadas (REPLAN)</span>
          <span style={{ fontSize: '20px', fontWeight: '700', color: '#c084fc', fontFamily: 'var(--font-mono)' }}>
            {replanCount}
          </span>
        </div>

        <div style={{
          backgroundColor: '#121824',
          borderRadius: '8px',
          border: '1px solid rgba(248, 81, 73, 0.25)',
          padding: '14px 16px',
        }}>
          <span style={{ fontSize: '11px', color: '#f85149', display: 'block' }}>Bloqueadas (BLOCKED)</span>
          <span style={{ fontSize: '20px', fontWeight: '700', color: '#f85149', fontFamily: 'var(--font-mono)' }}>
            {blockedCount}
          </span>
        </div>
      </div>

      {/* Controls Bar: Search & Status Filters */}
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
            placeholder="Buscar por comando, herramienta, ID o hash..."
            value={filterText}
            onChange={(e) => setFilterText(e.target.value)}
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

        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          {/* Risk Level Filter */}
          <select
            value={riskFilter}
            onChange={(e) => setRiskFilter(e.target.value)}
            style={{
              padding: '6px 10px',
              borderRadius: '6px',
              backgroundColor: '#121824',
              border: '1px solid #1e293b',
              color: '#cbd5e1',
              fontSize: '11px',
              outline: 'none',
            }}
          >
            <option value="ALL">Todo Riesgo</option>
            <option value="LOW">Riesgo Bajo (LOW)</option>
            <option value="MEDIUM">Riesgo Medio (MEDIUM)</option>
            <option value="HIGH">Riesgo Alto (HIGH)</option>
            <option value="CRITICAL">Riesgo Crítico (CRITICAL)</option>
          </select>

          {/* Status Filter Tabs */}
          <div style={{
            display: 'flex',
            backgroundColor: '#121824',
            borderRadius: '8px',
            border: '1px solid #1e293b',
            padding: '3px',
            gap: '2px',
          }}>
            {['ALL', 'ALLOW', 'REVIEW', 'REPLAN', 'BLOCKED'].map((st) => (
              <button
                key={st}
                onClick={() => setStatusFilter(st)}
                style={{
                  padding: '5px 12px',
                  borderRadius: '6px',
                  border: 'none',
                  background: statusFilter === st ? '#1e2a3c' : 'transparent',
                  color: statusFilter === st ? '#f8fafc' : '#73849c',
                  fontSize: '11px',
                  fontWeight: '600',
                  cursor: 'pointer',
                  transition: 'all 0.15s ease',
                }}
              >
                {st}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Decisions Table */}
      <div style={{
        backgroundColor: '#111722',
        borderRadius: '10px',
        border: '1px solid #1e293b',
        overflow: 'hidden',
      }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '12px' }}>
          <thead>
            <tr style={{ backgroundColor: '#0c111a', borderBottom: '1px solid #1a2333', color: '#64748b' }}>
              <th style={{ padding: '12px 16px', fontWeight: '600', width: '90px' }}>ID / Seq</th>
              <th style={{ padding: '12px 16px', fontWeight: '600', width: '100px' }}>Estado</th>
              <th style={{ padding: '12px 16px', fontWeight: '600', width: '130px' }}>Herramienta</th>
              <th style={{ padding: '12px 16px', fontWeight: '600' }}>Acción / Comando</th>
              <th style={{ padding: '12px 16px', fontWeight: '600', width: '95px' }}>Riesgo</th>
              <th style={{ padding: '12px 16px', fontWeight: '600', width: '130px' }}>Supervisor</th>
              <th style={{ padding: '12px 16px', fontWeight: '600', width: '120px' }}>Recibo HMAC</th>
              <th style={{ padding: '12px 16px', fontWeight: '600', width: '90px', textAlign: 'right' }}>Acción</th>
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

                return (
                  <tr
                    key={decId}
                    style={{
                      borderBottom: '1px solid #16202f',
                      backgroundColor: idx % 2 === 0 ? 'transparent' : '#0e141f',
                      transition: 'background 0.15s ease',
                    }}
                  >
                    {/* Seq / ID */}
                    <td style={{ padding: '12px 16px', fontFamily: 'var(--font-mono)', color: '#8b949e' }}>
                      <span title={decId} style={{ cursor: 'pointer' }}>
                        #{d.sequence || shortId}
                      </span>
                    </td>

                    {/* Status Badge */}
                    <td style={{ padding: '12px 16px' }}>
                      {status === 'ALLOW' ? (
                        <span className="badge badge-success">ALLOW</span>
                      ) : status === 'REVIEW' ? (
                        <span className="badge badge-warning">REVIEW</span>
                      ) : status === 'REPLAN' ? (
                        <span
                          className="badge"
                          style={{
                            backgroundColor: 'rgba(168, 85, 247, 0.15)',
                            color: '#c084fc',
                            border: '1px solid rgba(168, 85, 247, 0.35)',
                            padding: '2px 8px',
                            borderRadius: '4px',
                            fontSize: '11px',
                            fontWeight: 600,
                          }}
                        >
                          REPLAN
                        </span>
                      ) : (
                        <span className="badge badge-danger">BLOCKED</span>
                      )}
                    </td>

                    {/* Tool */}
                    <td style={{ padding: '12px 16px' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', color: '#cbd5e1', fontWeight: '500' }}>
                        {getToolIcon(d.tool)}
                        <span>{d.tool || 'system'}</span>
                      </div>
                    </td>

                    {/* Action / Command */}
                    <td style={{ padding: '12px 16px' }}>
                      <div
                        style={{
                          fontFamily: 'var(--font-mono)',
                          color: '#e2e8f0',
                          fontSize: '11.5px',
                          maxWidth: '420px',
                          maxHeight: '65px',
                          overflowX: 'auto',
                          overflowY: 'auto',
                          whiteSpace: 'pre-wrap',
                          wordBreak: 'break-word',
                          lineHeight: '1.4',
                        }}
                        title={d.command || d.description || ''}
                      >
                        {d.command || d.description || '(Acción sin comando especificado)'}
                      </div>
                    </td>

                    {/* Risk */}
                    <td style={{ padding: '12px 16px' }}>
                      <span style={{
                        fontSize: '11px',
                        fontWeight: '700',
                        color: risk === 'CRITICAL' || risk === 'HIGH' ? '#f87171' : risk === 'MEDIUM' ? '#fbbf24' : '#34d399',
                      }}>
                        {risk}
                      </span>
                    </td>

                    {/* Provider / Grounding */}
                    <td style={{ padding: '12px 16px', color: '#94a3b8', fontSize: '11.5px' }}>
                      <div>{d.provider || 'PolicyEngine'}</div>
                      {d.grounding_score !== undefined && d.grounding_score !== null && (
                        <div style={{ fontSize: '10px', color: '#64748b' }}>
                          Grounded: {(Number(d.grounding_score) * 100).toFixed(0)}%
                        </div>
                      )}
                    </td>

                    {/* HMAC Receipt */}
                    <td style={{ padding: '12px 16px' }}>
                      {sig ? (
                        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                          <span
                            onClick={() => setActiveReceiptModal(d)}
                            title="Ver recibo criptográfico firmado"
                            style={{
                              fontFamily: 'var(--font-mono)',
                              fontSize: '10.5px',
                              color: '#58a6ff',
                              cursor: 'pointer',
                              textDecoration: 'underline',
                            }}
                          >
                            {sig.slice(0, 10)}…
                          </span>
                          <button
                            onClick={() => copyToClipboard(sig, decId)}
                            style={{
                              background: 'none',
                              border: 'none',
                              color: copiedId === decId ? '#3fb950' : '#64748b',
                              cursor: 'pointer',
                              padding: '2px',
                            }}
                            title="Copiar hash HMAC"
                          >
                            {copiedId === decId ? <Check size={12} /> : <Copy size={12} />}
                          </button>
                        </div>
                      ) : (
                        <span style={{ color: '#64748b', fontSize: '11px' }}>Sin firma</span>
                      )}
                    </td>

                    {/* Action Button */}
                    <td style={{ padding: '12px 16px', textAlign: 'right' }}>
                      <button
                        onClick={() => onSelectDecision?.(decId, d.session_id)}
                        className="btn btn-secondary"
                        style={{ padding: '4px 10px', fontSize: '11px' }}
                        title="Inspeccionar en Canvas Live"
                      >
                        Ver
                        <ArrowRight size={11} />
                      </button>
                    </td>
                  </tr>
                );
              })
            ) : (
              <tr>
                <td colSpan={8} style={{ padding: '40px 16px', textAlign: 'center', color: '#73849c' }}>
                  <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '10px' }}>
                    <Shield size={28} style={{ color: '#334155' }} />
                    <span style={{ fontSize: '13px' }}>
                      {loading ? 'Cargando decisiones desde el repositorio...' : 'No hay decisiones registradas que coincidan con el filtro.'}
                    </span>
                  </div>
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Quick Cryptographic Receipt Modal */}
      {activeReceiptModal && (
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
            maxWidth: '520px',
            boxShadow: '0 20px 40px rgba(0, 0, 0, 0.6)',
            padding: '24px',
          }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '16px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <Key size={16} style={{ color: '#3fb950' }} />
                <h3 style={{ fontSize: '14px', fontWeight: '700', color: '#f8fafc' }}>
                  Recibo Criptográfico HMAC-SHA256
                </h3>
              </div>
              <button
                onClick={() => setActiveReceiptModal(null)}
                style={{ background: 'none', border: 'none', color: '#64748b', cursor: 'pointer', fontSize: '16px' }}
              >
                ✕
              </button>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', fontSize: '12px' }}>
              <div style={{ backgroundColor: '#0a0e16', padding: '10px 12px', borderRadius: '6px', border: '1px solid #1a2333' }}>
                <span style={{ color: '#8b949e', fontSize: '10.5px', display: 'block' }}>Decisión ID</span>
                <span style={{ color: '#f0f6fc', fontFamily: 'var(--font-mono)' }}>{activeReceiptModal.decision_id || activeReceiptModal.id}</span>
              </div>

              <div style={{ backgroundColor: '#0a0e16', padding: '10px 12px', borderRadius: '6px', border: '1px solid #1a2333' }}>
                <span style={{ color: '#8b949e', fontSize: '10.5px', display: 'block' }}>Firma HMAC</span>
                <span style={{ color: '#3fb950', fontFamily: 'var(--font-mono)', wordBreak: 'break-all', fontSize: '11px' }}>
                  {activeReceiptModal.signature || 'Firma generada en tiempo de ejecución'}
                </span>
              </div>

              <div style={{ backgroundColor: '#0a0e16', padding: '10px 12px', borderRadius: '6px', border: '1px solid #1a2333' }}>
                <span style={{ color: '#8b949e', fontSize: '10.5px', display: 'block' }}>Action Hash</span>
                <span style={{ color: '#58a6ff', fontFamily: 'var(--font-mono)', wordBreak: 'break-all', fontSize: '11px' }}>
                  {activeReceiptModal.action_hash || 'SHA256(canonical_action_payload)'}
                </span>
              </div>

              <div style={{ backgroundColor: '#0a0e16', padding: '10px 12px', borderRadius: '6px', border: '1px solid #1a2333' }}>
                <span style={{ color: '#8b949e', fontSize: '10.5px', display: 'block' }}>Sandbox Mode & Status</span>
                <span style={{ color: '#cbd5e1' }}>
                  {activeReceiptModal.execution_mode || 'local_restricted'} · Estado: <strong>{activeReceiptModal.status}</strong>
                </span>
              </div>
            </div>

            <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '18px' }}>
              <button
                onClick={() => {
                  const id = activeReceiptModal.decision_id || activeReceiptModal.id;
                  const sid = activeReceiptModal.session_id;
                  setActiveReceiptModal(null);
                  onSelectDecision?.(id, sid);
                }}
                className="btn btn-primary"
                style={{ padding: '6px 14px', fontSize: '11.5px' }}
              >
                Abrir en Decision Inspector
                <ArrowRight size={12} />
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
