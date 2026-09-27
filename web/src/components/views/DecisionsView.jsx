import React, { useState } from 'react';
import { GitFork, Check, X, AlertTriangle, Search, Filter, ArrowRight, Copy } from 'lucide-react';

export default function DecisionsView({
  decisions = [],
  onSelectDecision,
}) {
  const [filterText, setFilterText] = useState('');
  const [statusFilter, setStatusFilter] = useState('ALL');

  // Sample or incoming decisions list
  const allDecisions = decisions.length > 0 ? decisions : [
    {
      id: 'd_7f3a2c-1',
      sequence: 1,
      sessionId: '7f3a2c',
      tool: 'analyze_issue',
      command: 'analyze_codebase',
      risk: 'LOW',
      status: 'ALLOW',
      provider: 'JEV (0.92)',
      time: '14:32:01',
    },
    {
      id: 'd_7f3a2c-2',
      sequence: 2,
      sessionId: '7f3a2c',
      tool: 'plan_solution',
      command: 'create_remediation_plan',
      risk: 'LOW',
      status: 'ALLOW',
      provider: 'JEV (0.88)',
      time: '14:32:05',
    },
    {
      id: 'd_7f3a2c-4',
      sequence: 4,
      sessionId: '7f3a2c',
      tool: 'read_file',
      command: 'read_file: auth/middleware.py',
      risk: 'LOW',
      status: 'ALLOW',
      provider: 'LAYA (0.95)',
      time: '14:32:12',
    },
    {
      id: 'd_7f3a2c-5',
      sequence: 5,
      sessionId: '7f3a2c',
      tool: 'git',
      command: 'git push origin main',
      risk: 'HIGH',
      status: 'BLOCKED',
      provider: 'LAYA (0.81)',
      time: '14:32:18',
    },
    {
      id: 'd_7f3a2c-8',
      sequence: 8,
      sessionId: '7f3a2c',
      tool: 'Terminal',
      command: 'pytest tests/test_auth.py',
      risk: 'MEDIUM',
      status: 'BLOCKED',
      provider: 'JEV (0.89)',
      time: '14:32:31',
    },
    {
      id: 'd_7f3a2c-11',
      sequence: 11,
      sessionId: '7f3a2c',
      tool: 'Web',
      command: 'search_web: oauth2 invalid token response RFC',
      risk: 'MEDIUM',
      status: 'REVIEW',
      provider: 'LAYA (0.76)',
      time: '14:32:22',
    },
    {
      id: 'd_7f3a2c-14',
      sequence: 14,
      sessionId: '7f3a2c',
      tool: 'Env',
      command: 'write_env: .env.production',
      risk: 'CRITICAL',
      status: 'BLOCKED',
      provider: 'TypeSafe (0.35)',
      time: '14:32:26',
    },
  ];

  const filtered = allDecisions.filter((d) => {
    const matchesText =
      (d.command || '').toLowerCase().includes(filterText.toLowerCase()) ||
      (d.tool || '').toLowerCase().includes(filterText.toLowerCase()) ||
      (d.id || '').toLowerCase().includes(filterText.toLowerCase());

    const matchesStatus = statusFilter === 'ALL' || d.status === statusFilter;
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
      gap: '20px',
    }}>
      {/* View Header */}
      <div>
        <h1 style={{ fontSize: '18px', fontWeight: '700', color: '#f8fafc' }}>
          Registro Auditado de Decisiones
        </h1>
        <p style={{ fontSize: '12px', color: '#73849c', marginTop: '4px' }}>
          Trazabilidad inmutable de propuestas, evaluaciones semánticas, políticas y recibos HMAC.
        </p>
      </div>

      {/* Controls Bar */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '16px' }}>
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
            placeholder="Buscar por comando, herramienta o ID..."
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

        {/* Status Filter Tabs */}
        <div style={{
          display: 'flex',
          backgroundColor: '#121824',
          borderRadius: '8px',
          border: '1px solid #1e293b',
          padding: '3px',
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

      {/* Decisions Table */}
      <div style={{
        backgroundColor: '#111722',
        borderRadius: '8px',
        border: '1px solid #1e293b',
        overflow: 'hidden',
      }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '12px' }}>
          <thead>
            <tr style={{ backgroundColor: '#0c111a', borderBottom: '1px solid #1a2333', color: '#64748b' }}>
              <th style={{ padding: '10px 16px', fontWeight: '600', width: '70px' }}>Seq</th>
              <th style={{ padding: '10px 16px', fontWeight: '600', width: '100px' }}>Estado</th>
              <th style={{ padding: '10px 16px', fontWeight: '600', width: '110px' }}>Herramienta</th>
              <th style={{ padding: '10px 16px', fontWeight: '600' }}>Acción / Comando</th>
              <th style={{ padding: '10px 16px', fontWeight: '600', width: '90px' }}>Riesgo</th>
              <th style={{ padding: '10px 16px', fontWeight: '600', width: '120px' }}>Proveedor</th>
              <th style={{ padding: '10px 16px', fontWeight: '600', width: '80px', textAlign: 'right' }}>Acción</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((d, idx) => (
              <tr
                key={d.id || idx}
                style={{
                  borderBottom: '1px solid #16202f',
                  backgroundColor: idx % 2 === 0 ? 'transparent' : '#0e141f',
                  transition: 'background 0.15s ease',
                }}
              >
                <td style={{ padding: '12px 16px', fontFamily: 'var(--font-mono)', color: '#64748b' }}>
                  #{d.sequence}
                </td>
                <td style={{ padding: '12px 16px' }}>
                  {d.status === 'ALLOW' ? (
                    <span className="badge badge-success">ALLOW</span>
                  ) : d.status === 'REVIEW' ? (
                    <span className="badge badge-warning">REVIEW</span>
                  ) : d.status === 'REPLAN' ? (
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
                <td style={{ padding: '12px 16px', color: '#cbd5e1', fontWeight: '500' }}>
                  {d.tool}
                </td>
                <td style={{ padding: '12px 16px', fontFamily: 'var(--font-mono)', color: '#e2e8f0' }}>
                  {d.command}
                </td>
                <td style={{ padding: '12px 16px' }}>
                  <span style={{
                    fontSize: '11px',
                    fontWeight: '700',
                    color: d.risk === 'CRITICAL' || d.risk === 'HIGH' ? '#f87171' : d.risk === 'MEDIUM' ? '#fbbf24' : '#34d399',
                  }}>
                    {d.risk}
                  </span>
                </td>
                <td style={{ padding: '12px 16px', color: '#94a3b8' }}>
                  {d.provider}
                </td>
                <td style={{ padding: '12px 16px', textAlign: 'right' }}>
                  <button
                    onClick={() => onSelectDecision?.(d.id || `node-${d.sequence}`)}
                    className="btn btn-secondary"
                    style={{ padding: '4px 8px', fontSize: '11px' }}
                  >
                    Ver
                    <ArrowRight size={11} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
