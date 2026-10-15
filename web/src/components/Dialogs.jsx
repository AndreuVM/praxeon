import React, { useEffect, useRef, useState } from 'react';

/**
 * Avisos y confirmaciones propios de la aplicación (sustituyen a alert() y confirm() del navegador).
 *   notify('mensaje', { tone: 'error' | 'info' })
 *   const ok = await confirmDialog('¿Eliminar…?', { confirmLabel: 'Eliminar', danger: true })
 * Se pintan desde <DialogsHost />, que vive una sola vez en App.
 */
const bus = new EventTarget();

export function notify(message, { tone = 'error' } = {}) {
  bus.dispatchEvent(new CustomEvent('notify', { detail: { message: String(message), tone } }));
}

export function confirmDialog(message, { title = '', confirmLabel = 'Confirmar', cancelLabel = 'Cancelar', danger = false } = {}) {
  return new Promise((resolve) => {
    bus.dispatchEvent(new CustomEvent('confirm', { detail: { message: String(message), title, confirmLabel, cancelLabel, danger, resolve } }));
  });
}

export default function DialogsHost() {
  const [notices, setNotices] = useState([]);
  const [pending, setPending] = useState(null);
  const cancelRef = useRef(null);
  const confirmRef = useRef(null);
  const idRef = useRef(0);

  useEffect(() => {
    const onNotify = (e) => {
      const id = ++idRef.current;
      setNotices((prev) => [...prev, { id, ...e.detail }]);
      setTimeout(() => setNotices((prev) => prev.filter((n) => n.id !== id)), 7000);
    };
    const onConfirm = (e) => setPending(e.detail);
    bus.addEventListener('notify', onNotify);
    bus.addEventListener('confirm', onConfirm);
    return () => {
      bus.removeEventListener('notify', onNotify);
      bus.removeEventListener('confirm', onConfirm);
    };
  }, []);

  useEffect(() => {
    if (!pending) return undefined;
    // Foco en la opción segura: Cancelar si la acción es destructiva
    setTimeout(() => (pending.danger ? cancelRef : confirmRef).current?.focus(), 0);
    const onKey = (e) => { if (e.key === 'Escape') { e.preventDefault(); answer(false); } };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pending]);

  const answer = (value) => {
    pending?.resolve(value);
    setPending(null);
  };

  return (
    <>
      <div
        role="status"
        aria-live="polite"
        style={{ position: 'fixed', left: '50%', bottom: '24px', transform: 'translateX(-50%)', zIndex: 120, display: 'flex', flexDirection: 'column', gap: '8px', width: 'min(520px, calc(100vw - 32px))', pointerEvents: 'none' }}
      >
        {notices.map((n) => (
          <div
            key={n.id}
            className="rise"
            style={{
              pointerEvents: 'auto',
              display: 'flex',
              alignItems: 'flex-start',
              gap: '12px',
              background: 'var(--surface)',
              border: `1px solid ${n.tone === 'error' ? 'var(--bad)' : 'var(--line)'}`,
              borderRadius: 'var(--r-card)',
              padding: '10px 14px',
              fontSize: 'var(--fs-secondary)',
              color: n.tone === 'error' ? 'var(--bad)' : 'var(--text)',
              boxShadow: 'var(--shadow-overlay)',
              overflowWrap: 'anywhere',
            }}
          >
            <span style={{ flex: 1, whiteSpace: 'pre-wrap' }}>{n.message}</span>
            <button type="button" onClick={() => setNotices((prev) => prev.filter((x) => x.id !== n.id))} aria-label="Cerrar aviso" style={{ background: 'none', border: 0, color: 'var(--muted)' }}>
              ✕
            </button>
          </div>
        ))}
      </div>

      {pending && (
        <div
          onMouseDown={(e) => { if (e.target === e.currentTarget) answer(false); }}
          style={{ position: 'fixed', inset: 0, zIndex: 130, background: 'var(--scrim)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '20px' }}
        >
          <div
            role="alertdialog"
            aria-modal="true"
            aria-label={pending.title || 'Confirmación'}
            className="rise"
            style={{ background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 'var(--r-panel)', padding: '20px 22px', width: 'min(440px, 100%)', boxShadow: 'var(--shadow-overlay)', display: 'flex', flexDirection: 'column', gap: '14px' }}
          >
            {pending.title && <div style={{ fontWeight: 600, fontSize: 'var(--fs-chat)' }}>{pending.title}</div>}
            <div style={{ color: pending.title ? 'var(--muted)' : 'var(--text)', lineHeight: 1.55, whiteSpace: 'pre-wrap' }}>{pending.message}</div>
            <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
              <button ref={cancelRef} type="button" className="btn btn-secondary" onClick={() => answer(false)}>{pending.cancelLabel}</button>
              <button ref={confirmRef} type="button" className={`btn ${pending.danger ? 'btn-danger' : 'btn-primary'}`} onClick={() => answer(true)}>{pending.confirmLabel}</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
