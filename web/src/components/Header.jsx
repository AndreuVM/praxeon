import React, { useState } from 'react';
import { AlertTriangle, ChevronDown, Columns2, Plus, Search } from 'lucide-react';

/**
 * Cabecera de 46 px (RD-H1…H3): estado de la sesión, objetivo, identificador y, solo cuando ocurre algo
 * excepcional, etiquetas permanentes (Full Access, modo degradado, sin conexión, aprobación pendiente).
 */
export default function Header({
  session = {},
  runtimeActive = true,
  executionMode = 'local_restricted',
  isAutonomous = false,
  isDegraded = false,
  fallbackReason = null,
  isRunning = false,
  isPaused = false,
  pendingApprovals = 0,
  approvalQueue = [],
  panelCount = 1,
  drawerTab = null,
  onOpenSessions,
  onNewSession,
  onNewPanel,
  onGoToApproval,
  onOpenMap,
  onOpenPalette,
  onGoToConversation,
}) {
  const [queueOpen, setQueueOpen] = useState(false);
  const mode = (executionMode || '').toLowerCase();
  const isFull = mode === 'full_access' || mode.startsWith('full_access');
  const isReady = (session.status || '').toLowerCase() === 'ready';
  const goal = isReady ? 'Nueva conversación' : session.goal || 'Sin objetivo';
  const sessionId = !session.sessionId || session.sessionId === 'ready' ? 'Sesiones' : session.sessionId;

  let dotClass = 'dot';
  let dotLabel = 'Sesión inactiva';
  if (!runtimeActive) { dotClass = 'dot bad'; dotLabel = 'Sin conexión con el runtime'; }
  else if (isRunning) { dotClass = isPaused ? 'dot idle' : 'dot run'; dotLabel = isPaused ? 'Misión pausada' : 'Misión en curso'; }

  return (
    <header
      style={{
        height: '46px',
        borderBottom: '1px solid var(--line)',
        display: 'flex',
        alignItems: 'center',
        gap: '12px',
        padding: '0 18px 0 22px',
        flex: 'none',
        minWidth: 0,
      }}
    >
      <span className={dotClass} title={dotLabel} role="img" aria-label={dotLabel} />
      <b
        title={goal}
        style={{
          fontWeight: 500,
          whiteSpace: 'nowrap',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          color: isReady ? 'var(--muted)' : 'var(--text)',
          minWidth: 0,
        }}
      >
        {goal}
      </b>
      <button
        type="button"
        className="chip"
        onClick={onOpenSessions}
        title="Ver y seleccionar sesiones registradas"
        style={{ fontFamily: sessionId === 'Sesiones' ? undefined : 'var(--font-mono)' }}
      >
        {sessionId}
        <ChevronDown size={11} />
      </button>

      <span style={{ flex: 1 }} />

      {!runtimeActive && (
        <span className="chip bad" role="status">
          <AlertTriangle size={11} />
          Sin conexión con el runtime
        </span>
      )}
      {isFull && (
        <span
          className="chip bad"
          role="status"
          title={
            isAutonomous
              ? 'Full Access autónomo: ejecución directa supervisada sin parada interactiva humana'
              : 'Full Access manual: ejecución directa que requiere confirmación interactiva del operador'
          }
          style={{ fontWeight: 600, letterSpacing: '0.04em' }}
        >
          <AlertTriangle size={11} />
          {isAutonomous ? 'FULL ACCESS · AUTÓNOMO' : 'FULL ACCESS · MANUAL'}
        </span>
      )}
      {isDegraded && (
        <span
          className="chip bad"
          role="status"
          title={fallbackReason || 'El modelo no está disponible. Opera con el planificador degradado de reserva.'}
        >
          <AlertTriangle size={11} />
          Modo degradado{fallbackReason ? `: ${fallbackReason}` : ''}
        </span>
      )}
      {pendingApprovals > 0 && (
        <span style={{ position: 'relative', display: 'inline-flex' }}>
          <button
            type="button"
            className="chip accent"
            onClick={panelCount > 1 ? () => setQueueOpen((v) => !v) : onGoToConversation}
            title={panelCount > 1 ? 'Ver las aprobaciones pendientes de todos los paneles' : 'Ir a la solicitud de aprobación'}
            aria-haspopup={panelCount > 1 ? 'menu' : undefined}
            aria-expanded={panelCount > 1 ? queueOpen : undefined}
          >
            <span className="dot run" />
            Aprobación pendiente{pendingApprovals > 1 ? ` (${pendingApprovals})` : ''}
          </button>
          {panelCount > 1 && queueOpen && (
            <div
              role="menu"
              aria-label="Aprobaciones pendientes"
              style={{ position: 'absolute', right: 0, top: '110%', zIndex: 40, minWidth: 280, maxWidth: 420, background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 8, boxShadow: 'var(--shadow-pop, 0 8px 24px rgba(0,0,0,.35))', padding: 4 }}
              onKeyDown={(e) => { if (e.key === 'Escape') setQueueOpen(false); }}
            >
              {approvalQueue.map((item) => (
                <button
                  key={`${item.panelId}:${item.nodeId}`}
                  type="button"
                  role="menuitem"
                  onClick={() => { setQueueOpen(false); onGoToApproval?.(item); }}
                  style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: 2, width: '100%', padding: '6px 10px', border: 0, background: 'transparent', color: 'inherit', cursor: 'pointer', textAlign: 'left', borderRadius: 6, fontSize: 12 }}
                >
                  <b style={{ fontWeight: 600 }}>{item.panelLabel}</b>
                  <span style={{ color: 'var(--muted)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: '100%' }}>
                    {[item.tool, item.command].filter(Boolean).join(' · ')}
                  </span>
                </button>
              ))}
            </div>
          )}
        </span>
      )}

      <button
        type="button"
        className="btn-icon"
        onClick={onNewSession}
        title="Nueva sesión limpia (conversación y mapa en blanco)"
        aria-label="Nueva sesión"
      >
        <Plus size={16} strokeWidth={1.6} />
      </button>
      <button
        type="button"
        className="btn-icon"
        onClick={onNewPanel}
        title="Abrir un panel con otro agente sobre este workspace"
        aria-label="Nuevo panel"
      >
        <Columns2 size={16} strokeWidth={1.6} />
      </button>
      <button
        type="button"
        onClick={onOpenMap}
        className={`btn btn-secondary${drawerTab === 'map' ? ' on' : ''}`}
        style={{ border: 0, padding: '5px 10px', color: drawerTab === 'map' ? 'var(--text)' : undefined, background: drawerTab === 'map' ? 'var(--surface)' : 'transparent' }}
      >
        Mapa
      </button>
      <button
        type="button"
        onClick={onOpenPalette}
        className="btn btn-secondary"
        style={{ border: 0, padding: '5px 10px', gap: '8px' }}
        aria-label="Buscar y ejecutar acciones"
      >
        <Search size={15} strokeWidth={1.6} />
        <span className="kbd hide-sm">Ctrl K</span>
      </button>
    </header>
  );
}
