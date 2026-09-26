import React, { useState } from 'react';
import { FolderKanban, Plus, Clock, Cpu, Check, X, AlertTriangle, ArrowRight, Search } from 'lucide-react';

export default function SessionsView({
  sessions = [],
  currentSessionId,
  onSelectSession,
  onCreateSession,
}) {
  const [filter, setFilter] = useState('');
  const [newGoal, setNewGoal] = useState('');

  const filteredSessions = sessions.filter((s) =>
    (s.goal || '').toLowerCase().includes(filter.toLowerCase()) ||
    (s.session_id || '').toLowerCase().includes(filter.toLowerCase())
  );

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
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div>
          <h1 style={{ fontSize: '18px', fontWeight: '700', color: '#f8fafc' }}>
            Sesiones de Supervisión
          </h1>
          <p style={{ fontSize: '12px', color: '#73849c', marginTop: '4px' }}>
            Historial de sesiones activas e históricas auditadas por el runtime de PRAXEON.
          </p>
        </div>

        {/* Create Session Quick Form */}
        <div style={{ display: 'flex', gap: '8px' }}>
          <input
            type="text"
            placeholder="Nuevo objetivo de supervisión..."
            value={newGoal}
            onChange={(e) => setNewGoal(e.target.value)}
            style={{
              width: '280px',
              padding: '7px 12px',
              borderRadius: '6px',
              backgroundColor: '#121824',
              border: '1px solid #1e293b',
              color: '#f8fafc',
              fontSize: '12px',
              outline: 'none',
            }}
          />
          <button
            onClick={() => {
              if (newGoal.trim()) {
                onCreateSession(newGoal.trim());
                setNewGoal('');
              }
            }}
            disabled={!newGoal.trim()}
            className="btn btn-primary"
          >
            <Plus size={14} />
            Nueva Sesión
          </button>
        </div>
      </div>

      {/* Filter / Search Bar */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: '10px',
        padding: '8px 14px',
        backgroundColor: '#121824',
        borderRadius: '8px',
        border: '1px solid #1e293b',
        maxWidth: '400px',
      }}>
        <Search size={14} style={{ color: '#64748b' }} />
        <input
          type="text"
          placeholder="Filtrar por ID u objetivo..."
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

      {/* Sessions Grid */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))',
        gap: '16px',
      }}>
        {filteredSessions.map((s) => {
          const isSelected = s.session_id === currentSessionId;
          return (
            <div
              key={s.session_id}
              style={{
                backgroundColor: '#141924',
                border: `1px solid ${isSelected ? '#f0f6fc' : '#1e2636'}`,
                borderRadius: '8px',
                padding: '16px',
                display: 'flex',
                flexDirection: 'column',
                justifyContent: 'space-between',
                gap: '14px',
                transition: 'all 0.18s ease',
                boxShadow: isSelected ? '0 0 10px rgba(240, 246, 252, 0.12)' : 'var(--shadow-clay-sm)',
              }}
            >
              <div>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <span style={{ fontSize: '13px', fontWeight: '700', color: '#f0f6fc', fontFamily: 'var(--font-mono)' }}>
                      #{s.session_id}
                    </span>
                    <span className="badge badge-success">{s.status || 'Active'}</span>
                  </div>
                  <span style={{ fontSize: '11px', color: '#8b949e' }}>
                    {s.created_at ? new Date(s.created_at).toLocaleTimeString() : 'En curso'}
                  </span>
                </div>

                <p style={{ fontSize: '12.5px', color: '#c9d1d9', lineHeight: '1.4' }}>
                  {s.goal || 'Sin objetivo especificado'}
                </p>
              </div>

              <div style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                borderTop: '1px solid #1a202c',
                paddingTop: '12px',
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '12px', fontSize: '11px', color: '#8b949e' }}>
                  <span>{s.steps_count || 12} decisiones</span>
                  <span>·</span>
                  <span style={{ color: '#3fb950' }}>8 autorizadas</span>
                </div>

                <button
                  onClick={() => onSelectSession(s.session_id)}
                  className="btn btn-secondary"
                  style={{
                    padding: '4px 10px',
                    fontSize: '11.5px',
                    backgroundColor: isSelected ? '#1c2432' : '#161c26',
                    borderColor: isSelected ? '#f0f6fc' : '#242c3b',
                    color: isSelected ? '#f0f6fc' : '#c9d1d9',
                  }}
                >
                  {isSelected ? 'Inspeccionando' : 'Abrir'}
                  <ArrowRight size={12} />
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
