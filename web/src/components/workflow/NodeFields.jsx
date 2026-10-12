import React from 'react';
import ConditionBuilder from './ConditionBuilder';
import {
  NODE_TYPE_LABELS,
  TOOL_CHOICES,
  WORK_TYPES,
  describeResult,
  fieldLabelStyle,
  fieldStyle,
  hintStyle,
  panelStyle,
} from './formHelpers';

const titleStyle = { fontSize: '11px', fontWeight: 700, color: '#38bdf8' };

function Field({ label, hint, children }) {
  return (
    <div>
      <label style={fieldLabelStyle}>{label}</label>
      {children}
      {hint && <div style={hintStyle}>{hint}</div>}
    </div>
  );
}

function NodeSelect({ value, onChange, nodes, excludeId, emptyLabel }) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} style={fieldStyle}>
      <option value="">{emptyLabel}</option>
      {nodes
        .filter((n) => (n.node_id || n.id) !== excludeId)
        .map((n) => {
          const id = n.node_id || n.id;
          return (
            <option key={id} value={id}>
              {n.name || id}
            </option>
          );
        })}
    </select>
  );
}

/** Campos editables de un nodo: texto, números y desplegables (sin JSON). */
export default function NodeFields({ form, setForm, nodes, outgoingEdges, agents }) {
  const set = (patch) => setForm({ ...form, ...patch });
  const nt = form.node_type;
  const isWork = WORK_TYPES.includes(nt);
  const nodeName = (id) => nodes.find((n) => (n.node_id || n.id) === id)?.name || id;
  const edgeChoices = outgoingEdges.map((e) => ({
    value: e.label || e.edge_id,
    text: `Ir a «${nodeName(e.to_node)}»${e.label ? ` (${e.label})` : ''}`,
  }));
  const resultText = describeResult(form.last_result);

  return (
    <>
      <Field label="Nombre del nodo">
        <input type="text" required value={form.name} onChange={(e) => set({ name: e.target.value })} style={fieldStyle} />
      </Field>

      <Field label="Qué hace este nodo">
        <select value={nt} onChange={(e) => set({ node_type: e.target.value })} style={fieldStyle}>
          {Object.entries(NODE_TYPE_LABELS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </Field>

      {isWork && (
        <div style={panelStyle}>
          <div style={titleStyle}>Tarea del nodo</div>

          <Field
            label="Misión (qué debe conseguir)"
            hint="Instrucciones para el agente de este nodo. Recibirá además el objetivo general y los resultados de los nodos anteriores."
          >
            <textarea
              rows={4}
              placeholder="p. ej. Implementa la función y añade pruebas unitarias"
              value={form.mission}
              onChange={(e) => set({ mission: e.target.value })}
              style={{ ...fieldStyle, resize: 'vertical' }}
            />
          </Field>

          {!(nt === 'DELEGATE' && form.routing_mode === 'AUTOMATIC') && (
            <Field label="Agente que lo ejecuta">
              <select value={form.agent_id} onChange={(e) => set({ agent_id: e.target.value })} style={fieldStyle}>
                <option value="">{nt === 'TASK' ? 'Ninguno (usa el modelo de la tarea)' : 'Elige un agente…'}</option>
                {agents.map((ag) => (
                  <option key={ag.id} value={ag.id}>
                    {ag.name || ag.id}
                    {ag.role ? ` — ${ag.role}` : ''}
                  </option>
                ))}
                {form.agent_id && !agents.some((a) => a.id === form.agent_id) && (
                  <option value={form.agent_id}>{form.agent_id} (no registrado)</option>
                )}
              </select>
            </Field>
          )}

          {nt === 'TASK' && (
            <Field label="Herramienta fija" hint="Déjalo en «Ninguna» para que el agente decida cómo cumplir la misión.">
              <select value={form.tool_name} onChange={(e) => set({ tool_name: e.target.value })} style={fieldStyle}>
                {TOOL_CHOICES.map((t) => (
                  <option key={t.value} value={t.value}>
                    {t.label}
                  </option>
                ))}
                {form.tool_name && !TOOL_CHOICES.some((t) => t.value === form.tool_name) && (
                  <option value={form.tool_name}>{form.tool_name}</option>
                )}
              </select>
            </Field>
          )}

          <Field
            label="Resultado esperado (opcional)"
            hint="Cómo debe entregar su resultado. Para que el flujo decida (p. ej. un revisor), pide un veredicto: {&quot;approved&quot;: true, &quot;feedback&quot;: &quot;…&quot;}."
          >
            <textarea
              rows={2}
              placeholder="p. ej. El código completo y un resumen de los cambios"
              value={form.expected_output}
              onChange={(e) => set({ expected_output: e.target.value })}
              style={{ ...fieldStyle, resize: 'vertical' }}
            />
          </Field>

          <Field
            label="Máximo de pasos del agente"
            hint={
              form.unlimited_steps
                ? 'Sin límite: el agente trabaja hasta concluir. Se detiene si hay fallos repetidos sin avance o si paras la misión.'
                : 'Límite de acciones que puede hacer en este nodo (por defecto 30, hasta 500).'
            }
          >
            <label style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11.5px', color: '#e2e8f0', marginBottom: '5px', cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={Boolean(form.unlimited_steps)}
                onChange={(e) => set({ unlimited_steps: e.target.checked })}
              />
              Sin límite de pasos
            </label>
            <input
              type="number"
              min="1"
              max="500"
              placeholder="30"
              disabled={Boolean(form.unlimited_steps)}
              value={form.max_steps}
              onChange={(e) => set({ max_steps: e.target.value })}
              style={{ ...fieldStyle, opacity: form.unlimited_steps ? 0.45 : 1 }}
            />
          </Field>

          <div>
            <label style={fieldLabelStyle}>Datos adicionales (opcional)</label>
            {form.param_rows.map((row, idx) => (
              <div key={idx} style={{ display: 'flex', gap: '4px', marginBottom: '4px' }}>
                <input
                  type="text"
                  placeholder="nombre"
                  value={row.key}
                  onChange={(e) => set({ param_rows: form.param_rows.map((r, i) => (i === idx ? { ...r, key: e.target.value } : r)) })}
                  style={{ ...fieldStyle, flex: 1 }}
                />
                <input
                  type="text"
                  placeholder="valor"
                  value={row.value}
                  onChange={(e) => set({ param_rows: form.param_rows.map((r, i) => (i === idx ? { ...r, value: e.target.value } : r)) })}
                  style={{ ...fieldStyle, flex: 1 }}
                />
                <button
                  type="button"
                  title="Quitar"
                  onClick={() => set({ param_rows: form.param_rows.filter((_, i) => i !== idx) })}
                  style={{ background: 'none', border: '1px solid #243044', borderRadius: '5px', color: '#f43f5e', cursor: 'pointer', padding: '0 8px' }}
                >
                  ×
                </button>
              </div>
            ))}
            <button
              type="button"
              onClick={() => set({ param_rows: [...form.param_rows, { key: '', value: '' }] })}
              style={{ background: 'none', border: '1px dashed #334155', borderRadius: '5px', color: '#94a3b8', cursor: 'pointer', fontSize: '11px', padding: '3px 8px' }}
            >
              + Añadir dato
            </button>
          </div>
        </div>
      )}

      {nt === 'DELEGATE' && (
        <div style={panelStyle}>
          <div style={titleStyle}>Reparto entre agentes</div>
          <Field label="Cómo se elige el agente">
            <select value={form.routing_mode} onChange={(e) => set({ routing_mode: e.target.value })} style={fieldStyle}>
              <option value="MANUAL">Siempre el agente indicado arriba</option>
              <option value="AUTOMATIC">Automático: uno de los candidatos</option>
            </select>
          </Field>
          {form.routing_mode === 'AUTOMATIC' && (
            <div>
              <label style={fieldLabelStyle}>Agentes candidatos</label>
              {agents.length === 0 && <div style={hintStyle}>No hay agentes registrados.</div>}
              {agents.map((ag) => (
                <label key={ag.id} style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11.5px', color: '#e2e8f0', marginBottom: '3px' }}>
                  <input
                    type="checkbox"
                    checked={form.candidate_agents.includes(ag.id)}
                    onChange={(e) =>
                      set({
                        candidate_agents: e.target.checked
                          ? [...form.candidate_agents, ag.id]
                          : form.candidate_agents.filter((id) => id !== ag.id),
                      })
                    }
                  />
                  {ag.name || ag.id}
                  {ag.role ? ` — ${ag.role}` : ''}
                </label>
              ))}
            </div>
          )}
        </div>
      )}

      {nt === 'WHILE' && (
        <div style={panelStyle}>
          <div style={titleStyle}>Repetir mientras…</div>
          <ConditionBuilder
            value={form.cond}
            onChange={(cond) => set({ cond })}
            nodes={nodes}
            excludeNodeId={form.node_id}
            lastResultLabel="El nodo anterior"
          />
          <Field label="Primer nodo que se repite">
            <NodeSelect value={form.body_entry} onChange={(v) => set({ body_entry: v })} nodes={nodes} excludeId={form.node_id} emptyLabel="Elige un nodo…" />
          </Field>
          <Field label="Nodo al terminar el bucle">
            <NodeSelect value={form.exit_target} onChange={(v) => set({ exit_target: v })} nodes={nodes} excludeId={form.node_id} emptyLabel="Elige un nodo…" />
          </Field>
          <Field label="Máximo de vueltas" hint="Protege contra bucles infinitos entre agentes.">
            <input type="number" min="1" max="100" value={form.max_iterations} onChange={(e) => set({ max_iterations: e.target.value })} style={fieldStyle} />
          </Field>
          <Field label="Si se alcanza el máximo">
            <select value={form.on_limit} onChange={(e) => set({ on_limit: e.target.value })} style={fieldStyle}>
              <option value="ABORT">Detener el flujo</option>
              <option value="ESCALATE">Escalar a una persona</option>
            </select>
          </Field>
        </div>
      )}

      {(nt === 'IF' || nt === 'DECISION') && (
        <div style={panelStyle}>
          <div style={titleStyle}>Decidir según…</div>
          <ConditionBuilder value={form.cond} onChange={(cond) => set({ cond })} nodes={nodes} excludeNodeId={form.node_id} lastResultLabel="El nodo anterior" />
          {[
            ['true_edge', 'Si se cumple'],
            ['false_edge', 'Si no se cumple'],
            ['default_edge', 'Por defecto (opcional)'],
          ].map(([key, label]) => (
            <Field key={key} label={label}>
              <select value={form[key]} onChange={(e) => set({ [key]: e.target.value })} style={fieldStyle}>
                <option value="">{key === 'default_edge' ? 'Sin camino por defecto' : 'Elige un camino…'}</option>
                {edgeChoices.map((c) => (
                  <option key={c.value} value={c.value}>
                    {c.text}
                  </option>
                ))}
              </select>
            </Field>
          ))}
          {edgeChoices.length === 0 && <div style={hintStyle}>Conecta primero este nodo con los siguientes para poder elegir los caminos.</div>}
        </div>
      )}

      {nt === 'HUMAN_APPROVAL' && (
        <div style={panelStyle}>
          <div style={titleStyle}>Aprobación de una persona</div>
          <Field label="Pregunta que se le hará">
            <textarea rows={3} value={form.prompt} onChange={(e) => set({ prompt: e.target.value })} style={{ ...fieldStyle, resize: 'vertical' }} />
          </Field>
          <Field label="Tiempo máximo de espera (segundos)">
            <input type="number" min="1" value={form.timeout_seconds} onChange={(e) => set({ timeout_seconds: e.target.value })} style={fieldStyle} />
          </Field>
          <Field label="Quién puede aprobar">
            <input type="text" value={form.approver_role} onChange={(e) => set({ approver_role: e.target.value })} style={fieldStyle} />
          </Field>
          <Field label="Si se rechaza, volver a">
            <NodeSelect value={form.reject_target} onChange={(v) => set({ reject_target: v })} nodes={nodes} excludeId={form.node_id} emptyLabel="Detener el flujo" />
          </Field>
        </div>
      )}

      {nt === 'PARALLEL_JOIN' && (
        <div style={panelStyle}>
          <div style={titleStyle}>Unir ramas</div>
          <Field label="Cuándo continuar">
            <select value={form.join_policy} onChange={(e) => set({ join_policy: e.target.value })} style={fieldStyle}>
              <option value="all">Cuando terminen todas las ramas</option>
              <option value="any">Cuando termine la primera</option>
              <option value="quorum">Cuando termine la mayoría</option>
            </select>
          </Field>
          <Field label="Cómo combinar los resultados">
            <select value={form.merge_policy} onChange={(e) => set({ merge_policy: e.target.value })} style={fieldStyle}>
              <option value="namespace">Guardar cada rama por separado</option>
              <option value="shallow">Mezclar los datos en uno solo</option>
              <option value="reduce">Combinar en profundidad</option>
            </select>
          </Field>
        </div>
      )}

      {resultText && (
        <Field label={`Último resultado (${form.status})`}>
          <div
            style={{
              ...fieldStyle,
              backgroundColor: '#111827',
              border: '1px solid #1e293b',
              color: '#94a3b8',
              maxHeight: '140px',
              overflowY: 'auto',
              whiteSpace: 'pre-wrap',
              fontSize: '11px',
            }}
          >
            {resultText}
          </div>
        </Field>
      )}
    </>
  );
}
