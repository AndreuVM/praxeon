import React from 'react';
import {
  LAST_RESULT,
  OPERATORS,
  RAW,
  VARIABLES,
  fieldLabelStyle,
  fieldStyle,
  hintStyle,
  operatorNeedsValue,
} from './formHelpers';

/**
 * Editor de una condición en lenguaje natural:
 *   "Mirar [el resultado de <nodo>] · campo [approved] · [es igual a] · [valor]"
 * `value` = { source, key, operator, valueText }; `nodes` = nodos del workflow (para elegir el origen).
 */
export default function ConditionBuilder({ value, onChange, nodes = [], excludeNodeId = null, lastResultLabel }) {
  const set = (patch) => onChange({ ...value, ...patch });
  const needsValue = operatorNeedsValue(value.operator);
  const candidates = nodes.filter((n) => (n.node_id || n.id) !== excludeNodeId);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
      <div>
        <label style={fieldLabelStyle}>Mirar el resultado de</label>
        <select value={value.source} onChange={(e) => set({ source: e.target.value })} style={fieldStyle}>
          <option value={LAST_RESULT}>{lastResultLabel || 'El último nodo ejecutado'}</option>
          {candidates.map((n) => {
            const id = n.node_id || n.id;
            return (
              <option key={id} value={id}>
                {n.name || id}
              </option>
            );
          })}
          <option value={VARIABLES}>Una variable global del flujo</option>
          {value.source === RAW && <option value={RAW}>Ruta avanzada (conservada)</option>}
        </select>
      </div>

      <div>
        <label style={fieldLabelStyle}>Dato a comprobar</label>
        <input
          type="text"
          placeholder="p. ej. approved, status, score"
          value={value.key}
          onChange={(e) => set({ key: e.target.value })}
          style={fieldStyle}
        />
        <div style={hintStyle}>
          Nombre del dato del resultado. Si el agente termina con {'{"approved": true}'}, el dato es «approved».
        </div>
      </div>

      <div>
        <label style={fieldLabelStyle}>Condición</label>
        <select value={value.operator} onChange={(e) => set({ operator: e.target.value })} style={fieldStyle}>
          {OPERATORS.map((op) => (
            <option key={op.value} value={op.value}>
              {op.label}
            </option>
          ))}
        </select>
      </div>

      {needsValue && (
        <div>
          <label style={fieldLabelStyle}>Valor</label>
          <input
            type="text"
            placeholder={value.operator === 'in' ? 'aprobado, revisado' : 'p. ej. true, 3, OK'}
            value={value.valueText}
            onChange={(e) => set({ valueText: e.target.value })}
            style={fieldStyle}
          />
        </div>
      )}
    </div>
  );
}
