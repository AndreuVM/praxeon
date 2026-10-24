import React from 'react';

/** Evita que un error al cargar o pintar un panel deje la ventana entera vacía: muestra el motivo y permite recargar. */
export default class ErrorBoundary extends React.Component {
  state = { error: null };

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error) {
    console.error('[PRAXEON] Error en un panel:', error);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div role="alert" style={{ flex: 1, display: 'grid', placeItems: 'center', padding: 24, color: 'var(--text)' }}>
        <div style={{ maxWidth: 480, textAlign: 'center', display: 'flex', flexDirection: 'column', gap: 10, alignItems: 'center' }}>
          <strong>Este panel no se pudo cargar</strong>
          <span style={{ color: 'var(--muted)', fontSize: 13 }}>
            Suele pasar tras actualizar PRAXEON con la ventana abierta. Recargar vuelve a cargar la interfaz.
          </span>
          <code style={{ color: 'var(--muted)', fontSize: 12, wordBreak: 'break-word' }}>{String(this.state.error?.message || this.state.error)}</code>
          <button type="button" onClick={() => window.location.reload()} style={{ font: 'inherit', padding: '6px 14px', borderRadius: 8, border: '1px solid var(--line)', background: 'transparent', color: 'inherit', cursor: 'pointer' }}>
            Recargar
          </button>
        </div>
      </div>
    );
  }
}
