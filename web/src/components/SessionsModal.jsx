import React, { useState } from 'react';
import { X, Plus, ArrowRight, Trash2, Sparkles } from 'lucide-react';

export default function SessionsModal({
  isOpen,
  onClose,
  sessions = [],
  currentSessionId,
  onSelectSession,
  onCreateSession,
  onDeleteSession,
  onNewCleanSession,
}) {
  const [newGoal, setNewGoal] = useState('');
  const [isCreating, setIsCreating] = useState(false);

  if (!isOpen) return null;

  const handleCreate = async (e) => {
    e.preventDefault();
    if (!newGoal.trim()) return;
    setIsCreating(true);
    try {
      await onCreateSession(newGoal.trim());
      setNewGoal('');
      onClose();
    } catch (err) {
      alert(`Error creando sesión: ${err.message}`);
    } finally {
      setIsCreating(false);
    }
  };

  return (
    <div style={{
      position: 'fixed',
      top: 0,
      left: 0,
      right: 0,
      bottom: 0,
      backgroundColor: 'rgba(0, 0, 0, 0.65)',
      backdropFilter: 'blur(4px)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      zIndex: 100,
    }}>
      <div style={{
        width: '500px',
        backgroundColor: '#121824',
        border: '1px solid #233146',
        borderRadius: '10px',
        padding: '20px',
        boxShadow: 'var(--shadow-lg)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '16px' }}>
          <h2 style={{ fontSize: '15px', fontWeight: '700', color: '#f8fafc' }}>
            Sesiones de Supervisión
          </h2>
          <button
            onClick={onClose}
            style={{ background: 'none', border: 'none', color: '#64748b', cursor: 'pointer' }}
          >
            <X size={18} />
          </button>
        </div>

        {/* Create Session Form & Clean Session Shortcut */}
        <div style={{ marginBottom: '16px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
          <form onSubmit={handleCreate} style={{ display: 'flex', gap: '8px' }}>
            <input
              type="text"
              placeholder="Nuevo objetivo de supervisión..."
              value={newGoal}
              onChange={(e) => setNewGoal(e.target.value)}
              style={{
                flex: 1,
                padding: '8px 12px',
                borderRadius: '6px',
                backgroundColor: '#0a0e16',
                border: '1px solid #1e293b',
                color: '#f8fafc',
                fontSize: '12px',
                outline: 'none',
              }}
            />
            <button
              type="submit"
              disabled={isCreating || !newGoal.trim()}
              className="btn btn-primary"
            >
              <Plus size={14} />
              Crear
            </button>
          </form>

          {onNewCleanSession && (
            <button
              type="button"
              onClick={() => {
                onNewCleanSession();
                onClose();
              }}
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: '6px',
                padding: '6px 12px',
                borderRadius: '6px',
                backgroundColor: 'rgba(88, 166, 255, 0.1)',
                border: '1px solid rgba(88, 166, 255, 0.3)',
                color: '#58a6ff',
                fontSize: '11.5px',
                fontWeight: '600',
                cursor: 'pointer',
                transition: 'all 0.15s ease',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.backgroundColor = 'rgba(88, 166, 255, 0.2)';
                e.currentTarget.style.borderColor = '#58a6ff';
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.backgroundColor = 'rgba(88, 166, 255, 0.1)';
                e.currentTarget.style.borderColor = 'rgba(88, 166, 255, 0.3)';
              }}
            >
              <Sparkles size={13} />
              <span>Abrir Nueva Sesión Limpia (Grafo y Chat en blanco)</span>
            </button>
          )}
        </div>

        {/* Existing Sessions List */}
        <div style={{
          maxHeight: '260px',
          overflowY: 'auto',
          display: 'flex',
          flexDirection: 'column',
          gap: '8px',
        }}>
          {sessions.length === 0 ? (
            <div style={{ textAlign: 'center', padding: '24px 12px', color: '#64748b', fontSize: '12px' }}>
              No hay sesiones previas registradas.
            </div>
          ) : (
            sessions.map((s) => {
              const isSelected = s.session_id === currentSessionId;
              return (
                <div
                  key={s.session_id}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    padding: '10px 12px',
                    borderRadius: '6px',
                    backgroundColor: isSelected ? '#1c2432' : '#141924',
                    border: `1px solid ${isSelected ? '#58a6ff' : '#1e2636'}`,
                    boxShadow: 'var(--shadow-clay-sm)',
                    transition: 'all 0.15s ease',
                  }}
                >
                  <div
                    onClick={() => {
                      onSelectSession(s.session_id);
                      onClose();
                    }}
                    style={{ flex: 1, cursor: 'pointer', marginRight: '10px' }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                      <span style={{ fontSize: '12px', fontWeight: '600', color: isSelected ? '#58a6ff' : '#f0f6fc' }}>
                        #{s.session_id}
                      </span>
                      <span className="badge badge-success">{s.status || 'Active'}</span>
                    </div>
                    <div style={{
                      fontSize: '11px',
                      color: '#8b949e',
                      marginTop: '3px',
                      display: '-webkit-box',
                      WebkitLineClamp: 2,
                      WebkitBoxOrient: 'vertical',
                      overflow: 'hidden',
                    }}>
                      {s.goal || 'Sin objetivo'}
                    </div>
                  </div>

                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                    {onDeleteSession && (
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          if (window.confirm(`¿Eliminar permanentemente la sesión #${s.session_id}?`)) {
                            onDeleteSession(s.session_id);
                          }
                        }}
                        title="Eliminar sesión"
                        style={{
                          background: 'transparent',
                          border: 'none',
                          color: '#64748b',
                          cursor: 'pointer',
                          padding: '4px',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          borderRadius: '4px',
                        }}
                        onMouseEnter={(e) => {
                          e.currentTarget.style.color = '#f85149';
                          e.currentTarget.style.backgroundColor = 'rgba(248, 81, 73, 0.15)';
                        }}
                        onMouseLeave={(e) => {
                          e.currentTarget.style.color = '#64748b';
                          e.currentTarget.style.backgroundColor = 'transparent';
                        }}
                      >
                        <Trash2 size={13} />
                      </button>
                    )}

                    <div
                      onClick={() => {
                        onSelectSession(s.session_id);
                        onClose();
                      }}
                      style={{ cursor: 'pointer', display: 'flex', alignItems: 'center' }}
                    >
                      <ArrowRight size={14} style={{ color: isSelected ? '#58a6ff' : '#6b7280' }} />
                    </div>
                  </div>
                </div>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}
