// Pruebas de AgentSession (FE-007) con una API y un WebSocket falsos: ciclo de vida del socket, aislamiento entre sesiones y acciones.
import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AgentSession, createSessionRegistry } from './agentSession.js';
import { createChatRegistry } from '../utils/chatStore.js';

const fixtures = JSON.parse(readFileSync(new URL('../utils/__fixtures__/sessionStreams.json', import.meta.url), 'utf8'));
const golden = JSON.parse(readFileSync(new URL('../utils/__fixtures__/sessionGolden.json', import.meta.url), 'utf8'));
const MANUAL = fixtures.find((f) => f.name === 'manual_actions_restricted');

function makeEnv(overrides = {}) {
  const streams = [];
  const api = {
    createWebSocketStream: vi.fn((sid, onMessage, onStatus, getSeq) => {
      const s = { sid, onMessage, onStatus, getSeq, closed: false, close: vi.fn(() => { s.closed = true; }) };
      streams.push(s);
      return s;
    }),
    runMission: vi.fn(async (cfg) => ({ data: { session_id: 'run-1', execution_mode: cfg.execution_mode } })),
    pauseMission: vi.fn(async () => ({})),
    resumeMission: vi.fn(async () => ({})),
    confirmDecision: vi.fn(async () => ({})),
    rejectDecision: vi.fn(async () => ({})),
    executeDecision: vi.fn(async () => ({ data: { success: true, execution_time_ms: 5, output: 'hecho' } })),
    fetchSession: vi.fn(async () => null),
    fetchSessionDecisions: vi.fn(async () => ({ data: [] })),
    fetchAllEvents: vi.fn(async () => []),
    fetchDecisionDetail: vi.fn(async () => null),
    ...overrides.api,
  };
  let sessionsList = [];
  const persisted = vi.fn();
  const env = {
    streams,
    api,
    persisted,
    notify: vi.fn(),
    ui: { showLive: vi.fn(), closeDrawer: vi.fn(), openStep: vi.fn() },
    get sessionsList() { return sessionsList; },
  };
  env.deps = {
    api,
    notify: env.notify,
    chats: createChatRegistry({ load: () => ({}), persist: persisted, delayMs: 10 }),
    rebuildChat: vi.fn(() => []),
    buildCascade: () => ({ provider: 'cascade' }),
    getMissionConfig: () => ({ executionMode: 'local_restricted', llmProvider: 'simulator', supervisor: 'laya', customModel: '' }),
    getSessionsList: () => sessionsList,
    setSessionsList: (v) => { sessionsList = typeof v === 'function' ? v(sessionsList) : v; },
    onRuntimeActive: vi.fn(),
    ui: env.ui,
    ...overrides.deps,
  };
  return env;
}

const seed = (store, sid = 'rec-fixture', extra = {}) => store.setSession({ ...MANUAL.initial.session, sessionId: sid, ...extra });
const pushAll = (stream, events) => events.forEach((ev) => stream.onMessage({ action: 'event', data: structuredClone(ev) }));
const asJson = (o) => JSON.parse(JSON.stringify(o));

beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { vi.useRealTimers(); });

describe('WebSocket por sesión', () => {
  it('no abre nada con la sesión «ready» y abre uno al fijar un id real', () => {
    const env = makeEnv();
    const store = new AgentSession(env.deps);
    expect(env.streams).toHaveLength(0);
    seed(store);
    expect(env.streams).toHaveLength(1);
    expect(env.streams[0].sid).toBe('rec-fixture');
  });

  it('cambiar de sesión cierra el socket anterior y abre el nuevo; el mismo id no reabre', () => {
    const env = makeEnv();
    const store = new AgentSession(env.deps);
    seed(store, 'a');
    seed(store, 'a', { status: 'otra cosa' });
    expect(env.streams).toHaveLength(1);
    seed(store, 'b');
    expect(env.streams[0].closed).toBe(true);
    expect(env.streams.map((s) => s.sid)).toEqual(['a', 'b']);
    expect(env.streams[1].closed).toBe(false);
  });

  it('dispose cierra el socket y deja de aceptar eventos; activate lo recupera', () => {
    const env = makeEnv();
    const store = new AgentSession(env.deps);
    seed(store, 'a');
    const first = env.streams[0];
    store.dispose();
    expect(first.closed).toBe(true);
    pushAll(first, MANUAL.events.slice(0, 3));
    expect(store.getState().events).toHaveLength(0);
    store.activate();
    expect(env.streams).toHaveLength(2);
    expect(env.streams[1].sid).toBe('a');
    expect(env.streams[1].closed).toBe(false);
  });

  it('pide al servidor los eventos posteriores al último visto', () => {
    const env = makeEnv();
    const store = new AgentSession(env.deps);
    seed(store, 'a');
    pushAll(env.streams[0], MANUAL.events.slice(0, 5));
    expect(env.streams[0].getSeq()).toBe(MANUAL.events[4].sequence);
  });

  it('avisa de que el servidor responde cuando el socket conecta', () => {
    const env = makeEnv();
    const store = new AgentSession(env.deps);
    seed(store, 'a');
    env.streams[0].onStatus('connecting');
    expect(env.deps.onRuntimeActive).not.toHaveBeenCalled();
    env.streams[0].onStatus('connected');
    expect(env.deps.onRuntimeActive).toHaveBeenCalledWith(true);
  });
});

describe('dos sesiones a la vez', () => {
  it('cada una recibe solo sus eventos y llega al estado esperado', () => {
    const envA = makeEnv();
    const envB = makeEnv();
    const a = new AgentSession(envA.deps);
    const b = new AgentSession(envB.deps);
    a.setSession(MANUAL.initial.session);
    b.setSession({ ...MANUAL.initial.session, sessionId: 'otra' });
    const sim = fixtures.find((f) => f.name === 'simulator_mission');
    pushAll(envA.streams[0], MANUAL.events);
    pushAll(envB.streams[0], sim.events);
    expect(a.getState().events).toHaveLength(MANUAL.events.length);
    expect(b.getState().events).toHaveLength(sim.events.length);
    expect(a.getState().nodes.length).toBe(golden[MANUAL.name].final.nodes.length);
    expect(b.getState().nodes.length).toBe(golden.simulator_mission.final.nodes.length);
    expect(Object.keys(a.getState().decisionsMap)).not.toEqual(Object.keys(b.getState().decisionsMap));
  });

  it('un evento de una no cambia a la otra (ni su referencia)', () => {
    const env = makeEnv();
    const a = new AgentSession(env.deps);
    const b = new AgentSession(makeEnv().deps);
    seed(a, 'a');
    seed(b, 'b');
    const before = b.getState();
    pushAll(env.streams[0], MANUAL.events.slice(0, 6));
    expect(b.getState()).toBe(before);
  });

  it('el registro abre una sesión por panel y cerrar un panel cierra SOLO su socket', () => {
    const streams = [];
    const registry = createSessionRegistry((panelId) => {
      const env = makeEnv();
      env.api.createWebSocketStream.mockImplementation((sid) => { const s = { sid, panelId, closed: false, close() { s.closed = true; } }; streams.push(s); return s; });
      return env.deps;
    });
    const p1 = registry.open('p1');
    const p2 = registry.open('p2');
    expect(registry.open('p1')).toBe(p1);
    seed(p1, 'uno');
    seed(p2, 'dos');
    expect(registry.ids()).toEqual(['p1', 'p2']);
    registry.close('p1');
    expect(streams.find((s) => s.sid === 'uno').closed).toBe(true);
    expect(streams.find((s) => s.sid === 'dos').closed).toBe(false);
    expect(registry.get('p1')).toBeNull();
    registry.closeAll();
    expect(streams.every((s) => s.closed)).toBe(true);
    expect(registry.ids()).toEqual([]);
  });
});

describe('suscripción', () => {
  it('avisa una vez por cambio y una sola vez dentro de un lote', () => {
    const store = new AgentSession(makeEnv().deps);
    const listener = vi.fn();
    const off = store.subscribe(listener);
    store.setIsRunning(true);
    expect(listener).toHaveBeenCalledTimes(1);
    store.setIsRunning(true);                         // sin cambio
    expect(listener).toHaveBeenCalledTimes(1);
    store.batch(() => { store.setIsRunning(false); store.setIsPaused(true); store.log('INFO', 'x'); });
    expect(listener).toHaveBeenCalledTimes(2);
    off();
    store.setIsRunning(true);
    expect(listener).toHaveBeenCalledTimes(2);
  });
});

describe('acciones', () => {
  it('startMission: fija la sesión, abre su socket, guarda el chat y registra la lista', async () => {
    const env = makeEnv();
    const store = new AgentSession(env.deps);
    store.setChatMessages([{ id: 'u', role: 'user', text: 'hola' }, { id: 'a', role: 'assistant' }]);
    await store.startMission({ goal: 'haz algo', execution_mode: 'full_access', llm_provider: 'simulator', supervisor: 'laya', max_steps: 3, workflow_id: 'wf' });
    const s = store.getState();
    expect(s.session).toMatchObject({ sessionId: 'run-1', goal: 'haz algo', status: 'Active', execution_mode: 'full_access' });
    expect(s.isRunning).toBe(true);
    expect(s.followExecution).toEqual({ workflowId: 'wf', executionId: 'wfx-run-1' });
    expect(s.nodes).toHaveLength(1);
    expect(s.logs[0].message).toContain('#run-1');
    expect(env.streams.map((x) => x.sid)).toEqual(['run-1']);
    expect(env.deps.chats.chats['run-1']).toHaveLength(2);
    expect(env.sessionsList.map((x) => x.session_id)).toEqual(['run-1']);
    expect(env.api.runMission).toHaveBeenCalledWith(expect.objectContaining({ goal: 'haz algo', step_delay_ms: 1000, supervisor: 'laya' }));
  });

  it('startMission: la cascada se pide al configurador y un fallo deja la sesión parada con el error en el registro', async () => {
    const env = makeEnv({ api: { runMission: vi.fn(async () => { throw new Error('HTTP 500'); }) } });
    const store = new AgentSession(env.deps);
    await store.startMission({ goal: 'x', llm_provider: 'simulator', supervisor: 'cascade' });
    expect(env.api.runMission).toHaveBeenCalledWith(expect.objectContaining({ decision_model: { provider: 'cascade' }, supervisor: 'cascade' }));
    expect(store.getState().isRunning).toBe(false);
    expect(store.getState().logs.at(-1)).toMatchObject({ level: 'ERROR', message: 'No se pudo iniciar la misión: HTTP 500' });
    expect(env.streams).toHaveLength(0);
  });

  it('startMission: el chat de la sesión anterior conserva lo previo si era real, y se descarta si no lo era', async () => {
    const env = makeEnv();
    const store = new AgentSession(env.deps);
    store.newClean();
    const localSid = store.sid;
    store.setChatMessages([{ id: '1' }, { id: '2' }, { id: '3' }, { id: '4' }]);
    await store.startMission({ goal: 'x', llm_provider: 'simulator', supervisor: 'laya' });
    expect(env.deps.chats.chats[localSid]).toBeUndefined();          // la sesión local no existía en el servidor
    env.deps.setSessionsList([{ session_id: 'run-1' }]);
    env.api.runMission.mockResolvedValueOnce({ data: { session_id: 'run-2' } });
    await store.startMission({ goal: 'y', llm_provider: 'simulator', supervisor: 'laya' });
    expect(env.deps.chats.chats['run-1']).toHaveLength(2);           // lo anterior a este mensaje
    expect(env.deps.chats.chats['run-2']).toHaveLength(4);
  });

  it('pause / resume / stop', async () => {
    const env = makeEnv();
    const store = new AgentSession(env.deps);
    seed(store, 'a');
    store.setIsRunning(true);
    await store.pause();
    expect(env.api.pauseMission).toHaveBeenCalledWith('a');
    expect(store.getState().isPaused).toBe(true);
    await store.resume();
    expect(store.getState().isPaused).toBe(false);
    store.setIsPaused(true);
    store.stop();
    expect(store.getState()).toMatchObject({ isRunning: false, isPaused: false });
    expect(store.getState().logs.map((l) => l.message)).toEqual(expect.arrayContaining(['Misión pausada por el operador.', 'Misión reanudada por el operador.', 'Misión detenida.']));
  });

  it('pause no cambia nada si el servidor falla', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const env = makeEnv({ api: { pauseMission: vi.fn(async () => { throw new Error('x'); }) } });
    const store = new AgentSession(env.deps);
    seed(store, 'a');
    await store.pause();
    expect(store.getState().isPaused).toBe(false);
    warn.mockRestore();
  });

  it('newClean: sesión local nueva, chat vacío, cajón cerrado y vista en conversación', () => {
    const env = makeEnv();
    const store = new AgentSession(env.deps);
    store.setChatMessages([{ id: 'x' }]);
    store.newClean();
    const s = store.getState();
    expect(s.session.sessionId).toMatch(/^s-[a-z0-9]+$/);
    expect(s.session.execution_mode).toBe('local_restricted');
    expect(s.chatMessages).toEqual([]);
    expect(s.nodes).toHaveLength(1);
    expect(env.ui.closeDrawer).toHaveBeenCalled();
    expect(env.ui.showLive).toHaveBeenCalled();
    expect(env.streams.map((x) => x.sid)).toEqual([s.session.sessionId]);
  });

  it('approve: registra la aprobación y actualiza decisión, nodo y métricas; si el servidor falla, avisa y no cambia', async () => {
    const env = makeEnv();
    const store = new AgentSession(env.deps);
    seed(store);
    pushAll(env.streams[0], MANUAL.events);
    const review = Object.entries(store.getState().decisionsMap).find(([, d]) => d.status === 'REVIEW');
    expect(review).toBeTruthy();
    const [nodeId, dec] = review;
    const before = store.getState().session.metrics;
    await store.approve(dec.decisionId, nodeId);
    const s = store.getState();
    expect(env.api.confirmDecision).toHaveBeenCalledWith(dec.decisionId, true, 'Authorized from Decision Inspector', 'operator_admin', 'operator');
    expect(s.decisionsMap[nodeId].status).toBe('ALLOW');
    expect(s.decisionsMap[nodeId].capability.issued).toBe(true);
    expect(s.nodes.find((n) => n.id === nodeId).status).toBe('ALLOW');
    expect(s.session.metrics.allowed).toBe(before.allowed + 1);
    expect(s.session.metrics.review).toBe(Math.max(0, before.review - 1));
    expect(s.logs.at(-1).message).toContain('autorizada por el operador');

    env.api.confirmDecision.mockRejectedValueOnce(new Error('HTTP 409'));
    const frozen = store.getState();
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    await store.approve(dec.decisionId, nodeId);
    expect(env.notify).toHaveBeenCalledWith('No se pudo registrar la aprobación: HTTP 409');
    expect(store.getState()).toBe(frozen);
  });

  it('reject: bloquea decisión y nodo y deja un mensaje del operador en el chat', async () => {
    const env = makeEnv();
    const store = new AgentSession(env.deps);
    seed(store);
    pushAll(env.streams[0], MANUAL.events);
    store.sid = 'rec-fixture';
    const [nodeId, dec] = Object.entries(store.getState().decisionsMap).find(([, d]) => d.status === 'REVIEW');
    await store.reject(dec.decisionId, 'no me fío', nodeId);
    const s = store.getState();
    expect(env.api.rejectDecision).toHaveBeenCalledWith(dec.decisionId, 'no me fío', 'operator_admin', 'operator');
    expect(s.decisionsMap[nodeId]).toMatchObject({ status: 'BLOCKED', reason: 'no me fío' });
    expect(s.nodes.find((n) => n.id === nodeId).status).toBe('BLOCK');
    expect(s.chatMessages.at(-1)).toMatchObject({ role: 'operator', text: 'Rechazado: no me fío' });
    expect(env.deps.chats.chats['rec-fixture']).toHaveLength(1);
    expect(s.logs.at(-1)).toMatchObject({ level: 'ERROR' });
  });

  it('execute: registra el resultado en el registro de la sesión', async () => {
    const env = makeEnv();
    const store = new AgentSession(env.deps);
    await store.execute('d_1', { token: 'cap' });
    expect(env.api.executeDecision).toHaveBeenCalledWith('d_1', 'cap');
    expect(store.getState().logs.at(-1)).toMatchObject({ level: 'INFO', message: 'Ejecución en el sandbox terminada (5 ms): hecho' });
    env.api.executeDecision.mockRejectedValueOnce(new Error('denegado'));
    await store.execute('d_1', null);
    expect(store.getState().logs.at(-1)).toMatchObject({ level: 'ERROR', message: 'La ejecución falló: denegado' });
  });

  it('selectNode: abre el paso y pide el detalle de la decisión', () => {
    const env = makeEnv();
    const store = new AgentSession(env.deps);
    store.selectNode('a1');
    expect(store.getState().selectedNodeId).toBe('a1');
    expect(env.ui.openStep).toHaveBeenCalled();
    expect(env.api.fetchDecisionDetail).toHaveBeenCalledWith('d_a1');
    env.api.fetchDecisionDetail.mockClear();
    store.selectNode('start');
    expect(env.api.fetchDecisionDetail).not.toHaveBeenCalled();
  });
});

describe('loadSession', () => {
  const snapshot = (sid) => ({ data: { session_id: sid, goal: `meta ${sid}`, status: 'Completed', execution_mode: 'local_restricted', summary: { total_decisions: 3, allowed_count: 2, blocked_count: 1, review_count: 0 }, created_at: '2026-11-15T12:00:00Z' } });

  it('carga snapshot, historial y chat reconstruido, y abre el socket de esa sesión', async () => {
    const env = makeEnv({ api: { fetchSession: vi.fn(async (sid) => snapshot(sid)), fetchAllEvents: vi.fn(async () => MANUAL.events.slice(0, 4)) } });
    env.deps.rebuildChat = vi.fn(() => [{ id: 'r', role: 'user', text: 'reconstruido' }]);
    const store = new AgentSession(env.deps);
    await store.loadSession('old-1');
    const s = store.getState();
    expect(s.session).toMatchObject({ sessionId: 'old-1', goal: 'meta old-1', status: 'Completed', metrics: { totalDecisions: 3, allowed: 2, blocked: 1, review: 0 } });
    expect(s.events.map((e) => e.sequence)).toEqual(MANUAL.events.slice(0, 4).map((e) => e.sequence).reverse());
    expect(s.chatMessages).toEqual([{ id: 'r', role: 'user', text: 'reconstruido' }]);
    expect(env.deps.chats.chats['old-1']).toHaveLength(1);
    expect(env.streams.map((x) => x.sid)).toEqual(['old-1']);
    expect(env.ui.showLive).toHaveBeenCalled();
    expect(s.logs.at(-1).message).toBe('Sesión #old-1 cargada exitosamente.');
  });

  it('usa el chat guardado y no lo reconstruye', async () => {
    const env = makeEnv({ api: { fetchSession: vi.fn(async (sid) => snapshot(sid)) } });
    env.deps.chats.chats['old-2'] = [{ id: 'guardado' }];
    const store = new AgentSession(env.deps);
    await store.loadSession('old-2');
    expect(store.getState().chatMessages).toEqual([{ id: 'guardado' }]);
    expect(env.deps.rebuildChat).not.toHaveBeenCalled();
  });

  it('sin snapshot en el servidor deja la sesión con ese id', async () => {
    const env = makeEnv();
    const store = new AgentSession(env.deps);
    await store.loadSession('fantasma');
    expect(store.getState().session.sessionId).toBe('fantasma');
    expect(env.streams.map((x) => x.sid)).toEqual(['fantasma']);
  });

  it('si se pide otra sesión mientras la primera carga, el historial de la primera se descarta', async () => {
    let release;
    const slow = new Promise((r) => { release = r; });
    const env = makeEnv({ api: { fetchSession: vi.fn(async (sid) => snapshot(sid)), fetchAllEvents: vi.fn(async (sid) => (sid === 'lenta' ? slow : [])) } });
    const store = new AgentSession(env.deps);
    const first = store.loadSession('lenta');
    await vi.advanceTimersByTimeAsync(0);
    await store.loadSession('rapida');
    release(MANUAL.events.slice(0, 4));
    await first;
    expect(store.getState().events).toEqual([]);                      // nada de «lenta» pisó a «rapida»
    expect(store.sid).toBe('rapida');
  });
});

describe('chat compartido', () => {
  it('updateChat guarda bajo la sesión actual, la deja como la más reciente y persiste una vez', () => {
    const env = makeEnv();
    const store = new AgentSession(env.deps);
    store.sid = 'a';
    store.updateChat([{ id: 1 }]);
    store.sid = 'b';
    store.updateChat([{ id: 2 }]);
    store.sid = 'a';
    store.updateChat((prev) => [...prev, { id: 3 }]);
    expect(Object.keys(env.deps.chats.chats)).toEqual(['b', 'a']);
    vi.advanceTimersByTime(50);
    expect(env.persisted).toHaveBeenCalledTimes(1);
    expect(env.persisted.mock.calls[0][0]).toBe(env.deps.chats.chats);
  });

  it('dos sesiones comparten un único registro: ninguna pisa lo de la otra', () => {
    const chats = createChatRegistry({ load: () => ({}), persist: vi.fn(), delayMs: 10 });
    const a = new AgentSession({ ...makeEnv().deps, chats });
    const b = new AgentSession({ ...makeEnv().deps, chats });
    a.sid = 'a';
    b.sid = 'b';
    a.updateChat([{ id: 'de-a' }]);
    b.updateChat([{ id: 'de-b' }]);
    expect(chats.chats).toEqual({ a: [{ id: 'de-a' }], b: [{ id: 'de-b' }] });
  });
});

describe('demostración', () => {
  it('loadDemo deja un estado completo y coherente', () => {
    const store = new AgentSession(makeEnv().deps);
    store.loadDemo();
    const s = store.getState();
    expect(s.selectedNodeId).toBe('node-5');
    expect(s.chatMessages).toHaveLength(2);
    expect(asJson(s.isRunning)).toBe(false);
  });
});
