import { describe, expect, it } from 'vitest';
import {
  MAIN_ID, MAX_PANELS, STORAGE_KEY, addPanel, closePanel, createLayoutStore, focusPanel, gridFor, initialLayout, nextPanelId, pickConfig, sanitize, toggleMaximize, visibleIds,
} from './panelLayout.js';
import { approvalQueue, latestConflict, noticeFor, panelStatus, pendingApprovals } from './approvals.js';

const memory = (initial = {}) => {
  const data = { ...initial };
  return { data, getItem: (k) => (k in data ? data[k] : null), setItem: (k, v) => { data[k] = String(v); } };
};
const open = (n) => {
  let layout = initialLayout();
  for (let i = 1; i < n; i += 1) layout = addPanel(layout).layout;
  return layout;
};

describe('abrir, enfocar y cerrar paneles', () => {
  it('empieza con el panel principal enfocado', () => {
    expect(initialLayout()).toEqual({ order: [MAIN_ID], focusedId: MAIN_ID, maximizedId: null });
  });
  it('un panel nuevo toma el siguiente id libre y se enfoca; los ids cerrados se reutilizan', () => {
    let { layout, id } = addPanel(initialLayout());
    expect(id).toBe('p2');
    expect(layout.focusedId).toBe('p2');
    ({ layout, id } = addPanel(layout));
    expect(id).toBe('p3');
    layout = closePanel(layout, 'p2');
    expect(nextPanelId(layout.order)).toBe('p2');
  });
  it('no se pasa del máximo de paneles', () => {
    expect(open(MAX_PANELS).order).toHaveLength(MAX_PANELS);
    expect(addPanel(open(MAX_PANELS))).toBeNull();
  });
  it('el último panel no se cierra', () => {
    const l = initialLayout();
    expect(closePanel(l, MAIN_ID)).toBe(l);
  });
  it('al cerrar el enfocado el foco pasa al anterior; si era el primero, al siguiente', () => {
    let l = open(3);                                  // main, p2, p3 (enfocado p3)
    l = closePanel(l, 'p3');
    expect(l.focusedId).toBe('p2');
    l = focusPanel(open(3), 'main');
    expect(closePanel(l, 'main').focusedId).toBe('p2');
  });
  it('cerrar un panel no enfocado conserva el foco; cerrar el maximizado lo desmaximiza', () => {
    let l = toggleMaximize(open(3), 'p2');
    expect(closePanel(l, 'p3').focusedId).toBe('p2');
    expect(closePanel(l, 'p3').maximizedId).toBe('p2');
    expect(closePanel(l, 'p2').maximizedId).toBeNull();
    l = focusPanel(open(3), 'p2');
    expect(closePanel(l, 'p3').focusedId).toBe('p2');
  });
  it('enfocar un panel que no existe no cambia nada (misma referencia)', () => {
    const l = initialLayout();
    expect(focusPanel(l, 'zzz')).toBe(l);
    expect(focusPanel(l, MAIN_ID)).toBe(l);
    expect(toggleMaximize(l, 'zzz')).toBe(l);
  });
});

describe('qué se ve', () => {
  it('hasta 4 paneles visibles y el resto en pestañas', () => {
    expect(visibleIds(open(2))).toEqual(['main', 'p2']);
    expect(visibleIds(focusPanel(open(6), 'main'))).toEqual(['main', 'p2', 'p3', 'p4']);
    expect(visibleIds(open(6))).toEqual(['main', 'p2', 'p3', 'p6']);      // el recién abierto (enfocado) ocupa el último hueco
  });
  it('el panel enfocado siempre se ve: ocupa el último hueco si no cabía', () => {
    const l = focusPanel(open(6), 'p5');
    expect(visibleIds(l)).toEqual(['main', 'p2', 'p3', 'p5']);
    expect(visibleIds(focusPanel(open(6), 'p3'))).toEqual(['main', 'p2', 'p3', 'p4']);
  });
  it('con poco ancho solo se ve uno (el enfocado)', () => {
    expect(visibleIds(open(3), 1)).toEqual(['p3']);
    expect(visibleIds(focusPanel(open(3), 'main'), 1)).toEqual(['main']);
  });
  it('maximizado: solo ese; al volver a pulsar, todos', () => {
    let l = toggleMaximize(open(3), 'p2');
    expect(visibleIds(l)).toEqual(['p2']);
    expect(l.focusedId).toBe('p2');
    l = toggleMaximize(l, 'p2');
    expect(visibleIds(l)).toEqual(['main', 'p2', 'p3']);
  });
  it('cuadrícula: 1, 2 en columnas o 2×2', () => {
    expect(gridFor(1)).toEqual({ columns: 1, rows: 1 });
    expect(gridFor(2)).toEqual({ columns: 2, rows: 1 });
    expect(gridFor(3)).toEqual({ columns: 2, rows: 2 });
    expect(gridFor(4)).toEqual({ columns: 2, rows: 2 });
  });
});

describe('sanitize', () => {
  it('lo no válido da el panel principal', () => {
    for (const raw of [null, undefined, 5, 'x', {}, { panels: 'no' }]) expect(sanitize(raw)).toEqual({ panels: [{ id: 'main', sessionId: null, config: {} }], focusedId: 'main' });
  });
  it('descarta ids raros y duplicados, añade el principal si falta y limpia sesiones inválidas', () => {
    const s = sanitize({ panels: [{ id: 'p2', sessionId: 'abc' }, { id: 'p2', sessionId: 'otra' }, { id: '../x' }, { id: 'p3', sessionId: 'ready' }, { id: 'p4', sessionId: 5 }, null], focusedId: 'p9' });
    expect(s.panels.map((p) => p.id)).toEqual(['main', 'p2', 'p3', 'p4']);
    expect(s.panels.map((p) => p.sessionId)).toEqual([null, 'abc', null, null]);
    expect(s.panels.every((p) => typeof p.config === 'object')).toBe(true);
    expect(s.focusedId).toBe('main');
  });
  it('limita la cantidad de paneles', () => {
    const many = Array.from({ length: 30 }, (_, i) => ({ id: `p${i + 2}`, sessionId: null }));
    expect(sanitize({ panels: many }).panels.length).toBeLessThanOrEqual(MAX_PANELS);
  });
});

describe('configuración guardada de cada panel (FE-011)', () => {
  it('solo se guardan los campos permitidos y valores simples: nunca claves ni Full Access', () => {
    const cfg = pickConfig({ llmProvider: 'groq', customModel: 'm', apiKey: 'SECRETO', fullAccessConfirmed: true, maxSteps: 12, baseUrl: 'http://x', workflowId: null, executionMode: { a: 1 }, extra: 'x', supervisor: 'x'.repeat(600) });
    expect(cfg).toEqual({ llmProvider: 'groq', customModel: 'm', maxSteps: 12, baseUrl: 'http://x' });
    expect(JSON.stringify(cfg)).not.toContain('SECRETO');
    expect(pickConfig(null)).toEqual({});
    expect(pickConfig({ maxSteps: Infinity })).toEqual({});
  });
  it('el almacén no guarda claves aunque se las pasen', () => {
    const mem = memory();
    createLayoutStore(mem).save('/w', { panels: [{ id: 'main', sessionId: null, config: { llmProvider: 'groq', apiKey: 'SECRETO', fullAccessConfirmed: true } }] });
    expect(mem.data[STORAGE_KEY]).not.toContain('SECRETO');
    expect(mem.data[STORAGE_KEY]).not.toContain('fullAccessConfirmed');
    expect(createLayoutStore(mem).load('/w').panels[0].config).toEqual({ llmProvider: 'groq' });
  });
});

describe('almacén por workspace', () => {
  it('guarda y recupera la disposición de cada workspace sin mezclar', () => {
    const store = createLayoutStore(memory());
    store.save('/w/a', { panels: [{ id: 'main', sessionId: 's1', config: { llmProvider: 'groq' } }, { id: 'p2', sessionId: 's2', config: { llmProvider: 'ollama', maxSteps: 7 } }], focusedId: 'p2' });
    store.save('/w/b', { panels: [{ id: 'main', sessionId: 'x' }], focusedId: 'main' });
    expect(store.load('/w/a')).toEqual({ panels: [{ id: 'main', sessionId: 's1', config: { llmProvider: 'groq' } }, { id: 'p2', sessionId: 's2', config: { llmProvider: 'ollama', maxSteps: 7 } }], focusedId: 'p2' });
    expect(store.load('/w/b').panels).toHaveLength(1);
    expect(store.load('/w/nuevo').panels).toEqual([{ id: 'main', sessionId: null, config: {} }]);
  });
  it('JSON corrupto, sin almacenamiento o sin workspace no rompe nada', () => {
    expect(createLayoutStore(memory({ [STORAGE_KEY]: '{no json' })).load('/w').focusedId).toBe('main');
    const store = createLayoutStore(null);
    expect(() => store.save('/w', { panels: [] })).not.toThrow();
    expect(store.load('/w').focusedId).toBe('main');
    const mem = memory();
    createLayoutStore(mem).save('', { panels: [] });
    expect(mem.data).toEqual({});
    const throwing = { getItem: () => null, setItem: () => { throw new Error('Quota'); } };
    expect(() => createLayoutStore(throwing).save('/w', { panels: [] })).not.toThrow();
  });
  it('recuerda un máximo de workspaces', () => {
    const store = createLayoutStore(memory());
    for (let i = 0; i < 25; i += 1) store.save(`/w${i}`, { panels: [{ id: 'main', sessionId: `s${i}` }, { id: 'p2', sessionId: null }], focusedId: 'p2' });
    expect(store.load('/w0').panels).toHaveLength(1);
    expect(store.load('/w24').panels).toHaveLength(2);
  });
});

describe('aprobaciones y avisos', () => {
  const review = (id) => ({ status: 'REVIEW', decisionId: `d_${id}`, tool: 'run_command', actionCommand: 'git push', riskLevel: 'HIGH', semanticEvaluation: [{ score: 0.7 }] });
  it('solo cuentan las retenidas sin capacidad emitida y con la misión en marcha', () => {
    const state = { isRunning: true, decisionsMap: { a: review('a'), b: { ...review('b'), capability: { issued: true } }, c: { status: 'ALLOW' } } };
    expect(pendingApprovals(state)).toEqual([{ nodeId: 'a', decisionId: 'd_a', tool: 'run_command', command: 'git push', riskLevel: 'HIGH', laya: 0.7 }]);
    expect(pendingApprovals({ ...state, isRunning: false })).toEqual([]);
  });
  it('la cola global junta las de todos los paneles con su panel', () => {
    const q = approvalQueue([
      { id: 'main', label: 'Agente 1', state: { isRunning: true, decisionsMap: { a: review('a') } } },
      { id: 'p2', label: 'Agente 2', state: { isRunning: true, decisionsMap: { b: review('b'), c: review('c') } } },
      { id: 'p3', label: 'Agente 3', state: { isRunning: false, decisionsMap: { d: review('d') } } },
    ]);
    expect(q.map((a) => `${a.panelId}:${a.nodeId}`)).toEqual(['main:a', 'p2:b', 'p2:c']);
    expect(q[1].panelLabel).toBe('Agente 2');
  });
  it('el último conflicto de escritura no descartado', () => {
    const events = [{ seq: 9, type: 'execution.completed' }, { seq: 8, type: 'workspace.file_conflict', payload: { path: 'a.txt', conflicting_session: 'B', message: 'msg' } }, { seq: 3, type: 'workspace.file_conflict', payload: { path: 'old.txt' } }];
    expect(latestConflict(events)).toEqual({ seq: 8, path: 'a.txt', by: 'B', message: 'msg' });
    expect(latestConflict(events, 8)).toBeNull();
    expect(latestConflict([{ seq: 2, type: 'workspace.file_conflict', payload: 'texto' }])).toMatchObject({ seq: 2, path: '' });
    expect(latestConflict([])).toBeNull();
  });
  it('estado visible de un panel', () => {
    const base = { isRunning: false, isPaused: false, decisionsMap: {}, session: { status: 'Ready' } };
    expect(panelStatus(base)).toEqual({ key: 'idle', label: 'Listo' });
    expect(panelStatus({ ...base, isRunning: true })).toEqual({ key: 'running', label: 'En curso' });
    expect(panelStatus({ ...base, isRunning: true, isPaused: true })).toEqual({ key: 'paused', label: 'Pausado' });
    expect(panelStatus({ ...base, isRunning: true, decisionsMap: { a: review('a') } })).toEqual({ key: 'approval', label: 'Esperando aprobación' });
    expect(panelStatus({ ...base, session: { status: 'Completed' } })).toEqual({ key: 'done', label: 'Terminado' });
    expect(panelStatus({ ...base, session: { status: 'Active (Paso 3)' } })).toEqual({ key: 'idle', label: 'Detenido' });
  });
  it('qué eventos avisan', () => {
    expect(noticeFor({ type: 'approval.requested' })).toMatchObject({ kind: 'approval' });
    expect(noticeFor({ type: 'workspace.file_conflict', payload: { path: 'x.py' } }).text).toContain('x.py');
    expect(noticeFor({ type: 'session.completed' })).toMatchObject({ kind: 'done' });
    expect(noticeFor({ type: 'policy.decided' })).toBeNull();
  });
});
