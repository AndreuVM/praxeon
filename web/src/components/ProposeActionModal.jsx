import React, { useState } from 'react';
import { Play } from 'lucide-react';
import { notify } from './Dialogs';
import { Modal, Field } from './ui';

export default function ProposeActionModal({ isOpen, onClose, onSubmit }) {
  const [tool, setTool] = useState('run_command');
  const [command, setCommand] = useState('pytest tests/test_security.py');
  const [rationale, setRationale] = useState('Ejecutar la suite de regresión antes de desplegar');
  const [isSubmitting, setIsSubmitting] = useState(false);

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
      notify(`No se pudo proponer la acción: ${err.message}`);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <Modal open={isOpen} title="Proponer una acción manual" onClose={onClose} maxWidth={480}>
      <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
        <Field label="Herramienta" htmlFor="pa-tool">
          <select id="pa-tool" value={tool} onChange={(e) => setTool(e.target.value)} style={{ width: '100%' }}>
            <option value="run_command">run_command (shell o terminal)</option>
            <option value="read_file">read_file (lectura del sistema de archivos)</option>
            <option value="edit_file">edit_file (escritura del sistema de archivos)</option>
            <option value="search_web">search_web (lectura de red)</option>
            <option value="git_push">git_push (envío a un remoto)</option>
          </select>
        </Field>

        <Field label="Comando o argumentos" htmlFor="pa-command">
          <input id="pa-command" type="text" value={command} onChange={(e) => setCommand(e.target.value)} required style={{ width: '100%', fontFamily: 'var(--font-mono)' }} />
        </Field>

        <Field label="Justificación (razonamiento)" htmlFor="pa-rationale">
          <textarea id="pa-rationale" rows={3} value={rationale} onChange={(e) => setRationale(e.target.value)} style={{ width: '100%', resize: 'vertical' }} />
        </Field>

        <p style={{ color: 'var(--muted)', fontSize: 'var(--fs-secondary)', lineHeight: 1.5 }}>
          La propuesta pasa por el pipeline completo: evidencia empírica, riesgo operativo, evaluación del modelo y motor de políticas.
        </p>

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px' }}>
          <button type="button" className="btn btn-secondary" onClick={onClose}>Cancelar</button>
          <button type="submit" className="btn btn-primary" disabled={isSubmitting || !command.trim()}>
            <Play size={13} />
            {isSubmitting ? 'Evaluando…' : 'Evaluar la propuesta'}
          </button>
        </div>
      </form>
    </Modal>
  );
}
