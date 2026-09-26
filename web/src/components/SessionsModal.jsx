import React, { useState } from 'react';
import { X, Plus, FolderKanban, ArrowRight } from 'lucide-react';

export default function SessionsModal({
  isOpen,
  onClose,
  sessions = [],
  currentSessionId,
  onSelectSession,
  onCreateSession,
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

        {/* Create Session Form */}
        <form onSubmit={handleCreate} style={{ display: 'flex', gap: '8px', marginBottom: '16px' }}>
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

        {/* Existing Sessions List */}
        <div style={{
          maxHeight: '260px',
          overflowY: 'auto',
          display: 'flex',
          flexDirection: 'column',
          gap: '8px',
        }}>
          {sessions.map((s) => {
            const isSelected = s.session_id === currentSessionId;
            return (
              <div
                key={s.session_id}
                onClick={() => {
                  onSelectSession(s.session_id);
                  onClose();
                }}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  padding: '10px 12px',
                  borderRadius: '6px',
                  backgroundColor: isSelected ? '#1a2436' : '#0e141f',
                  border: `1px solid ${isSelected ? '#38bdf8' : '#1e293b'}`,
                  cursor: 'pointer',
                  transition: 'all 0.15s ease',
                }}
              >
                <div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <span style={{ fontSize: '12px', fontWeight: '600', color: '#f8fafc' }}>
                      #{s.session_id}
                    </span>
                    <span className="badge badge-success">{s.status || 'Active'}</span>
                  </div>
                  <div style={{ fontSize: '11px', color: '#8595a8', marginTop: '3px' }}>
                    {s.goal}
                  </div>
                </div>
                <ArrowRight size={14} style={{ color: isSelected ? '#38bdf8' : '#475569' }} />
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
