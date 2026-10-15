import React, { useEffect, useRef, useState } from 'react';

const SEGMENT = (active) => ({
  background: active ? 'var(--surface)' : 'transparent',
  border: 0,
  borderRadius: 'var(--r-ui)',
  padding: '4px 10px',
  color: active ? 'var(--text)' : 'var(--muted)',
  fontSize: 'var(--fs-secondary)',
});

const LEVEL_COLOR = { ERROR: 'var(--bad)', WARN: 'var(--accent)' };
const VERDICT_WORDS = [['ALLOW', 'var(--ok)'], ['REVIEW', 'var(--accent)'], ['BLOCK', 'var(--bad)']];

// Resalta la palabra de veredicto en los mensajes del terminal
function colorizeMessage(message) {
  for (const [word, color] of VERDICT_WORDS) {
    if (message.includes(word)) {
      const [before, ...rest] = message.split(word);
      return (
        <>
          {before}
          <span style={{ color, fontWeight: 600 }}>{word}</span>
          {rest.join(word)}
        </>
      );
    }
  }
  return message;
}

/**
 * Pestaña «Eventos» del cajón: registro de eventos del runtime, terminal de la sesión y datos del runtime.
 */
export default function EventsPanel({ logs = [], events = [], runtime = {} }) {
  const [view, setView] = useState('events');
  const endRef = useRef(null);

  useEffect(() => {
    if (view === 'terminal') endRef.current?.scrollIntoView({ block: 'end' });
  }, [view, logs.length]);

  const rt = [
    ['Proveedor del modelo', runtime.provider],
    ['Versión del runtime', runtime.version],
    ['Latencia (p50)', runtime.latencyP50],
    ['Tiempo de ejecución', runtime.executionTime],
  ];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '14px', minHeight: 0 }}>
      <div style={{ display: 'flex', gap: '4px' }} role="tablist" aria-label="Registro">
        <button type="button" role="tab" aria-selected={view === 'events'} style={SEGMENT(view === 'events')} onClick={() => setView('events')}>
          Eventos ({events.length})
        </button>
        <button type="button" role="tab" aria-selected={view === 'terminal'} style={SEGMENT(view === 'terminal')} onClick={() => setView('terminal')}>
          Terminal
        </button>
      </div>

      {view === 'events' ? (
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-code)', lineHeight: 1.5 }}>
          {events.length === 0 && <p style={{ color: 'var(--muted)', fontFamily: 'var(--font-sans)', fontSize: 'var(--fs-ui)' }}>Aún no hay eventos en esta sesión.</p>}
          {events.map((ev, idx) => {
            const isError = /error|pruned|blocked/.test(ev.type || '');
            return (
              <div key={`${ev.seq}-${idx}`} style={{ padding: '5px 0', borderBottom: '1px solid var(--line)' }}>
                <div style={{ display: 'flex', gap: '12px' }}>
                  <span style={{ color: 'var(--muted)', flexShrink: 0 }}>{ev.time}</span>
                  <span style={{ color: isError ? 'var(--bad)' : 'var(--text)', overflowWrap: 'anywhere' }}>{ev.type}</span>
                  <span style={{ color: 'var(--muted)', marginLeft: 'auto', flexShrink: 0 }}>#{ev.seq || events.length - idx}</span>
                </div>
                {ev.detail && <div style={{ color: 'var(--muted)', overflowWrap: 'anywhere', paddingLeft: '0' }}>{ev.detail}</div>}
              </div>
            );
          })}
        </div>
      ) : (
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-code)', lineHeight: 1.7 }}>
          {logs.map((log, idx) => (
            <div key={idx} style={{ display: 'flex', gap: '10px', whiteSpace: 'pre-wrap' }}>
              <span style={{ color: 'var(--muted)', userSelect: 'none', flexShrink: 0 }}>{log.time}</span>
              <span style={{ color: LEVEL_COLOR[log.level] || 'var(--muted)', fontWeight: 600, flexShrink: 0 }}>[{log.level}]</span>
              <span style={{ color: LEVEL_COLOR[log.level] || 'var(--text)', overflowWrap: 'anywhere', minWidth: 0 }}>{colorizeMessage(log.message)}</span>
            </div>
          ))}
          <div ref={endRef} />
        </div>
      )}

      <div style={{ borderTop: '1px solid var(--line)', paddingTop: '6px' }}>
        <div style={{ fontSize: 'var(--fs-label)', letterSpacing: '0.07em', textTransform: 'uppercase', color: 'var(--muted)', padding: '6px 0 2px' }}>Runtime</div>
        {rt.map(([label, value]) => (
          <div key={label} style={{ display: 'flex', justifyContent: 'space-between', gap: '16px', padding: '6px 0', borderBottom: '1px solid var(--line)' }}>
            <span style={{ color: 'var(--muted)' }}>{label}</span>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-code)', textAlign: 'right' }}>{value || '—'}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
