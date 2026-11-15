import React from 'react';
import { Plus, X } from 'lucide-react';
import AgentPanel from './AgentPanel';
import { useSessionRegistryVersion } from '../../state/useAgentSession';
import { MAX_PANELS, gridFor, panelLabel, visibleIds } from '../../utils/panelLayout';
import { panelStatus, pendingApprovals } from '../../utils/approvals';

const STATUS_DOT = { running: 'dot run', paused: 'dot idle', approval: 'dot run', done: 'dot', idle: 'dot' };

/** Pestañas de los paneles abiertos: estado, aprobaciones pendientes y avisos de cada uno; cualquiera se enfoca con un clic. */
function PanelTabs({ registry, layout, unread, onFocus, onClose, onAdd }) {
  useSessionRegistryVersion(registry);
  const shown = new Set(visibleIds(layout));
  return (
    <div role="tablist" aria-label="Paneles de agentes" style={{ display: 'flex', alignItems: 'center', gap: 2, padding: '0 8px', height: 34, borderBottom: '1px solid var(--line)', flex: 'none', overflowX: 'auto' }}>
      {layout.order.map((id) => {
        const store = registry.get(id);
        const state = store?.getState();
        const status = state ? panelStatus(state) : { key: 'idle', label: 'Listo' };
        const approvals = state ? pendingApprovals(state).length : 0;
        const selected = layout.focusedId === id;
        return (
          <span key={id} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
            <button
              type="button"
              role="tab"
              aria-selected={selected}
              className={selected ? 'on' : ''}
              onClick={() => onFocus(id)}
              title={`${status.label}${shown.has(id) ? '' : ' · oculto: se verá al enfocarlo'}`}
              style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '4px 10px', border: 0, borderRadius: 6, cursor: 'pointer', fontSize: 12, background: selected ? 'var(--surface)' : 'transparent', color: selected ? 'var(--text)' : 'var(--muted)', opacity: shown.has(id) ? 1 : 0.75 }}
            >
              <span className={STATUS_DOT[status.key] || 'dot'} aria-hidden="true" />
              {panelLabel(id)}
              {approvals > 0 && <span className="chip accent" style={{ padding: '0 6px' }}>{approvals}</span>}
              {unread[id] > 0 && !selected && <span className="chip accent" style={{ padding: '0 6px' }} title="Avisos sin ver">{unread[id]}</span>}
            </button>
            {layout.order.length > 1 && (
              <button type="button" className="btn-icon" aria-label={`Cerrar ${panelLabel(id)}`} title="Cerrar panel" onClick={() => onClose(id)} style={{ opacity: 0.6 }}>
                <X size={12} strokeWidth={1.6} />
              </button>
            )}
          </span>
        );
      })}
      <button type="button" className="btn-icon" aria-label="Nuevo panel" title={layout.order.length >= MAX_PANELS ? 'Máximo de paneles abiertos' : 'Nuevo panel con otro agente'} disabled={layout.order.length >= MAX_PANELS} onClick={onAdd}>
        <Plus size={14} strokeWidth={1.6} />
      </button>
    </div>
  );
}

/**
 * Zona de conversación con uno o varios paneles (hasta 4 visibles: 1, 2 en columnas o 2×2; el resto, en pestañas). Todos los paneles
 * quedan montados —los que no se ven, ocultos— para que su texto y su sesión no se pierdan al enfocar otro.
 */
export default function PanelsArea({
  registry, layout, narrow, unread, drawerTab, openDrawer, onFocus, onMaximize, onClose, onAdd, hidden,
}) {
  const visible = visibleIds(layout, narrow ? 1 : undefined);
  const { columns, rows } = gridFor(visible.length);
  const multi = layout.order.length > 1;
  return (
    <div style={{ display: hidden ? 'none' : 'flex', flex: 1, minHeight: 0, flexDirection: 'column' }}>
      {multi && <PanelTabs registry={registry} layout={layout} unread={unread} onFocus={onFocus} onClose={onClose} onAdd={onAdd} />}
      <div
        style={{ flex: 1, minHeight: 0, minWidth: 0, display: 'grid', gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`, gridTemplateRows: `repeat(${rows}, minmax(0, 1fr))` }}
      >
        {layout.order.map((id) => {
          const shown = visible.includes(id);
          return (
            <div key={id} style={{ display: shown ? 'flex' : 'none', minWidth: 0, minHeight: 0 }}>
              <AgentPanel
                id={id}
                store={registry.get(id)}
                label={panelLabel(id)}
                focused={layout.focusedId === id}
                multi={multi}
                maximized={layout.maximizedId === id}
                canClose={layout.order.length > 1}
                unread={unread[id] || 0}
                drawerTab={drawerTab}
                openDrawer={openDrawer}
                onFocus={onFocus}
                onMaximize={onMaximize}
                onClose={onClose}
              />
            </div>
          );
        })}
      </div>
    </div>
  );
}
