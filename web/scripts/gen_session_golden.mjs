// Genera los fixtures de FE-007: flujos de eventos y el estado que producía el código ORIGINAL de App.jsx (commit 3e504de).
// El callback del WebSocket se extrae del propio App.jsx del commit y se ejecuta con setters que aplican cada actualizador en orden.
// Uso (desde web/, con TZ=UTC):  node scripts/gen_session_golden.mjs <carpeta_con_manual_actions.json_y_mission_simulator.json> src/utils/__fixtures__
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import path from 'node:path';
import { computeTreeLayout } from '../src/utils/treeLayout.js';

const [recDir, outDir] = process.argv.slice(2);
const COMMIT = '3e504de';

// ---- 1. callback original ----------------------------------------------------------------------------------------------------
const src = execFileSync('git', ['show', `${COMMIT}:web/src/App.jsx`], { encoding: 'utf8', maxBuffer: 1 << 26 }).replace(/\r\n/g, '\n').split('\n');
const start = src.findIndex((l, i) => l.trim() === '(msg) => {' && src[i - 1]?.includes('session.sessionId,'));
const end = src.findIndex((l, i) => i > start && l === '      },' && src[i + 1]?.trim() === '(status) => {');
if (start < 0 || end < 0) throw new Error(`no se encontró el callback original (${start}, ${end})`);
const body = src.slice(start, end + 1).join('\n').replace(/,\s*$/, '');
const makeHandler = new Function(
  'setEvents', 'setLogs', 'setNodes', 'setDecisionsMap', 'setSession', 'setSelectedNodeId', 'setIsRunning', 'session', 'missionConfig', 'maxSeqRef', 'computeTreeLayout',
  `return ${body};`,
);

function runOriginal(initial, customModel, events) {
  const st = structuredClone(initial);
  const maxSeqRef = { current: 0 };
  const apply = (key) => (v) => { st[key] = typeof v === 'function' ? v(st[key]) : v; };
  const handler = makeHandler(
    apply('events'), apply('logs'), apply('nodes'), apply('decisionsMap'), apply('session'), apply('selectedNodeId'), apply('isRunning'),
    structuredClone(initial.session), { customModel }, maxSeqRef, computeTreeLayout,
  );
  const digests = [];
  for (const ev of events) {
    handler({ action: 'event', data: structuredClone(ev) });
    digests.push(sha({ ...st, maxSeq: maxSeqRef.current }));
  }
  return { final: { ...st, maxSeq: maxSeqRef.current }, digests };
}
const canon = (v) => (Array.isArray(v) ? v.map(canon) : v && typeof v === 'object' ? Object.fromEntries(Object.keys(v).sort().map((k) => [k, canon(v[k])])) : v);
const sha = (o) => createHash('sha256').update(JSON.stringify(canon(o))).digest('hex').slice(0, 16);

// ---- 2. flujos ----------------------------------------------------------------------------------------------------------------
const rec = (n) => JSON.parse(readFileSync(path.join(recDir, `${n}.json`), 'utf8'));
const scrub = (o) => JSON.parse(JSON.stringify(o)
  .replace(/C:\\\\Users\\\\[^"]*?\\\\ws/g, '/workspace').replace(/C:\\\\Users\\\\[^"]*?(?=")/g, '/tmp/x').replace(/adria/gi, 'user'));
const manual = scrub(rec('manual_actions')).events;
const sim = scrub(rec('mission_simulator')).events;

const mk = (sid, type, node_id, payload, extra = {}) => ({
  event_id: `evt_${sid}_${type}_${node_id}`, session_id: sid, sequence: 0, type, timestamp: '2026-11-15T12:30:00.000000+00:00', node_id, parent_id: null, decision_id: null, payload, ...extra,
});
let seq = 100;
const syn = (type, node_id, payload, extra) => ({ ...mk('syn', type, node_id, payload, extra), sequence: ++seq, timestamp: `2026-11-15T12:30:${String(seq - 100).padStart(2, '0')}.000000+00:00` });

const workflow = [
  syn('session.started', 'root_syn', { agent_name: 'CodingAgent', status: 'Active' }),
  syn('workflow.node_started', 'wf1', { node_id: 'plan', node_name: 'Planificar', agent_name: 'Planner', agent_role: 'plan', iteration: 1, parent_id: null }),
  syn('action.proposed', 'plan_i1_act_1', { tool: 'read_file', operation: '1. read_file', step: 1, arguments: { path: 'a.txt' }, workflow_node: { node_id: 'plan', node_name: 'Planificar', agent_name: 'Planner', agent_role: 'plan', iteration: 1 } }, { parent_id: 'start', decision_id: 'd_w1' }),
  syn('policy.decided', 'plan_i1_act_1_policy', { status: 'ALLOW', requires_confirmation: false, reason_code: 'OK' }, { parent_id: 'plan_i1_act_1' }),
  syn('observation.recorded', 'plan_i1_act_1', { output: 'Tarea concluida: hito del nodo, no cierra la misión' }),
  syn('workflow.node_completed', 'wf1', { node_id: 'plan', node_name: 'Planificar', agent_name: 'Planner', iteration: 1 }),
  syn('workflow.node_started', 'wf2', { node_id: 'code', node_name: 'Programar', agent_name: 'Coder', agent_role: 'code', iteration: 1, parent_id: 'plan_i1_act_1' }),
  syn('workflow.node_started', 'wf2', { node_id: 'code', node_name: 'Programar', agent_name: 'Coder', agent_role: 'code', iteration: 1, parent_id: 'plan_i1_act_1' }),
  syn('action.proposed', 'code_i1_act_1', { tool: 'edit_file', operation: '2. edit_file', step: 2, arguments: {}, workflow_node: { node_id: 'code', node_name: 'Programar', agent_name: 'Coder', agent_role: 'code', iteration: 1 } }, { parent_id: 'plan_i1_act_1' }),
  syn('approval.requested', 'code', { kind: 'node_failure', risk_level: 'HIGH' }),
  syn('intervention.applied', 'code_i1_act_1', { intervention: 'Supervisor', message: 'reintentar', failed_node: 'code_i1_act_1' }),
  syn('workflow.node_started', 'wf2b', { node_id: 'code', node_name: 'Programar', agent_name: 'Coder', agent_role: 'code', iteration: 2, parent_id: 'plan_i1_act_1' }),
  syn('workflow.node_completed', 'wf2b', { node_id: 'code', node_name: 'Programar', agent_name: 'Coder', iteration: 2 }),
  syn('observation.recorded', 'final_act', { output: 'Tarea concluida: todo listo ✔' }),
  syn('session.completed', 'root_syn', { summary: 'Hecho' }),
  syn('some.unknown_event', 'x', 'texto suelto'),
  syn('execution.completed', 'plan_i1_act_1', { success: false, execution_time_ms: 0 }),
  syn('policy.decided', 'orphan_policy', { status: 'REVIEW', requires_confirmation: true, execution_mode: 'full_access' }, { parent_id: 'orphan' }),
];

const tail = [
  syn('observation.recorded', 'a1', { output: 'contenido del archivo leído (no es una conclusión)' }),
  syn('observation.recorded', 'a2', { output: '  Tarea concluida: resumen final' }),
  syn('execution.completed', 'a2', { success: true, execution_time_ms: 42 }),
  syn('session.completed', 'root_rec-manual', { summary: 'Resumen de la misión' }),
];
const dupAction = manual.filter((e) => e.type === 'action.proposed').slice(0, 1);

const streams = [
  { name: 'manual_actions_restricted', mode: 'local_restricted', customModel: '', events: manual },
  { name: 'manual_actions_full_access', mode: 'full_access', customModel: 'mi-modelo', events: manual },
  { name: 'manual_plus_completion', mode: 'container', customModel: '', events: [...manual, ...tail] },
  { name: 'simulator_mission', mode: 'local_restricted', customModel: '', events: sim },
  { name: 'workflow_synthetic', mode: 'local_restricted', customModel: '', events: workflow },
  { name: 'effective_model_and_workspace', mode: 'container', customModel: 'custom', sessionExtra: { decision_model_effective: { provider: 'eff-p', model_id: 'eff-model' }, decisionModel: { provider: 'dm-p', model_id: 'dm-model' }, workspace_root: '/ws/x' }, events: manual.slice(0, 9) },
  { name: 'llm_model_fallback', mode: 'local_restricted', customModel: 'custom', sessionExtra: { llm_model: 'llm-m', working_directory: '/wd' }, events: manual.slice(0, 9) },
  { name: 'conclusion_text_inside_output', mode: 'local_restricted', customModel: '', events: [...manual.slice(0, 3), syn('observation.recorded', 'a1', { output: 'El archivo dice: Tarea concluida: falso' }), syn('observation.recorded', 'a1', { output: 'Tarea concluida: de verdad\ncon dos líneas' })] },
  { name: 'hub_nodes_and_ids', mode: 'local_restricted', customModel: '', events: [syn('session.started', 'root_h', {}), syn('action.proposed', 'h5', { tool: 'plan', operation: '5. plan', step: 5, source: 'LLM (qwen)' }, { parent_id: 'root_h', decision_id: 'd_h5' }), syn('action.proposed', 'h6', { tool: 'propose', operation: '6. ProposeAction', step: 6 }, { parent_id: 'inexistente' }), syn('action.proposed', 'h7', { tool: 'x', operation: 'sin numero', step: 7 }, { parent_id: 'h5' })] },
  { name: 'duplicate_action_and_out_of_order', mode: undefined, customModel: '', events: [...manual.slice(0, 4), ...dupAction, ...manual.slice(4, 8), manual[2]] },
];

const initialFor = (mode, name, extra = {}) => {
  const session = {
    sessionId: 'rec-fixture',
    status: 'Active',
    agent: 'CodingAgent',
    goal: 'objetivo de la prueba',
    ...(mode ? { execution_mode: mode } : {}),
    ...(name === 'manual_actions_restricted' ? { decisionModel: { provider: 'laya', model_id: 'laya-1' }, decision_model_effective: { provider: 'laya', model_id: 'laya-1' } } : {}),
    metrics: { totalDecisions: 0, allowed: 0, blocked: 0, review: 0 },
    runtime: { provider: 'SIMULATOR + LAYA', version: 'v1.1.0', latencyP50: 'N/A', executionTime: 'Live' },
    finalAnswer: null,
    ...extra,
  };
  return {
    session,
    nodes: [{ id: 'start', label: 'Start', type: 'start', status: 'SYSTEM', x: 420, y: 30, parentId: null }],
    decisionsMap: {}, selectedNodeId: 'start', logs: [], events: [], isRunning: true,
  };
};

const fixtures = [];
const golden = {};
for (const s of streams) {
  const initial = initialFor(s.mode, s.name, s.sessionExtra);
  fixtures.push({ name: s.name, initial, customModel: s.customModel, events: s.events });
  const { final, digests } = runOriginal(initial, s.customModel, s.events);
  golden[s.name] = { final, digests };
  console.log(s.name, s.events.length, 'eventos;', final.nodes.length, 'nodos;', Object.keys(final.decisionsMap).length, 'decisiones;', final.session.status);
}
const text = JSON.stringify({ fixtures, golden });
if (/adria|AppData|Users\\\\/i.test(text)) throw new Error('quedan rutas o nombres locales en los fixtures');
mkdirSync(outDir, { recursive: true });
writeFileSync(path.join(outDir, 'sessionStreams.json'), JSON.stringify(fixtures));
writeFileSync(path.join(outDir, 'sessionGolden.json'), JSON.stringify(golden));
console.log('escrito en', outDir, (text.length / 1024).toFixed(0), 'KB');
