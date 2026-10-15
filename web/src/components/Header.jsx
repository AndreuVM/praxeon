import React from 'react';
import { AlertTriangle, ChevronDown, Plus, Search } from 'lucide-react';

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
  drawerTab = null,
  onOpenSessions,
  onNewSession,
  onOpenMap,
  onOpenPalette,
  onGoToConversation,
}) {
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
        <button type="button" className="chip accent" onClick={onGoToConversation} title="Ir a la solicitud de aprobación">
          <span className="dot run" />
          Aprobación pendiente{pendingApprovals > 1 ? ` (${pendingApprovals})` : ''}
        </button>
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
