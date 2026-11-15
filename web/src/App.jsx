import React, { useState, useEffect, useLayoutEffect, useRef, useCallback, Suspense } from 'react';
import ErrorBoundary from './components/ErrorBoundary';
import {
  Activity, FolderOpen,
  Plus, Map as MapIcon, ScrollText, History, FlaskConical, SquarePen, KeyRound, Pause, Play, Square, Columns2, X, Maximize2, Minimize2,
} from 'lucide-react';
import Header from './components/Header';
import Rail, { NAV_ITEMS } from './components/Rail';
import Drawer from './components/Drawer';
import PanelsArea from './components/panels/PanelsArea';
import CommandPalette from './components/CommandPalette';
import StepDetail from './components/StepDetail';
import EventsPanel from './components/EventsPanel';
import DecisionTree from './components/DecisionTree';
import ProposeActionModal from './components/ProposeActionModal';
import SessionsModal from './components/SessionsModal';
import UnauthorizedModal from './components/UnauthorizedModal';
import DialogsHost, { confirmDialog, notify } from './components/Dialogs';
import WorkspacePicker from './components/workspace/WorkspacePicker';
import WorkspaceFlyout from './components/workspace/WorkspaceFlyout';
import useWorkspace from './components/workspace/useWorkspace';
import { loadChatConfig, persistChatConfig, chatRegistry } from './utils/chatStore';
import { createSessionRegistry } from './state/agentSession';
import useAgentSession, { useSessionRegistryVersion } from './state/useAgentSession';
import { MAIN_ID, addPanel, closePanel, focusPanel, initialLayout, layoutStore, panelLabel, toggleMaximize } from './utils/panelLayout';
import { approvalQueue, noticeFor } from './utils/approvals';
import { buildCascadeDecisionModel } from './utils/cascadeConfig';
import { rebuildChatFromSession } from './utils/chatFromEvents';

const SessionsView = React.lazy(() => import('./components/views/SessionsView'));
const DecisionsView = React.lazy(() => import('./components/views/DecisionsView'));
const AgentsView = React.lazy(() => import('./components/views/AgentsView'));
const ProvidersView = React.lazy(() => import('./components/views/ProvidersView'));
const McpView = React.lazy(() => import('./components/views/McpView'));
const FilesView = React.lazy(() => import('./components/views/FilesView'));
const SkillsView = React.lazy(() => import('./components/views/SkillsView'));
const SecurityView = React.lazy(() => import('./components/views/SecurityView'));
const SettingsView = React.lazy(() => import('./components/views/SettingsView'));
const WorkflowsView = React.lazy(() => import('./components/views/WorkflowsView'));

import * as api from './services/api';

export default function App() {
  const [activeNav, setActiveNav] = useState('live');
  const [sessionsList, setSessionsList] = useState([]);

  const [runtimeActive, setRuntimeActive] = useState(true);
  const [isProposeOpen, setIsProposeOpen] = useState(false);
  const [isSessionsOpen, setIsSessionsOpen] = useState(false);
  const [isAuthModalOpen, setIsAuthModalOpen] = useState(false);
  const [isWorkspaceOpen, setIsWorkspaceOpen] = useState(false);
  const [isPaletteOpen, setIsPaletteOpen] = useState(false);
  const [isWsFlyoutOpen, setIsWsFlyoutOpen] = useState(false);
  const { workspace, choose: chooseWorkspace, forget: forgetWorkspace, pin: pinWorkspace, alias: aliasWorkspace } = useWorkspace();
  const workspaceRef = useRef(null);
  workspaceRef.current = workspace;

  // Cambio rápido de workspace (atajos, riel y paleta): avisa del destino y de cualquier fallo
  const switchWorkspace = useCallback(async (path) => {
    const target = (workspaceRef.current?.recent || []).find((w) => w.path === path);
    try {
      await chooseWorkspace(path);
      notify(`Workspace: ${target?.name || path}`, { tone: 'info' });
    } catch (err) {
      notify(err.message || 'No se pudo cambiar el workspace.');
    }
  }, [chooseWorkspace]);

  // Alt+1…9: ir al favorito con ese número · Alt+0: alternar con el anterior.
  // No se usa Ctrl: en teclado español Ctrl+Alt es AltGr y escribe @ # |.
  useEffect(() => {
    const onKey = (e) => {
      if (!e.altKey || e.ctrlKey || e.metaKey || e.shiftKey || !/^Digit\d$/.test(e.code)) return;
      const ws = workspaceRef.current;
      const n = Number(e.code.slice(5));
      const target = n === 0 ? ws?.previous : (ws?.recent || []).find((w) => w.slot === n && w.exists)?.path;
      if (!target) return;
      e.preventDefault();
      if (target !== ws?.path) switchWorkspace(target);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [switchWorkspace]);

  // Cajón de detalle: null (cerrado) | 'map' | 'step' | 'events'
  const [drawerTab, setDrawerTab] = useState(null);
  const [drawerWide, setDrawerWide] = useState(false);
  const [narrow, setNarrow] = useState(() => typeof window !== 'undefined' && window.matchMedia('(max-width: 980px)').matches);
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 980px)');
    const onChange = (e) => setNarrow(e.matches);
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);

  // Ctrl K / ⌘ K abre la paleta de comandos
  useEffect(() => {
    const onKey = (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setIsPaletteOpen((v) => !v);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  // Escuchar eventos globales de 401/403 para abrir modal de autenticación reactivo
  useEffect(() => {
    const handleUnauthorized = () => {
      setIsAuthModalOpen(true);
    };
    window.addEventListener('praxeon:unauthorized', handleUnauthorized);
    return () => {
      window.removeEventListener('praxeon:unauthorized', handleUnauthorized);
    };
  }, []);

  // Estado persistente del Chat de Misión y configuración de modelos
  const [missionConfig, setMissionConfig] = useState({
    llmProvider: 'simulator',
    supervisor: 'laya',
    executionMode: 'auto',
    fullAccessConfirmed: false,
    maxSteps: 25,
    customModel: '',
    apiKey: '',
    baseUrl: '',
    ...loadChatConfig(), // última configuración usada: modelo, supervisor, modo, workflow, pasos…
  });
  useEffect(() => {
    persistChatConfig(missionConfig);
  }, [missionConfig]);


  // Sesión en vivo de este panel (FE-007): estado, WebSocket y acciones viven en AgentSession; aquí solo se conecta con la interfaz.
  // Las funciones de `deps` leen siempre el último valor mediante refs, porque el almacén se crea una sola vez.
  const missionConfigRef = useRef(missionConfig);
  const sessionsListRef = useRef(sessionsList);
  useLayoutEffect(() => {
    missionConfigRef.current = missionConfig;
    sessionsListRef.current = sessionsList;
  });
  // Un registro de sesiones, una por panel (FE-008). El aviso de lo que pasa en un panel sin foco lo decide `panelEventRef`.
  const panelEventRef = useRef(null);
  const [sessions] = useState(() => createSessionRegistry((panelId) => ({
    api,
    notify,
    chats: chatRegistry(),
    rebuildChat: rebuildChatFromSession,
    buildCascade: buildCascadeDecisionModel,
    getMissionConfig: () => missionConfigRef.current,
    getSessionsList: () => sessionsListRef.current,
    setSessionsList,
    onRuntimeActive: setRuntimeActive,
    onEvent: (ev) => panelEventRef.current?.(panelId, ev),
    ui: { showLive: () => setActiveNav('live'), closeDrawer: () => setDrawerTab(null), openStep: () => setDrawerTab('step') },
  })));
  const [layout, setLayout] = useState(initialLayout);
  const [unread, setUnread] = useState({});                // avisos sin ver de cada panel que no tiene el foco
  const layoutOwnerRef = useRef(null);                     // workspace al que pertenece la disposición actual (para guardarla)
  useState(() => sessions.open(MAIN_ID));
  // El ciclo de vida de los WebSockets es del conjunto, no de cada panel: un panel oculto o en pestaña sigue conectado.
  useEffect(() => {
    sessions.activateAll();
    return () => sessions.disposeAll();
  }, [sessions]);
  const registryVersion = useSessionRegistryVersion(sessions);
  const live = sessions.get(layout.focusedId) || sessions.get(MAIN_ID);   // la sesión del panel con el foco: la que ven la cabecera y el cajón
  const { session, nodes, decisionsMap, selectedNodeId, logs, events, isRunning, isPaused, followExecution } = useAgentSession(live);
  const { setSelectedNodeId } = live;

  // Aprobaciones de TODOS los paneles: una pendiente en un panel sin foco no se pierde
  const queue = approvalQueue(layout.order.map((id) => ({ id, label: panelLabel(id), state: sessions.get(id)?.getState() })).filter((x) => x.state));

  const focusPanelById = (id) => {
    setLayout((l) => focusPanel(l, id));
    setUnread((u) => (u[id] ? { ...u, [id]: 0 } : u));
  };
  const handleNewPanel = () => {
    const result = addPanel(layout);
    if (!result) {
      notify('Hay ya el máximo de paneles abiertos. Cierra alguno para abrir otro.', { tone: 'info' });
      return;
    }
    sessions.open(result.id).newClean();
    setLayout(result.layout);
    setActiveNav('live');
  };
  const handleClosePanel = async (id) => {
    const store = sessions.get(id);
    if (!store || layout.order.length <= 1) return;
    if (store.getState().isRunning) {
      const ok = await confirmDialog(`${panelLabel(id)} tiene una misión en curso. Cerrar el panel no la detiene: sigue en el servidor y la verás en Sesiones.`, { confirmLabel: 'Cerrar panel' });
      if (!ok) return;
    }
    sessions.close(id);
    setLayout((l) => closePanel(l, id));
    setUnread((u) => {
      const { [id]: _gone, ...rest } = u;
      return rest;
    });
  };
  const handleMaximizePanel = (id) => setLayout((l) => toggleMaximize(l, id));

  // Avisos por panel: algo que pasa en un panel sin foco (aprobación pedida, conflicto de escritura, misión terminada)
  const focusedRef = useRef(MAIN_ID);
  useLayoutEffect(() => {
    focusedRef.current = layout.focusedId;
    panelEventRef.current = (panelId, ev) => {
      const notice = noticeFor(ev);
      if (!notice || panelId === focusedRef.current) return;
      setUnread((u) => ({ ...u, [panelId]: (u[panelId] || 0) + 1 }));
      notify(`${panelLabel(panelId)}: ${notice.text}`, { tone: notice.kind === 'conflict' ? 'error' : 'info' });
    };
  });

  // Paneles de un workspace que se guardaron: se abren de nuevo con su sesión (la conversación y el estado vienen del servidor)
  const restorePanels = async (path) => {
    const saved = layoutStore.load(path);
    const extras = saved.panels.filter((x) => x.id !== MAIN_ID);
    if (extras.length === 0) return;
    await Promise.all(extras.map((x) => {
      const store = sessions.open(x.id);
      return x.sessionId ? store.loadSession(x.sessionId) : Promise.resolve(store.newClean());
    }));
    setLayout({ order: [MAIN_ID, ...extras.map((x) => x.id)], focusedId: saved.focusedId, maximizedId: null });
  };

  // La disposición se guarda por workspace (paneles abiertos, la sesión de cada uno y cuál tiene el foco)
  const layoutKey = JSON.stringify([layout.order.map((id) => [id, sessions.get(id)?.getState().session.sessionId || null]), layout.focusedId, registryVersion >= 0]);
  useEffect(() => {
    const path = workspace?.path;
    if (!path || layoutOwnerRef.current !== path) return;
    layoutStore.save(path, {
      panels: layout.order.map((id) => ({ id, sessionId: sessions.get(id)?.getState().session.sessionId || null })),
      focusedId: layout.focusedId,
    });
  }, [layoutKey, workspace?.path]); // eslint-disable-line react-hooks/exhaustive-deps

  // Cada workspace tiene su propia conversación: al cambiar de workspace no se conserva la sesión actual (sigue en el servidor y
  // en la lista de sesiones), se carga la última usada en el workspace nuevo o, si no tiene ninguna, se abre una sesión limpia.
  const activeNavRef = useRef(activeNav);
  activeNavRef.current = activeNav;
  const lastWorkspacePathRef = useRef(null);
  useEffect(() => {
    const path = workspace?.path;
    if (!path) return undefined;
    const previous = lastWorkspacePathRef.current;
    lastWorkspacePathRef.current = path;
    if (previous === path) return undefined;                        // el mismo workspace; al arrancar (previous === null) también se carga su última sesión
    let cancelled = false;
    (async () => {
      const nav = activeNavRef.current;
      const same = (a, b) => Boolean(a && b) && a.replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase() === b.replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase();
      let target = null;
      try {
        const res = await api.fetchSessions();
        target = (res?.data || [])
          .filter((sess) => same(sess.workspace_root, path))
          .sort((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at))[0] || null;
      } catch { /* sin lista de sesiones: se abre una limpia */ }
      if (cancelled) return;
      // Al CAMBIAR de workspace, los paneles extra del anterior se cierran (sus sesiones siguen en el servidor). Al arrancar no hay
      // nada que cerrar: si la persona ya abrió un panel mientras cargaba, se respeta. El principal carga la última sesión.
      const switched = previous !== null;
      if (switched) {
        sessions.ids().filter((id) => id !== MAIN_ID).forEach((id) => sessions.close(id));
        setLayout(initialLayout());
        setUnread({});
      }
      const main = sessions.get(MAIN_ID);
      if (target) await main.loadSession(target.session_id);
      else main.newClean();
      if (switched || sessions.ids().length === 1) await restorePanels(path);   // los paneles que este workspace tenía abiertos
      layoutOwnerRef.current = path;
      if (nav !== 'live') setActiveNav(nav);                          // cambiar de workspace no te saca de la vista en que estabas
    })();
    return () => { cancelled = true; };
  }, [workspace?.path]); // eslint-disable-line react-hooks/exhaustive-deps

  // Comprobar salud del backend y listar sesiones previas
  useEffect(() => {
    async function initBackend() {
      const health = await api.fetchHealth();
      if (health?.data?.status === 'healthy' || health?.data?.status === 'ok') {
        setRuntimeActive(true);
        const sList = await api.fetchSessions();
        if (sList?.data && sList.data.length > 0) {
          setSessionsList(sList.data);
        }
      }
    }
    initBackend();
  }, []);


  // Selección de nodo en el árbol
  const currentDecision = decisionsMap[selectedNodeId] || Object.values(decisionsMap)[0] || null;

  const handleSelectNode = (nodeId) => live.selectNode(nodeId);

  // Enriquecer automáticamente la decisión seleccionada si aún no tiene decisionTab
  useEffect(() => {
    if (!selectedNodeId || selectedNodeId === 'start') return;
    const cur = decisionsMap[selectedNodeId];
    if (cur && (!cur.decisionTab || !cur.decisionTab.execution_mode)) {
      const decId = cur.decisionId || (selectedNodeId.startsWith('d_') ? selectedNodeId : `d_${selectedNodeId}`);
      live.fetchAndSetDecision(selectedNodeId, decId);
    }
  }, [selectedNodeId, live]); // eslint-disable-line react-hooks/exhaustive-deps

  // Misión: iniciar, pausar, reanudar y detener actúan sobre la sesión de este panel
  const handleStartMission = (cfg) => live.startMission(cfg);
  const handlePauseMission = () => live.pause();
  const handleResumeMission = () => live.resume();
  const handleStopMission = () => live.stop();

  // Refrescar lista de sesiones
  const refreshSessionsList = async () => {
    try {
      const sList = await api.fetchSessions();
      if (sList?.data) {
        setSessionsList(sList.data);
      }
    } catch (e) {
      console.warn('Error refreshing sessions:', e);
    }
  };

  // Crear una nueva sesión limpia (grafo y chat en blanco, sin memoria previa)
  const handleNewCleanSession = () => live.newClean();

  // Eliminar una sesión individual
  const handleDeleteSession = async (sessionId) => {
    const timeStr = new Date().toTimeString().split(' ')[0];
    try {
      await api.deleteSession(sessionId);
      setSessionsList((prev) => prev.filter((s) => s.session_id !== sessionId));
      setLogs((prev) => [
        ...prev,
        { time: timeStr, level: 'INFO', message: `Sesión #${sessionId} eliminada y purgada exitosamente.` },
      ]);

      if (session.sessionId === sessionId) {
        handleNewCleanSession();
      }
    } catch (err) {
      console.error('Error eliminando sesión:', err);
      setLogs((prev) => [
        ...prev,
        { time: timeStr, level: 'ERROR', message: `Error eliminando sesión #${sessionId}: ${err.message}` },
      ]);
    }
  };

  // Limpiar/purgar sesiones antiguas finalizadas
  const handleClearOldSessions = async () => {
    const timeStr = new Date().toTimeString().split(' ')[0];
    try {
      const res = await api.clearSessions(true, session.sessionId);
      const count = res?.data?.deleted_count || 0;
      await refreshSessionsList();
      setLogs((prev) => [
        ...prev,
        {
          time: timeStr,
          level: 'INFO',
          message: `Limpieza completada: se purgaron ${count} sesiones antiguas finalizadas.`,
        },
      ]);
    } catch (err) {
      console.error('Error purgando sesiones antiguas:', err);
      setLogs((prev) => [
        ...prev,
        { time: timeStr, level: 'ERROR', message: `Error en limpieza de sesiones: ${err.message}` },
      ]);
    }
  };

  // Cargar sesión existente y su snapshot de árbol / decisiones
  const handleLoadSession = (sid) => live.loadSession(sid);

  // Cargar árbol de demostración visual (para comparar con la captura si se desea)
  const handleLoadDemo = () => live.loadDemo();

  // Autorización humana, rechazo y ejecución: actúan sobre la sesión de este panel
  const handleApprove = (decisionId, nodeId) => live.approve(decisionId, nodeId);
  const handleReject = (decisionId, customReason, nodeId) => live.reject(decisionId, customReason, nodeId);
  const handleExecute = (decisionId, capability) => live.execute(decisionId, capability);

  const openDrawer = (tab) => {
    setActiveNav('live');
    setDrawerTab(tab);
  };

  // Esc en una vista secundaria vuelve a la conversación (RD-V3)
  useEffect(() => {
    const onKey = (e) => {
      if (e.key !== 'Escape' || e.defaultPrevented || activeNav === 'live') return;
      if (isPaletteOpen || isSessionsOpen || isProposeOpen || isAuthModalOpen || isWorkspaceOpen || isWsFlyoutOpen) return;
      const tag = (e.target?.tagName || '').toLowerCase();
      if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
      setActiveNav('live');
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [activeNav, isPaletteOpen, isSessionsOpen, isProposeOpen, isAuthModalOpen, isWorkspaceOpen, isWsFlyoutOpen]);

  const paletteItems = [
    { id: 'go-live', group: 'Conversación', label: 'Volver a la conversación', icon: Activity, run: () => setActiveNav('live') },
    { id: 'new-session', group: 'Conversación', label: 'Nueva sesión limpia', icon: Plus, run: handleNewCleanSession },
    { id: 'new-panel', group: 'Conversación', label: 'Abrir un panel con otro agente', icon: Columns2, run: handleNewPanel },
    ...(layout.order.length > 1
      ? [
          { id: 'close-panel', group: 'Conversación', label: `Cerrar el panel ${panelLabel(layout.focusedId)}`, icon: X, run: () => handleClosePanel(layout.focusedId) },
          { id: 'max-panel', group: 'Conversación', label: layout.maximizedId ? 'Restaurar los paneles' : 'Maximizar el panel actual', icon: layout.maximizedId ? Minimize2 : Maximize2, run: () => handleMaximizePanel(layout.focusedId) },
        ]
      : []),
    { id: 'open-map', group: 'Conversación', label: 'Abrir el mapa de decisiones', icon: MapIcon, run: () => openDrawer('map') },
    { id: 'open-events', group: 'Conversación', label: 'Ver los eventos de la sesión', icon: ScrollText, run: () => openDrawer('events') },
    { id: 'open-sessions', group: 'Conversación', label: 'Abrir las sesiones registradas', icon: History, run: () => setIsSessionsOpen(true) },
    { id: 'propose', group: 'Conversación', label: 'Proponer una acción manual', icon: SquarePen, run: () => setIsProposeOpen(true) },
    { id: 'demo', group: 'Conversación', label: 'Cargar la sesión de demostración', icon: FlaskConical, run: () => { setActiveNav('live'); handleLoadDemo(); } },
    ...(isRunning
      ? [
          isPaused
            ? { id: 'resume', group: 'Conversación', label: 'Reanudar la misión', icon: Play, run: handleResumeMission }
            : { id: 'pause', group: 'Conversación', label: 'Pausar la misión', icon: Pause, run: handlePauseMission },
          { id: 'stop', group: 'Conversación', label: 'Detener la misión', icon: Square, run: handleStopMission },
        ]
      : []),
    { id: 'workspace', group: 'Proyecto y cuenta', label: 'Cambiar de workspace', icon: FolderOpen, run: () => setIsWsFlyoutOpen(true) },
    ...(workspace?.previous
      ? [{ id: 'workspace-previous', group: 'Workspaces', label: 'Volver al workspace anterior (Alt+0)', icon: FolderOpen, run: () => switchWorkspace(workspace.previous) }]
      : []),
    ...(workspace?.recent || [])
      .filter((w) => w.exists && w.path !== workspace?.path)
      .map((w) => ({ id: `workspace-${w.path}`, group: 'Workspaces', label: `Ir a ${w.name}${w.slot ? ` (Alt+${w.slot})` : ''}`, icon: FolderOpen, run: () => switchWorkspace(w.path) })),
    { id: 'api-key', group: 'Proyecto y cuenta', label: 'Configurar la clave de API', icon: KeyRound, run: () => setIsAuthModalOpen(true) },
    ...NAV_ITEMS.filter((i) => i.id !== 'live').map((i) => ({ id: `view-${i.id}`, group: 'Vistas', label: i.label, icon: i.icon, run: () => setActiveNav(i.id) })),
  ];

  const sessionWorkspace = session.workspace_root || session.working_directory || null;
  const workspaceMismatch = Boolean(sessionWorkspace && workspace?.path && sessionWorkspace !== workspace.path);
  const selectedNode = nodes.find((n) => n.id === selectedNodeId);
  const stepDecision = selectedNodeId === 'start' ? null : currentDecision;
  const executionModeNow = session.execution_mode || missionConfig.executionMode || 'local_restricted';

  return (
    <div style={{ display: 'flex', height: '100vh', width: '100vw', backgroundColor: 'var(--bg)', color: 'var(--text)' }}>
      <Rail
        active={activeNav}
        onSelect={setActiveNav}
        workspace={workspace}
        workspaceMismatch={workspaceMismatch}
        workspaceOpen={isWsFlyoutOpen}
        onSwitchWorkspace={switchWorkspace}
        onOpenWorkspace={() => setIsWsFlyoutOpen((v) => !v)}
        onOpenPalette={() => setIsPaletteOpen(true)}
        onOpenAccount={() => setIsAuthModalOpen(true)}
        pendingApprovals={queue.length}
      />

      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
        <Header
          session={session}
          runtimeActive={runtimeActive}
          executionMode={executionModeNow}
          isAutonomous={Boolean(session.autonomous || session.isAutonomous || session.execution_mode === 'full_access_autonomous')}
          isDegraded={Boolean(session.isDegraded || session.fallbackActive || session.status === 'Degraded')}
          fallbackReason={session.fallbackReason}
          isRunning={isRunning}
          isPaused={isPaused}
          pendingApprovals={queue.length}
          approvalQueue={queue}
          panelCount={layout.order.length}
          drawerTab={drawerTab}
          onOpenSessions={() => setIsSessionsOpen(true)}
          onNewSession={handleNewCleanSession}
          onNewPanel={handleNewPanel}
          onGoToApproval={(item) => {
            focusPanelById(item.panelId);
            sessions.get(item.panelId)?.setSelectedNodeId(item.nodeId);
            openDrawer('step');
          }}
          onOpenMap={() => (activeNav === 'live' && drawerTab === 'map' ? setDrawerTab(null) : openDrawer('map'))}
          onOpenPalette={() => setIsPaletteOpen(true)}
          onGoToConversation={() => setActiveNav('live')}
        />

        <div style={{ flex: 1, minHeight: 0, display: 'flex', position: 'relative', overflow: 'hidden' }}>
          <main style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
            {/* Los paneles permanecen montados al cambiar de vista para no perder el texto ni la configuración */}
            <PanelsArea
              registry={sessions}
              layout={layout}
              narrow={narrow}
              unread={unread}
              missionConfig={missionConfig}
              onConfigChange={setMissionConfig}
              drawerTab={drawerTab}
              openDrawer={openDrawer}
              onFocus={focusPanelById}
              onMaximize={handleMaximizePanel}
              onClose={handleClosePanel}
              onAdd={handleNewPanel}
              hidden={activeNav !== 'live'}
            />

            {activeNav !== 'live' && (
              <div style={{ display: 'flex', flex: 1, overflow: 'hidden', minWidth: 0 }}>
                <ErrorBoundary key={activeNav}>
                <Suspense fallback={<div style={{ flex: 1, display: 'grid', placeItems: 'center', color: 'var(--muted)' }}>Cargando…</div>}>
              {activeNav === 'sessions' && (
                <SessionsView
                  sessions={sessionsList}
                  currentSessionId={session.sessionId}
                  onSelectSession={handleLoadSession}
                  onCreateSession={(cfg) => {
                    handleStartMission(cfg);
                    setActiveNav('live');
                  }}
                  onRefreshSessions={refreshSessionsList}
                  onDeleteSession={handleDeleteSession}
                  onClearOldSessions={handleClearOldSessions}
                  onNewCleanSession={handleNewCleanSession}
                />
              )}

              {activeNav === 'decisions' && (
                <DecisionsView
                  decisions={nodes.filter((n) => n.type !== 'start').map((n) => ({
                    decision_id: n.id,
                    sequence: n.sequence,
                    session_id: session.sessionId,
                    tool: n.tool,
                    command: n.command || n.title,
                    status: n.verdict || n.status,
                    risk_level: n.risk,
                    created_at: n.timestamp,
                  }))}
                  currentSessionId={session.sessionId}
                  sessions={sessionsList}
                  onSelectDecision={(nodeId, sid) => {
                    if (sid && sid !== session.sessionId) {
                      handleLoadSession(sid);
                    }
                    setSelectedNodeId(nodeId);
                    setDrawerTab('step');
                    setActiveNav('live');
                  }}
                />
              )}

              {activeNav === 'agents' && (
                <AgentsView
                  session={session}
                  sessionsList={sessionsList}
                  missionConfig={missionConfig}
                  isRunning={isRunning}
                  isPaused={isPaused}
                  onSelectSession={handleLoadSession}
                  onLaunchAgentMission={(cfg) => {
                    handleStartMission(cfg);
                    setActiveNav('live');
                  }}
                />
              )}

              {activeNav === 'workflows' && <WorkflowsView followExecution={followExecution} />}

              {activeNav === 'providers' && <ProvidersView />}

              {activeNav === 'mcp' && <McpView />}

              {activeNav === 'files' && <FilesView workspace={workspace} />}

              {activeNav === 'skills' && <SkillsView />}

              {activeNav === 'security' && (
                <SecurityView
                  session={session}
                  sessionsList={sessionsList}
                  decisions={nodes.filter((n) => n.type !== 'start').map((n) => ({
                    decision_id: n.id,
                    session_id: session.sessionId,
                    tool: n.tool,
                    command: n.command || n.title,
                    status: n.verdict || n.status,
                    risk_level: n.risk,
                    created_at: n.timestamp,
                  }))}
                />
              )}

              {activeNav === 'settings' && (
                <SettingsView
                  missionConfig={missionConfig}
                  onConfigChange={setMissionConfig}
                />
              )}
            </Suspense>
                </ErrorBoundary>
              </div>
            )}
          </main>

          {activeNav === 'live' && (
            <Drawer
              tab={drawerTab}
              wide={drawerWide}
              overlay={narrow}
              onTabChange={setDrawerTab}
              onToggleWide={() => setDrawerWide((v) => !v)}
              onClose={() => setDrawerTab(null)}
            >
              {drawerTab === 'map' && (
                <DecisionTree nodes={nodes} selectedNodeId={selectedNodeId} onSelectNode={handleSelectNode} />
              )}
              {drawerTab === 'step' && (
                <div style={{ flex: 1, overflowY: 'auto', padding: '18px' }}>
                  <StepDetail
                    decision={stepDecision}
                    nodeId={selectedNodeId}
                    title={selectedNode && selectedNodeId !== 'start' ? selectedNode.label : ''}
                    session={session}
                    onApprove={handleApprove}
                    onReject={handleReject}
                    onExecute={handleExecute}
                  />
                </div>
              )}
              {drawerTab === 'events' && (
                <div style={{ flex: 1, overflowY: 'auto', padding: '18px' }}>
                  <EventsPanel logs={logs} events={events} runtime={session.runtime} />
                </div>
              )}
            </Drawer>
          )}
        </div>
      </div>

      <WorkspaceFlyout
        open={isWsFlyoutOpen}
        workspace={workspace}
        sessionWorkspace={sessionWorkspace}
        onSelect={chooseWorkspace}
        onForget={forgetWorkspace}
        onPin={pinWorkspace}
        onAlias={aliasWorkspace}
        onBrowse={() => {
          setIsWsFlyoutOpen(false);
          setIsWorkspaceOpen(true);
        }}
        onClose={() => setIsWsFlyoutOpen(false)}
      />
      <WorkspacePicker
        open={isWorkspaceOpen}
        title="Workspace del proyecto"
        initialPath={workspace?.path || ''}
        recent={workspace?.recent || []}
        onSelect={chooseWorkspace}
        onForget={forgetWorkspace}
        onClose={() => setIsWorkspaceOpen(false)}
      />
      <DialogsHost />
      <CommandPalette open={isPaletteOpen} items={paletteItems} onClose={() => setIsPaletteOpen(false)} />

      {/* Modals */}
      <ProposeActionModal
        isOpen={isProposeOpen}
        onClose={() => setIsProposeOpen(false)}
        onSubmit={async (proposal) => {
          try {
            await api.proposeAction(session.sessionId, proposal);
          } catch (e) {
            console.warn('Propose action error:', e);
          }
          setIsProposeOpen(false);
        }}
      />

      <SessionsModal
        isOpen={isSessionsOpen}
        onClose={() => setIsSessionsOpen(false)}
        sessions={sessionsList}
        currentSessionId={session.sessionId}
        onSelectSession={(sid) => {
          handleLoadSession(sid);
          setIsSessionsOpen(false);
        }}
        onCreateSession={(cfg) => {
          // El modal entrega solo el objetivo: se completa con la configuración de la conversación
          handleStartMission(
            typeof cfg === 'string'
              ? {
                  goal: cfg,
                  execution_mode: missionConfig.executionMode,
                  llm_provider: missionConfig.llmProvider,
                  supervisor: missionConfig.supervisor,
                  max_steps: missionConfig.maxSteps,
                }
              : cfg
          );
          setIsSessionsOpen(false);
          setActiveNav('live');
        }}
        onDeleteSession={handleDeleteSession}
        onNewCleanSession={handleNewCleanSession}
      />

      <UnauthorizedModal
        isOpen={isAuthModalOpen}
        onClose={() => setIsAuthModalOpen(false)}
        onSuccess={() => {
          refreshSessionsList();
        }}
      />
    </div>
  );
}
