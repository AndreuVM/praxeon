// Conversión entre el modelo del workflow (JSON del backend) y formularios pensados para personas.
// Nada de lo que ve el usuario es JSON: son campos de texto, números y desplegables.

export const WORK_TYPES = ['AGENT', 'TASK', 'DELEGATE'];

export const NODE_TYPE_LABELS = {
  START: 'Inicio',
  END: 'Fin',
  AGENT: 'Agente (trabaja una misión)',
  TASK: 'Tarea (herramienta o agente)',
  DELEGATE: 'Delegar en un agente',
  DECISION: 'Decisión (según un resultado)',
  IF: 'Si… entonces…',
  WHILE: 'Repetir mientras se cumpla',
  HUMAN_APPROVAL: 'Aprobación humana',
  PARALLEL_FORK: 'Dividir en ramas paralelas',
  PARALLEL_JOIN: 'Unir ramas paralelas',
};

export const OPERATORS = [
  { value: '==', label: 'es igual a', needsValue: true },
  { value: '!=', label: 'es distinto de', needsValue: true },
  { value: '>', label: 'es mayor que', needsValue: true },
  { value: '>=', label: 'es mayor o igual que', needsValue: true },
  { value: '<', label: 'es menor que', needsValue: true },
  { value: '<=', label: 'es menor o igual que', needsValue: true },
  { value: 'contains', label: 'contiene', needsValue: true },
  { value: 'in', label: 'es uno de (separados por comas)', needsValue: true },
  { value: 'is_true', label: 'es verdadero (sí)', needsValue: false },
  { value: 'is_false', label: 'es falso (no)', needsValue: false },
  { value: 'is_null', label: 'está vacío', needsValue: false },
  { value: 'not_null', label: 'tiene algún valor', needsValue: false },
];

// Herramientas fijas que puede ejecutar un nodo de tarea. Vacío = lo resuelve un agente con su misión.
export const TOOL_CHOICES = [
  { value: '', label: 'Ninguna: lo resuelve un agente según la misión' },
  { value: 'run_command', label: 'Ejecutar un comando' },
  { value: 'read_file', label: 'Leer un archivo' },
];

export const LAST_RESULT = '@last';
export const VARIABLES = '@variables';
export const RAW = '@raw';

const MISSION_KEYS = ['mission', 'instruction', 'task', 'prompt'];

export function operatorNeedsValue(operator) {
  const op = OPERATORS.find((o) => o.value === operator);
  return op ? op.needsValue : true;
}

// ---------------------------------------------------------------- condiciones
export function buildFieldPath(source, key) {
  const k = (key || '').trim();
  if (source === LAST_RESULT) return `output.${k}`;
  if (source === VARIABLES) return `variables.${k}`;
  if (source === RAW) return k;
  return `outputs.${source}.${k}`;
}

export function parseFieldPath(field, nodeIds = []) {
  const f = (field || '').trim();
  if (!f) return { source: LAST_RESULT, key: '' };
  if (f.startsWith('output.')) return { source: LAST_RESULT, key: f.slice('output.'.length) };
  if (f.startsWith('variables.')) return { source: VARIABLES, key: f.slice('variables.'.length) };
  if (f.startsWith('outputs.')) {
    const [, nodeId, ...rest] = f.split('.');
    if (nodeIds.includes(nodeId)) return { source: nodeId, key: rest.join('.') };
  }
  return { source: RAW, key: f };
}

export function valueToText(value) {
  if (value === undefined || value === null) return '';
  if (Array.isArray(value)) return value.map((v) => String(v)).join(', ');
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function coerceScalar(text) {
  const t = String(text).trim();
  if (t === 'true') return true;
  if (t === 'false') return false;
  if (t !== '' && !Number.isNaN(Number(t))) return Number(t);
  return t;
}

export function textToValue(text, operator) {
  if (!operatorNeedsValue(operator)) return null;
  if (operator === 'in') {
    return String(text || '')
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean)
      .map(coerceScalar);
  }
  return coerceScalar(text ?? '');
}

export function emptyCondition() {
  return { source: LAST_RESULT, key: '', operator: '==', valueText: '' };
}

export function conditionToForm(condition, nodeIds = []) {
  if (!condition) return emptyCondition();
  const field = typeof condition === 'string' ? condition : condition.field;
  const operator = (typeof condition === 'object' && (condition.operator || condition.op)) || (typeof condition === 'string' ? 'is_true' : '==');
  const raw = typeof condition === 'object' ? (condition.expected_value !== undefined ? condition.expected_value : condition.value) : undefined;
  return { ...parseFieldPath(field, nodeIds), operator, valueText: valueToText(raw) };
}

// Devuelve null si el formulario no define condición (campo vacío)
export function formToCondition(form) {
  if (!form || !(form.key || '').trim()) return null;
  return {
    field: buildFieldPath(form.source, form.key),
    operator: form.operator || '==',
    expected_value: textToValue(form.valueText, form.operator),
  };
}

// ---------------------------------------------------------------- parámetros (pares nombre / valor)
export function inputsToRows(inputs = {}) {
  const rows = [];
  const preserved = {};
  Object.entries(inputs).forEach(([key, value]) => {
    if (MISSION_KEYS.includes(key)) return;
    if (value !== null && typeof value === 'object') {
      preserved[key] = value; // estructuras complejas: se conservan sin exponerlas como JSON
    } else {
      rows.push({ key, value: value === undefined || value === null ? '' : String(value) });
    }
  });
  return { rows, preserved };
}

export function rowsToInputs(rows = [], preserved = {}) {
  const out = { ...preserved };
  rows.forEach(({ key, value }) => {
    const k = (key || '').trim();
    if (k) out[k] = coerceScalar(value ?? '');
  });
  return out;
}

// ---------------------------------------------------------------- nodo <-> formulario
export function nodeToForm(node, nodeIds = []) {
  const cfg = node.control_config || {};
  const meta = node.metadata || {};
  const inputs = node.inputs || {};
  let mission = typeof meta.mission === 'string' ? meta.mission : '';
  if (!mission) {
    const k = MISSION_KEYS.find((key) => typeof inputs[key] === 'string' && inputs[key].trim());
    mission = k ? inputs[k] : '';
  }
  const { rows, preserved } = inputsToRows(inputs);
  const routing = String(cfg.routing_mode || cfg.strategy || 'MANUAL').toUpperCase();
  return {
    node_id: node.node_id || node.id,
    name: node.name || '',
    node_type: node.node_type || 'TASK',
    status: node.status || 'PENDING',
    agent_id: node.agent_id || '',
    tool_name: node.tool_name || '',
    mission,
    expected_output: typeof meta.expected_output === 'string' ? meta.expected_output : '',
    max_steps: meta.max_steps === 0 ? '' : meta.max_steps ?? '',
    unlimited_steps: meta.max_steps === 0,
    param_rows: rows,
    preserved_inputs: preserved,
    raw_inputs: inputs,
    raw_metadata: meta,
    raw_control_config: cfg,
    last_result: node.outputs || {},
    // control de flujo
    cond: conditionToForm(cfg.condition, nodeIds),
    max_iterations: cfg.max_iterations ?? 10,
    on_limit: cfg.on_limit || 'ABORT',
    body_entry: cfg.body_entry || '',
    exit_target: cfg.exit_target || '',
    true_edge: cfg.true_edge || '',
    false_edge: cfg.false_edge || '',
    default_edge: cfg.default_edge || '',
    prompt: cfg.prompt || 'Se requiere aprobación de un supervisor humano.',
    timeout_seconds: cfg.timeout_seconds || 3600,
    approver_role: cfg.approver_role || 'supervisor',
    reject_target: cfg.reject_target || '',
    routing_mode: routing === 'AUTOMATIC' ? 'AUTOMATIC' : 'MANUAL',
    candidate_agents: Array.isArray(cfg.candidate_agents) ? cfg.candidate_agents : [],
    join_policy: cfg.join_policy || 'all',
    merge_policy: cfg.merge_policy || 'namespace',
  };
}

export function formToNodeUpdate(form) {
  const nt = form.node_type;
  const isWork = WORK_TYPES.includes(nt);
  const base = { ...(form.raw_control_config || {}) };
  let controlConfig = null;
  const cond = formToCondition(form.cond);

  if (nt === 'WHILE') {
    controlConfig = {
      ...base,
      max_iterations: Number(form.max_iterations) || 10,
      on_limit: form.on_limit || 'ABORT',
      ...(form.body_entry ? { body_entry: form.body_entry } : {}),
      ...(form.exit_target ? { exit_target: form.exit_target } : {}),
      ...(cond ? { condition: cond } : {}),
    };
  } else if (nt === 'IF' || nt === 'DECISION') {
    controlConfig = {
      ...base,
      ...(form.true_edge ? { true_edge: form.true_edge } : {}),
      ...(form.false_edge ? { false_edge: form.false_edge } : {}),
      ...(form.default_edge ? { default_edge: form.default_edge } : {}),
      ...(cond ? { condition: cond } : {}),
    };
  } else if (nt === 'HUMAN_APPROVAL') {
    controlConfig = {
      ...base,
      prompt: (form.prompt || '').trim() || 'Aprobación requerida para continuar.',
      timeout_seconds: Number(form.timeout_seconds) || 3600,
      approver_role: (form.approver_role || '').trim() || 'supervisor',
      ...(form.reject_target ? { reject_target: form.reject_target } : {}),
    };
  } else if (nt === 'DELEGATE') {
    controlConfig = {
      ...base,
      routing_mode: form.routing_mode,
      strategy: form.routing_mode,
      candidate_agents: form.candidate_agents,
    };
  } else if (nt === 'PARALLEL_JOIN') {
    controlConfig = { ...base, join_policy: form.join_policy || 'all', merge_policy: form.merge_policy || 'namespace' };
  }

  const metadata = { ...(form.raw_metadata || {}) };
  ['mission', 'expected_output', 'max_steps'].forEach((k) => delete metadata[k]);
  if (isWork) {
    if ((form.mission || '').trim()) metadata.mission = form.mission.trim();
    if ((form.expected_output || '').trim()) metadata.expected_output = form.expected_output.trim();
    // 0 = sin límite: el agente solo se detiene al concluir, por un fallo irrecuperable o si lo paras tú
    if (form.unlimited_steps) metadata.max_steps = 0;
    else {
      const steps = Number(form.max_steps);
      if (steps > 0) metadata.max_steps = Math.min(Math.floor(steps), 500);
    }
  }

  return {
    name: (form.name || '').trim(),
    node_type: nt,
    // '' borra el valor; null lo dejaría sin cambios en el backend
    agent_id: isWork && !(nt === 'DELEGATE' && form.routing_mode === 'AUTOMATIC') ? form.agent_id || '' : '',
    tool_name: nt === 'TASK' ? form.tool_name || '' : '',
    inputs: isWork ? rowsToInputs(form.param_rows, form.preserved_inputs) : form.raw_inputs || {},
    metadata,
    ...(controlConfig ? { control_config: controlConfig } : {}),
  };
}

// Texto legible del último resultado de un nodo (sin mostrar JSON crudo)
export function describeResult(result = {}) {
  if (!result || Object.keys(result).length === 0) return '';
  const lines = [];
  if (typeof result.response === 'string' && result.response) lines.push(result.response);
  Object.entries(result).forEach(([k, v]) => {
    if (k === 'response' || v === null || typeof v === 'object') return;
    lines.push(`${k}: ${v}`);
  });
  return lines.join('\n');
}

export const fieldLabelStyle = { fontSize: '10.5px', color: '#94a3b8', display: 'block', marginBottom: '3px' };
export const fieldStyle = {
  width: '100%',
  backgroundColor: '#161c28',
  border: '1px solid #243044',
  borderRadius: '5px',
  padding: '5px 8px',
  fontSize: '11.5px',
  color: '#f8fafc',
};
export const hintStyle = { fontSize: '10px', color: '#64748b', marginTop: '3px', lineHeight: 1.35 };
export const panelStyle = {
  backgroundColor: '#0d1520',
  border: '1px solid #1e2b3f',
  borderRadius: '6px',
  padding: '10px',
  display: 'flex',
  flexDirection: 'column',
  gap: '8px',
};
