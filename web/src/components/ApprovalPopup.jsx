import React, { useEffect, useRef, useState } from 'react';

/**
 * Ventana emergente de aprobación humana (RD-6.3.3). Flota justo encima del compositor.
 * No se cierra con Esc ni al pulsar fuera: exige una decisión explícita (RD-A2).
 * approvals: [{ nodeId, decisionId, tool, command, riskLevel, laya }]
 */
export default function ApprovalPopup({ approvals = [], onApprove, onReject, onViewDetail }) {
  const current = approvals[0];
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const rejectBtnRef = useRef(null);
  const reasonRef = useRef(null);
  const currentId = current?.decisionId;

  // Cada solicitud nueva empieza sin rechazo en curso y con el foco en la opción segura (Rechazar)
  useEffect(() => {
    setRejecting(false);
    setReason('');
    setError('');
    setBusy(false);
    if (currentId) setTimeout(() => rejectBtnRef.current?.focus(), 0);
  }, [currentId]);

  useEffect(() => {
    if (rejecting) setTimeout(() => reasonRef.current?.focus(), 0);
  }, [rejecting]);

  if (!current) return null;

  const command = (current.command || '').replace(/\s+/g, ' ').trim();
  const shortCommand = command.length > 110 ? `${command.slice(0, 107)}…` : command;

  const approve = async () => {
    setBusy(true);
    try { await onApprove?.(current); } finally { setBusy(false); }
  };
  const confirmReject = async () => {
    if (!reason.trim()) {
      setError('Escribe el motivo del rechazo para continuar.');
      return;
    }
    setBusy(true);
    try { await onReject?.(current, reason.trim()); } finally { setBusy(false); }
  };

  return (
    <div style={{ position: 'absolute', left: '24px', right: '24px', bottom: 'calc(100% - 8px)', zIndex: 15, pointerEvents: 'none' }}>
      <div
        role="alertdialog"
        aria-label="Necesita tu aprobación"
        aria-describedby="approval-desc"
        className="rise"
        style={{
          pointerEvents: 'auto',
          margin: '0 auto',
          width: 'min(720px, 100%)',
          background: 'var(--surface)',
          border: '1px solid var(--muted)',
          borderRadius: 'var(--r-panel)',
          padding: '14px 16px',
          display: 'flex',
          flexDirection: 'column',
          gap: '10px',
          boxShadow: '0 -10px 30px rgba(0, 0, 0, 0.35)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '16px', flexWrap: 'wrap' }}>
          <div id="approval-desc" style={{ flex: '1 1 280px', minWidth: 0 }}>
            <div style={{ fontWeight: 600 }}>
              Necesita tu aprobación
              {approvals.length > 1 && <span style={{ color: 'var(--muted)', fontWeight: 400 }}> · 1 de {approvals.length}</span>}
            </div>
            <div style={{ color: 'var(--muted)', fontSize: 'var(--fs-secondary)', overflowWrap: 'anywhere' }}>
              Ejecutar <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text)' }}>{shortCommand.startsWith(String(current.tool || '')) ? shortCommand : `${current.tool || ''} ${shortCommand}`.trim()}</span>
              {current.riskLevel ? ` · riesgo ${String(current.riskLevel).toLowerCase()}` : ''}
              {current.laya !== undefined && current.laya !== null ? ` · LAYA ${current.laya}` : ''}
              {' · '}
              <button type="button" onClick={() => onViewDetail?.(current)} style={{ background: 'none', border: 0, padding: 0, color: 'var(--muted)', textDecoration: 'underline', textUnderlineOffset: '3px' }}>
                Ver detalle
              </button>
            </div>
          </div>
          {!rejecting && (
            <div style={{ display: 'flex', gap: '8px', flex: 'none' }}>
              <button ref={rejectBtnRef} type="button" className="btn btn-secondary" disabled={busy} onClick={() => setRejecting(true)}>
                Rechazar
              </button>
              <button type="button" className="btn btn-primary" disabled={busy} onClick={approve}>
                Aprobar
              </button>
            </div>
          )}
        </div>

        {rejecting && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            <label htmlFor="approval-reason" style={{ fontSize: 'var(--fs-secondary)', color: 'var(--muted)' }}>
              Motivo del rechazo (obligatorio)
            </label>
            <input
              id="approval-reason"
              ref={reasonRef}
              type="text"
              value={reason}
              onChange={(e) => { setReason(e.target.value); setError(''); }}
              onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); confirmReject(); } }}
              placeholder="Por qué no debe ejecutarse"
              aria-invalid={Boolean(error)}
              style={{ borderColor: error ? 'var(--bad)' : undefined }}
            />
            {error && <div style={{ color: 'var(--bad)', fontSize: 'var(--fs-secondary)' }}>{error}</div>}
            <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
              <button type="button" className="btn btn-secondary" onClick={() => { setRejecting(false); setError(''); }}>
                Cancelar
              </button>
              <button type="button" className="btn btn-danger" disabled={busy} onClick={confirmReject}>
                Confirmar rechazo
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
