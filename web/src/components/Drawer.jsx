import React, { useEffect, useRef } from 'react';
import { ChevronsLeft, ChevronsRight, X } from 'lucide-react';

const TABS = [
  { id: 'map', label: 'Mapa' },
  { id: 'step', label: 'Paso' },
  { id: 'events', label: 'Eventos' },
];

/**
 * Cajón de detalle (RD-6.5): 390 px, oculto por defecto, un solo contenido a la vez.
 * `ampliar` lo hace más ancho para el mapa de decisiones.
 */
export default function Drawer({ tab, wide = false, onTabChange, onToggleWide, onClose, overlay = false, children }) {
  const open = Boolean(tab);
  const lastTab = useRef(tab);
  if (tab) lastTab.current = tab;
  const shown = tab || lastTab.current;

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => {
      if (e.key === 'Escape' && !e.defaultPrevented) onClose?.();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  const width = wide ? 'min(760px, 62vw)' : '390px';

  return (
    <aside
      aria-label="Detalle"
      aria-hidden={!open}
      style={{
        width: open ? width : 0,
        flex: 'none',
        overflow: 'hidden',
        background: 'var(--bg)',
        borderLeft: open ? '1px solid var(--line)' : '0 solid var(--line)',
        transition: 'width var(--t-drawer) ease',
        ...(overlay && open ? { position: 'absolute', right: 0, top: 0, bottom: 0, zIndex: 30, boxShadow: 'var(--shadow-drawer)', maxWidth: '100%' } : {}),
      }}
    >
      <div style={{ width, maxWidth: '100%', height: '100%', display: 'flex', flexDirection: 'column' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px', padding: '0 10px 0 18px', borderBottom: '1px solid var(--line)', height: '46px', flex: 'none' }}>
          <div className="tabs" role="tablist" style={{ flex: 1 }}>
            {TABS.map((t) => (
              <button
                key={t.id}
                type="button"
                role="tab"
                aria-selected={shown === t.id}
                className={shown === t.id ? 'on' : ''}
                onClick={() => onTabChange?.(t.id)}
              >
                {t.label}
              </button>
            ))}
          </div>
          <button
            type="button"
            className="btn-icon"
            onClick={onToggleWide}
            title={wide ? 'Reducir el cajón' : 'Ampliar el cajón'}
            aria-label={wide ? 'Reducir el cajón' : 'Ampliar el cajón'}
          >
            {wide ? <ChevronsRight size={15} /> : <ChevronsLeft size={15} />}
          </button>
          <button type="button" className="btn-icon" onClick={onClose} title="Cerrar (Esc)" aria-label="Cerrar el cajón">
            <X size={16} strokeWidth={1.6} />
          </button>
        </div>
        <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>{children}</div>
      </div>
    </aside>
  );
}
