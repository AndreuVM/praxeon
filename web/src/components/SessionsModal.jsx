import React, { useState } from 'react';
import { Plus, ArrowRight, Trash2 } from 'lucide-react';
import { notify, confirmDialog } from './Dialogs';
import { Modal, Chip, EmptyState } from './ui';

const STATUS_LABEL = { Active: 'Activa', Paused: 'Pausada', Completed: 'Completada' };

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

  const handleCreate = async (e) => {
    e.preventDefault();
    if (!newGoal.trim()) return;
    setIsCreating(true);
    try {
      await onCreateSession(newGoal.trim());
      setNewGoal('');
      onClose();
    } catch (err) {
      notify(`No se pudo crear la sesión: ${err.message}`);
    } finally {
      setIsCreating(false);
    }
  };

  return (
    <Modal open={isOpen} title="Sesiones registradas" onClose={onClose} maxWidth={560}>
      <form onSubmit={handleCreate} style={{ display: 'flex', gap: '8px' }}>
        <input
          type="text"
          value={newGoal}
          onChange={(e) => setNewGoal(e.target.value)}
          placeholder="Objetivo de una nueva misión…"
          aria-label="Objetivo de una nueva misión"
          style={{ flex: 1, minWidth: 0 }}
        />
        <button type="submit" disabled={isCreating || !newGoal.trim()} className="btn btn-primary">
          <Plus size={14} />
          Crear
        </button>
      </form>

      {onNewCleanSession && (
        <button
          type="button"
          className="btn btn-secondary"
          onClick={() => {
            onNewCleanSession();
            onClose();
          }}
        >
          Abrir una sesión limpia (conversación y mapa en blanco)
        </button>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', maxHeight: '340px', overflowY: 'auto', margin: '0 -4px' }}>
        {sessions.length === 0 ? (
          <EmptyState title="Aún no hay sesiones">Escribe un objetivo arriba para lanzar la primera misión supervisada.</EmptyState>
        ) : (
          sessions.map((s) => {
            const isSelected = s.session_id === currentSessionId;
            return (
              <div
                key={s.session_id}
                style={{ display: 'flex', alignItems: 'center', gap: '10px', padding: '10px 4px', borderBottom: '1px solid var(--line)' }}
              >
                <button
                  type="button"
                  onClick={() => {
                    onSelectSession(s.session_id);
                  }}
                  style={{ flex: 1, minWidth: 0, textAlign: 'left', background: 'none', border: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: '3px' }}
                >
                  <span style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-secondary)', fontWeight: isSelected ? 600 : 400 }}>#{s.session_id}</span>
                    <Chip tone={(s.status || 'Active') === 'Active' ? 'ok' : undefined}>{STATUS_LABEL[s.status || 'Active'] || s.status}</Chip>
                    {isSelected && <span style={{ fontSize: 'var(--fs-label)', color: 'var(--muted)' }}>abierta</span>}
                  </span>
                  <span style={{ color: 'var(--muted)', fontSize: 'var(--fs-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {s.goal || 'Sin objetivo'}
                  </span>
                </button>
                {onDeleteSession && (
                  <button
                    type="button"
                    className="btn-icon"
                    title="Eliminar esta sesión"
                    aria-label={`Eliminar la sesión ${s.session_id}`}
                    onClick={async (e) => {
                      e.stopPropagation();
                      if (await confirmDialog(`¿Eliminar permanentemente la sesión #${s.session_id}?`, { confirmLabel: 'Eliminar', danger: true })) {
                        onDeleteSession(s.session_id);
                      }
                    }}
                  >
                    <Trash2 size={14} strokeWidth={1.6} />
                  </button>
                )}
                <button type="button" className="btn-icon" title="Abrir la sesión" aria-label={`Abrir la sesión ${s.session_id}`} onClick={() => onSelectSession(s.session_id)}>
                  <ArrowRight size={14} strokeWidth={1.6} />
                </button>
              </div>
            );
          })
        )}
      </div>
    </Modal>
  );
}
