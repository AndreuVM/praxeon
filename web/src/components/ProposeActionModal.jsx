import React, { useState } from 'react';
import { X, Play, AlertTriangle } from 'lucide-react';

export default function ProposeActionModal({ isOpen, onClose, onSubmit }) {
  const [tool, setTool] = useState('run_command');
  const [command, setCommand] = useState('pytest tests/test_security.py');
  const [rationale, setRationale] = useState('Execute regression test suite before deploy');
  const [isSubmitting, setIsSubmitting] = useState(false);

  if (!isOpen) return null;

  const handleSubmit = async (e) => {
    e.preventDefault();
    setIsSubmitting(true);
    try {
      await onSubmit({
        tool,
        operation: command,
        arguments: { command },
        thought_rationale: rationale,
        provenance: { source: 'UserUI', step: 1 },
      });
      onClose();
    } catch (err) {
      alert(`Error al proponer acción: ${err.message}`);
    } finally {
      setIsSubmitting(false);
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
        width: '460px',
        backgroundColor: '#121824',
        border: '1px solid #233146',
        borderRadius: '10px',
        padding: '20px',
        boxShadow: 'var(--shadow-lg)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '16px' }}>
          <h2 style={{ fontSize: '15px', fontWeight: '700', color: '#f8fafc' }}>
            Proponer Acción a Supervisión
          </h2>
          <button
            onClick={onClose}
            style={{ background: 'none', border: 'none', color: '#64748b', cursor: 'pointer' }}
          >
            <X size={18} />
          </button>
        </div>

        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
          <div>
            <label style={{ fontSize: '11.5px', color: '#94a3b8', display: 'block', marginBottom: '4px' }}>
              Herramienta (Tool)
            </label>
            <select
              value={tool}
              onChange={(e) => setTool(e.target.value)}
              style={{
                width: '100%',
                padding: '8px 10px',
                borderRadius: '6px',
                backgroundColor: '#0a0e16',
                border: '1px solid #1e293b',
                color: '#f8fafc',
                fontSize: '12.5px',
                outline: 'none',
              }}
            >
              <option value="run_command">run_command (Shell/Terminal)</option>
              <option value="read_file">read_file (Filesystem read)</option>
              <option value="edit_file">edit_file (Filesystem write)</option>
              <option value="search_web">search_web (Network read)</option>
              <option value="git_push">git_push (Remote egress)</option>
            </select>
          </div>

          <div>
            <label style={{ fontSize: '11.5px', color: '#94a3b8', display: 'block', marginBottom: '4px' }}>
              Comando / Argumentos
            </label>
            <input
              type="text"
              value={command}
              onChange={(e) => setCommand(e.target.value)}
              style={{
                width: '100%',
                padding: '8px 10px',
                borderRadius: '6px',
                backgroundColor: '#0a0e16',
                border: '1px solid #1e293b',
                color: '#f8fafc',
                fontSize: '12px',
                fontFamily: 'var(--font-mono)',
                outline: 'none',
              }}
              required
            />
          </div>

          <div>
            <label style={{ fontSize: '11.5px', color: '#94a3b8', display: 'block', marginBottom: '4px' }}>
              Justificación Cognitiva (Rationale)
            </label>
            <textarea
              value={rationale}
              onChange={(e) => setRationale(e.target.value)}
              rows={2}
              style={{
                width: '100%',
                padding: '8px 10px',
                borderRadius: '6px',
                backgroundColor: '#0a0e16',
                border: '1px solid #1e293b',
                color: '#f8fafc',
                fontSize: '12px',
                outline: 'none',
                resize: 'none',
              }}
            />
          </div>

          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            padding: '8px 10px',
            borderRadius: '6px',
            backgroundColor: 'rgba(56, 189, 248, 0.08)',
            border: '1px solid rgba(56, 189, 248, 0.2)',
            fontSize: '11px',
            color: '#7dd3fc',
          }}>
            <AlertTriangle size={14} style={{ flexShrink: 0 }} />
            <span>
              La propuesta pasará por el pipeline estricto: Evidencia empírica, Riesgo operacional, Evaluación de modelo y PolicyEngine.
            </span>
          </div>

          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px', marginTop: '6px' }}>
            <button
              type="button"
              onClick={onClose}
              className="btn btn-secondary"
            >
              Cancelar
            </button>
            <button
              type="submit"
              disabled={isSubmitting}
              className="btn btn-primary"
            >
              <Play size={12} />
              {isSubmitting ? 'Evaluando...' : 'Evaluar Propuesta'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
