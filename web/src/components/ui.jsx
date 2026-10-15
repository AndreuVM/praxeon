import React from 'react';

/** Contenedor de una vista secundaria (RD-6.8): margen lateral fluido y ancho máximo. */
export function ViewPage({ children, maxWidth = 1100, gap = 24, style }) {
  return (
    <div style={{ flex: 1, minWidth: 0, overflowY: 'auto', backgroundColor: 'var(--bg)' }}>
      <div
        style={{
          maxWidth: `${maxWidth}px`,
          margin: '0 auto',
          padding: '34px clamp(20px, 5vw, 64px) 48px',
          display: 'flex',
          flexDirection: 'column',
          gap: `${gap}px`,
          ...style,
        }}
      >
        {children}
      </div>
    </div>
  );
}

/** Título de 20 px, descripción atenuada de una línea y acciones a la derecha. */
export function ViewHeader({ title, description, actions }) {
  return (
    <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', flexWrap: 'wrap', gap: '16px' }}>
      <div style={{ minWidth: 0 }}>
        <h1 style={{ fontSize: 'var(--fs-title)', fontWeight: 600, letterSpacing: '-0.01em', textWrap: 'balance' }}>{title}</h1>
        {description && <p style={{ color: 'var(--muted)', marginTop: '4px', maxWidth: '68ch' }}>{description}</p>}
      </div>
      {actions && <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>{actions}</div>}
    </div>
  );
}

/** Chip neutro de metadatos. `tone`: 'ok' | 'bad' | 'accent' solo para estado. */
export function Chip({ children, icon: Icon, tone, title, mono }) {
  const color = tone === 'ok' ? 'var(--ok)' : tone === 'bad' ? 'var(--bad)' : tone === 'accent' ? 'var(--accent)' : 'var(--muted)';
  return (
    <span
      title={title}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '5px',
        padding: '1px 9px',
        borderRadius: 'var(--r-pill)',
        border: `1px solid ${tone ? `color-mix(in srgb, ${color} 45%, transparent)` : 'var(--line)'}`,
        color,
        fontSize: 'var(--fs-label)',
        fontFamily: mono ? 'var(--font-mono)' : undefined,
        whiteSpace: 'nowrap',
        maxWidth: '100%',
        overflow: 'hidden',
        textOverflow: 'ellipsis',
      }}
    >
      {Icon && <Icon size={11} strokeWidth={1.8} style={{ flexShrink: 0 }} />}
      {children}
    </span>
  );
}

/** Selector segmentado de texto (filtros de estado). */
export function Segmented({ options, value, onChange, label }) {
  return (
    <div role="tablist" aria-label={label} style={{ display: 'inline-flex', gap: '2px', border: '1px solid var(--line)', borderRadius: 'var(--r-ui)', padding: '3px' }}>
      {options.map((o) => (
        <button
          key={o.id}
          type="button"
          role="tab"
          aria-selected={value === o.id}
          onClick={() => onChange(o.id)}
          style={{
            padding: '4px 12px',
            borderRadius: '7px',
            border: 0,
            background: value === o.id ? 'var(--surface)' : 'transparent',
            color: value === o.id ? 'var(--text)' : 'var(--muted)',
            fontSize: 'var(--fs-secondary)',
          }}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** Campo de búsqueda con icono. */
export function SearchField({ value, onChange, placeholder, icon: Icon, maxWidth = 420 }) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: '10px',
        padding: '0 12px',
        border: '1px solid var(--line)',
        borderRadius: 'var(--r-ui)',
        flex: 1,
        maxWidth: `${maxWidth}px`,
        minWidth: '200px',
      }}
    >
      {Icon && <Icon size={14} strokeWidth={1.6} style={{ color: 'var(--muted)', flexShrink: 0 }} />}
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        aria-label={placeholder}
        style={{ border: 0, background: 'transparent', padding: '7px 0', width: '100%', outline: 'none' }}
      />
    </div>
  );
}

/** Etiqueta de campo + control + ayuda/errores. */
export function Field({ label, htmlFor, hint, error, children, style }) {
  return (
    <div style={style}>
      {label && (
        <label htmlFor={htmlFor} style={{ display: 'block', color: 'var(--muted)', fontSize: 'var(--fs-secondary)', marginBottom: '4px' }}>
          {label}
        </label>
      )}
      {children}
      {hint && !error && <div style={{ color: 'var(--muted)', fontSize: 'var(--fs-secondary)', marginTop: '4px' }}>{hint}</div>}
      {error && <div style={{ color: 'var(--bad)', fontSize: 'var(--fs-secondary)', marginTop: '4px' }}>{error}</div>}
    </div>
  );
}

/** Estado vacío: explica qué aparecerá y cómo crear el primer elemento. */
export function EmptyState({ title, children, actions }) {
  return (
    <div style={{ border: '1px dashed var(--line)', borderRadius: 'var(--r-card)', padding: '40px 24px', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '10px', textAlign: 'center' }}>
      <div style={{ fontWeight: 600 }}>{title}</div>
      {children && <p style={{ color: 'var(--muted)', maxWidth: '56ch' }}>{children}</p>}
      {actions && <div style={{ display: 'flex', gap: '8px', marginTop: '6px', flexWrap: 'wrap', justifyContent: 'center' }}>{actions}</div>}
    </div>
  );
}

/** Diálogo modal con el estilo común (RD-6.9). */
export function Modal({ open = true, title, onClose, children, maxWidth = 560, labelledBy }) {
  React.useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => { if (e.key === 'Escape') onClose?.(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose?.(); }}
      style={{ position: 'fixed', inset: 0, zIndex: 100, background: 'var(--scrim)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '20px' }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy || 'modal-title'}
        className="rise"
        style={{
          background: 'var(--surface)',
          border: '1px solid var(--line)',
          borderRadius: 'var(--r-panel)',
          width: '100%',
          maxWidth: `${maxWidth}px`,
          maxHeight: 'calc(100vh - 40px)',
          display: 'flex',
          flexDirection: 'column',
          boxShadow: 'var(--shadow-overlay)',
          overflow: 'hidden',
        }}
      >
        <div style={{ padding: '16px 22px', borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px' }}>
          <h2 id={labelledBy || 'modal-title'} style={{ fontSize: 'var(--fs-chat)', fontWeight: 600 }}>{title}</h2>
          <button type="button" className="btn-icon" onClick={onClose} aria-label="Cerrar" title="Cerrar (Esc)">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg>
          </button>
        </div>
        <div style={{ overflowY: 'auto', padding: '22px', display: 'flex', flexDirection: 'column', gap: '16px' }}>{children}</div>
      </div>
    </div>
  );
}
