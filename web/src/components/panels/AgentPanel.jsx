import React, { useState } from 'react';
import { AlertTriangle, Maximize2, Minimize2, X } from 'lucide-react';
import Conversation from '../Conversation';
import useAgentSession from '../../state/useAgentSession';
import { latestConflict, modelOf, panelStatus, pendingApprovals } from '../../utils/approvals';

const MODE_LABELS = { local_restricted: 'Local restringido', container: 'Aislado', full_access: 'Full Access', full_access_autonomous: 'Full Access' };
const STATUS_DOT = { running: 'dot run', paused: 'dot idle', approval: 'dot run', done: 'dot', idle: 'dot' };

function PanelHeader({ label, state, focused, maximized, canClose, unread, onMaximize, onClose }) {
  const { session } = state;
  const status = panelStatus(state);
  const approvals = pendingApprovals(state).length;
  const mode = session.execution_mode || '';
  const model = modelOf(session);
  return (
    <div
      style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '5px 10px', borderBottom: '1px solid var(--line)', fontSize: 12, flex: 'none', minWidth: 0, background: focused ? 'var(--surface, transparent)' : 'transparent' }}
    >
      <span className={STATUS_DOT[status.key] || 'dot'} role="img" aria-label={status.label} title={status.label} />
      <b style={{ fontWeight: 600, whiteSpace: 'nowrap' }}>{label}</b>
      <span style={{ color: 'var(--muted)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', minWidth: 0 }} title={session.goal || ''}>
        {[session.agent_name || session.agent, model].filter(Boolean).join(' · ')}
      </span>
      {mode && (
        <span className={`chip${mode.startsWith('full_access') ? ' bad' : ''}`} title="Modo de ejecución">
          {MODE_LABELS[mode] || mode}
        </span>
      )}
      <span className="chip" role="status">{status.label}</span>
      {approvals > 0 && <span className="chip accent">Aprobación pendiente{approvals > 1 ? ` (${approvals})` : ''}</span>}
      {unread > 0 && <span className="chip accent" title="Avisos desde la última vez que miraste este panel">{unread} {unread === 1 ? 'aviso' : 'avisos'}</span>}
      <span style={{ flex: 1 }} />
      <button type="button" className="btn-icon" onClick={onMaximize} aria-label={maximized ? 'Restaurar panel' : 'Maximizar panel'} title={maximized ? 'Restaurar' : 'Maximizar'}>
        {maximized ? <Minimize2 size={14} strokeWidth={1.6} /> : <Maximize2 size={14} strokeWidth={1.6} />}
      </button>
      {canClose && (
        <button type="button" className="btn-icon" onClick={onClose} aria-label="Cerrar panel" title="Cerrar panel (la sesión sigue en el servidor)">
          <X size={14} strokeWidth={1.6} />
        </button>
      )}
    </div>
  );
}

/**
 * Un panel: una sesión de agente con su conversación. Con varios paneles visibles lleva cabecera propia (agente, modelo, modo y estado)
 * y avisa en el propio panel de un conflicto de escritura con otra sesión (FE-009). Con un solo panel se ve igual que siempre.
 */
export default function AgentPanel({
  id, store, label, focused, multi, maximized, canClose, unread, missionConfig, onConfigChange, drawerTab, onFocus, onMaximize, onClose, openDrawer,
}) {
  const state = useAgentSession(store);
  const [dismissed, setDismissed] = useState(0);
  const approvals = pendingApprovals(state);
  const conflict = latestConflict(state.events, dismissed);
  const focusThen = (fn) => (...args) => {
    onFocus(id);
    return fn(...args);
  };

  return (
    <section
      data-panel={id}
      aria-label={label}
      onMouseDownCapture={() => { if (!focused) onFocus(id); }}
      style={{
        display: 'flex', flexDirection: 'column', flex: 1, minWidth: 0, minHeight: 0,
        outline: multi && focused ? '1px solid var(--accent, #6ea0ff)' : 'none', outlineOffset: -1,
        borderRight: multi ? '1px solid var(--line)' : 'none', borderBottom: multi ? '1px solid var(--line)' : 'none',
      }}
    >
      {multi && (
        <PanelHeader
          label={label}
          state={state}
          focused={focused}
          maximized={maximized}
          canClose={canClose}
          unread={unread}
          onMaximize={() => onMaximize(id)}
          onClose={() => onClose(id)}
        />
      )}
      {conflict && (
        <div
          role="alert"
          style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 12px', fontSize: 12, flex: 'none', background: 'rgba(229,72,77,.12)', borderBottom: '1px solid var(--line)' }}
        >
          <AlertTriangle size={13} />
          <span style={{ flex: 1, minWidth: 0 }}>
            <b>Conflicto de escritura.</b> {conflict.message}
          </span>
          <button type="button" className="chip" onClick={() => setDismissed(conflict.seq)}>Descartar</button>
        </div>
      )}
      <Conversation
        session={state.session}
        isRunning={state.isRunning}
        isPaused={state.isPaused}
        events={state.events}
        chatMessages={state.chatMessages}
        onMessagesChange={store.updateChat}
        missionConfig={missionConfig}
        onConfigChange={onConfigChange}
        onStartMission={focusThen((cfg) => store.startMission(cfg))}
        onPauseMission={focusThen(() => store.pause())}
        onResumeMission={focusThen(() => store.resume())}
        onStopMission={focusThen(() => store.stop())}
        nodes={state.nodes}
        selectedNodeId={state.selectedNodeId}
        drawerTab={focused ? drawerTab : null}
        pendingApprovals={approvals}
        onSelectStep={focusThen((nodeId) => {
          if (nodeId) store.selectNode(nodeId);
          else openDrawer('step');
        })}
        onOpenDrawer={focusThen((tab) => openDrawer(tab))}
        onApprove={(a) => store.approve(a.decisionId, a.nodeId)}
        onReject={(a, reason) => store.reject(a.decisionId, reason, a.nodeId)}
        onViewApproval={focusThen((a) => {
          store.setSelectedNodeId(a.nodeId);
          openDrawer('step');
        })}
      />
    </section>
  );
}
