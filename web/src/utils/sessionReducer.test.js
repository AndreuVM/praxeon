// Prueba DIFERENCIAL del reducer de sesión (FE-007): el estado que produce `reduceEvent` evento a evento debe ser exactamente el que
// producía el código original de App.jsx (commit 3e504de) con los mismos eventos. Los «golden» se generaron EJECUTANDO ese callback
// original (no escritos a mano) sobre flujos grabados del backend real (`manual_*`, `simulator_mission`) y flujos sintéticos para
// lo que el backend no emite en pruebas (workflow, observación final, intervención). Zona horaria fija: TZ=UTC (vite.config.js).
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { READY_NODES, READY_SESSION, clockOf, createSessionState, describeEvent, reduceEvent } from './sessionReducer.js';

const here = new URL('./__fixtures__/', import.meta.url);
const fixtures = JSON.parse(readFileSync(new URL('sessionStreams.json', here), 'utf8'));
const golden = JSON.parse(readFileSync(new URL('sessionGolden.json', here), 'utf8'));

const canon = (v) => (Array.isArray(v) ? v.map(canon) : v && typeof v === 'object' ? Object.fromEntries(Object.keys(v).sort().map((k) => [k, canon(v[k])])) : v);
const sha = (o) => createHash('sha256').update(JSON.stringify(canon(o))).digest('hex').slice(0, 16);
const asJson = (o) => JSON.parse(JSON.stringify(o));               // quita los `undefined`, como hace el fixture

const start = (fx) => createSessionState({ ...structuredClone(fx.initial), maxSeq: 0 });

describe('reduceEvent frente al código original de App.jsx', () => {
  it('los fixtures son los esperados (zona horaria fija)', () => {
    expect(new Date('2026-11-15T12:30:00Z').toTimeString().startsWith('12:30:00')).toBe(true);
    expect(fixtures.map((f) => f.name)).toEqual(Object.keys(golden));
  });

  for (const fx of fixtures) {
    it(`${fx.name}: el estado coincide tras CADA evento`, () => {
      let state = start(fx);
      fx.events.forEach((ev, i) => {
        state = reduceEvent(state, structuredClone(ev), { customModel: fx.customModel });
        const got = sha({ ...state });
        expect(got, `${fx.name}: el estado diverge tras el evento ${i + 1} (${ev.type})`).toBe(golden[fx.name].digests[i]);
      });
      expect(asJson(state)).toEqual(asJson(golden[fx.name].final));
    });
  }

  it('no modifica el estado ni el evento recibidos', () => {
    const fx = fixtures[0];
    const state = start(fx);
    const frozen = structuredClone(state);
    const ev = structuredClone(fx.events[2]);
    const evFrozen = structuredClone(ev);
    reduceEvent(state, ev, {});
    expect(state).toEqual(frozen);
    expect(ev).toEqual(evFrozen);
  });

  it('dos estados reciben solo sus propios eventos', () => {
    const [a, b] = [fixtures[0], fixtures[3]];
    let sa = start(a);
    let sb = start(b);
    for (const ev of a.events) sa = reduceEvent(sa, ev, {});
    for (const ev of b.events) sb = reduceEvent(sb, ev, {});
    expect(sa.events).toHaveLength(a.events.length);
    expect(sb.events).toHaveLength(b.events.length);
    expect(asJson(sa)).toEqual(asJson(golden[a.name].final));
    expect(asJson(sb)).toEqual(asJson(golden[b.name].final));
  });
});

describe('piezas sueltas', () => {
  it('el estado inicial es el de «listo» y no comparte referencias mutables con otro', () => {
    const s = createSessionState();
    expect(s.session).toBe(READY_SESSION);
    expect(s.nodes).toBe(READY_NODES);
    expect(s.selectedNodeId).toBe('start');
    expect(s.logs).toEqual([]);
    expect(s.decisionsMap).not.toBe(createSessionState().decisionsMap);
  });
  it('clockOf da «HH:MM:SS» local', () => {
    expect(clockOf('2026-11-15T12:30:05Z')).toMatch(/^\d\d:\d\d:\d\d$/);
  });
  it('un evento desconocido solo añade evento y registro', () => {
    const s = reduceEvent(createSessionState(), { type: 'algo.nuevo', payload: 'x', timestamp: '2026-11-15T12:30:00Z', sequence: 7 }, {});
    expect(s.events).toHaveLength(1);
    expect(s.logs).toHaveLength(1);
    expect(s.logs[0].message).toBe('[algo.nuevo]');
    expect(s.maxSeq).toBe(7);
    expect(s.nodes).toBe(READY_NODES);
  });
  it('un conflicto de escritura entre sesiones (FE-009) se registra como aviso, sin tocar el árbol', () => {
    const ev = { type: 'workspace.file_conflict', node_id: 'a1', timestamp: '2026-11-15T12:30:00Z', sequence: 3, payload: { path: 'f.txt', conflicting_session: 'B', message: "Conflicto de escritura en 'f.txt': …" } };
    const before = createSessionState();
    const s = reduceEvent(before, ev, {});
    expect(s.logs[0]).toMatchObject({ level: 'WARN', message: "[CONFLICTO] Conflicto de escritura en 'f.txt': …" });
    expect(s.events[0]).toMatchObject({ type: 'workspace.file_conflict', seq: 3 });
    expect(s.nodes).toBe(before.nodes);
    expect(describeEvent({ type: 'workspace.file_conflict', payload: { path: 'x.txt' } }, undefined)).toBe("[CONFLICTO] Otra sesión modificó 'x.txt'");
  });
  it('las herramientas de agy (FE-015): la activación avisa, el uso se resume y las imágenes se acumulan sin repetirse', () => {
    const at = (type, payload, sequence) => ({ type, node_id: 'root', timestamp: '2026-11-15T12:30:00Z', sequence, payload });
    let s = createSessionState();
    s = reduceEvent(s, at('agy.tools_enabled', { groups: ['images'], titles: ['Generación de imágenes'] }, 1), {});
    expect(s.logs.at(-1)).toMatchObject({ level: 'WARN' });
    expect(s.logs.at(-1).message).toContain('Generación de imágenes');
    expect(s.logs.at(-1).message).toContain('sin la política de PRAXEON');
    s = reduceEvent(s, at('agy.tool_used', { tools: [{ name: 'generate_image', detail: 'circulo' }], denied: 2 }, 2), {});
    expect(s.logs.at(-1).message).toBe('[AGY] Herramientas usadas: generate_image (circulo) · 2 bloqueada(s)');
    const img = { rel_path: '.praxeon/generated/s-1/circulo.jpg', name: 'circulo.jpg', width: 1024, height: 1024 };
    s = reduceEvent(s, at('agy.image_generated', img, 3), {});
    s = reduceEvent(s, at('agy.image_generated', img, 4), {});                              // el mismo evento repetido no duplica
    expect(s.session.generatedImages).toEqual([img]);
    s = reduceEvent(s, at('agy.image_generated', { ...img, rel_path: '../../fuera.png' }, 5), {});   // una ruta fuera de la carpeta de imágenes se ignora
    s = reduceEvent(s, at('agy.image_generated', { ...img, rel_path: '.praxeon/attachments/s/a.png' }, 6), {});
    expect(s.session.generatedImages).toEqual([img]);
  });
  it('el rótulo de execution.started depende del modo', () => {
    expect(describeEvent({ type: 'execution.started', payload: { tool: 't' } }, 'full_access')).toContain('Full Access');
    expect(describeEvent({ type: 'execution.started', payload: { tool: 't' } }, 'local_restricted')).toContain('local sandbox');
  });
});
