import React, { useEffect, useRef, useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { agentColor } from '../utils/agentColor';
import { computeTreeLayout } from '../utils/treeLayout';
import TreeCanvas from './TreeCanvas';

/**
 * Agrupa los pasos del árbol en bloques: uno por cada ejecución de un nodo del workflow (agente + nodo + pasada).
 * Devuelve los bloques en orden de aparición con su estado: trabajando, completado o fallido.
 */
export function buildBlocks(nodes) {
  const byKey = new Map();
  const order = [];
  nodes.forEach((n) => {
    if (n.type === 'start') return;
    const key = n.wfNodeId ? `${n.wfNodeId}#${n.wfIteration || 1}` : 'sesion';
    if (!byKey.has(key)) {
      const block = {
        key,
        wfNodeId: n.wfNodeId || null,
        wfNodeName: n.wfNodeName || n.wfNodeId || 'Misión',
        iteration: n.wfIteration || 1,
        agentName: n.agentName || 'Agente',
        agentRole: n.agentRole || null,
        steps: [],
        done: false,
        failed: false,
        pending: false,
      };
      byKey.set(key, block);
      order.push(block);
    }
    const block = byKey.get(key);
    if (n.isPending) block.pending = true;
    else block.steps.push(n);
    if (n.blockDone) block.done = true;
    if (n.blockFailed) block.failed = true;
  });
  order.forEach((b, i) => {
    b.index = i;
    b.state = b.failed ? 'failed' : b.done ? 'done' : 'running';
  });
  return order;
}

const STATE_LABEL = { running: 'Trabajando', done: 'Completado', failed: 'Interrumpido' };
const STATE_COLOR = { running: 'var(--accent)', done: 'var(--ok)', failed: 'var(--bad)' };

const PAD = 10;
const CARD_W = 184; // tarjetas compactas: caben varias ramas por bloque
const V_STEP = 64;

/**
 * Los pasos de un bloque forman un único árbol conectado. Cuando el supervisor retrocede al inicio del nodo
 * (circuit breaker), el backend engancha el paso siguiente fuera del bloque: aquí se muestra a continuación del
 * último paso correcto en lugar de como un segundo árbol suelto.
 */
export function attachOrphans(steps) {
  const ids = new Set(steps.map((st) => st.id));
  const resolved = [];
  let lastGood = null;
  steps.forEach((st, i) => {
    let parentId = st.parentId;
    if (!ids.has(parentId)) {
      parentId = i === 0 ? null : lastGood || steps[i - 1].id;
    }
    resolved.push({ ...st, parentId });
    if (st.status === 'ALLOW') lastGood = st.id;
  });
  return resolved;
}

// Árbol de pasos de un bloque: mismo aspecto, uniones y animaciones que el árbol clásico, con una raíz virtual oculta
function BlockTree({ steps, selectedNodeId, onSelectNode, latestId }) {
  const wrapRef = useRef(null);
  const [avail, setAvail] = useState(0);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el || typeof ResizeObserver === 'undefined') return undefined;
    const update = () => setAvail(el.clientWidth);
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const layout = React.useMemo(() => {
    const connected = attachOrphans(steps);
    const root = { id: 'start', type: 'start', label: '', status: 'SYSTEM', parentId: null, x: 0, y: 0 };
    const local = [root, ...connected.map((st) => ({ ...st, parentId: st.parentId || 'start' }))];
    const laid = computeTreeLayout(local, { nodeWidth: CARD_W, horizontalGap: 16, verticalStep: V_STEP });
    const real = laid.filter((n) => n.id !== 'start');
    const minX = Math.min(...real.map((n) => n.x));
    const maxX = Math.max(...real.map((n) => n.x));
    const minY = Math.min(...real.map((n) => n.y));
    const maxY = Math.max(...real.map((n) => n.y));
    return {
      nodes: laid.map((n) => ({ ...n, x: n.x - minX + PAD, y: n.y - minY + PAD })),
      width: maxX - minX + CARD_W + PAD * 2,
      height: maxY - minY + 44 + PAD * 2,
    };
  }, [steps]);

  // Si el árbol es más ancho que el bloque, se escala para que quepa entero (sin scroll lateral)
  const scale = avail > 0 && layout.width > avail ? Math.max(0.5, avail / layout.width) : 1;

  return (
    <div ref={wrapRef} style={{ paddingTop: 6, overflowX: scale < 1 ? 'hidden' : 'auto' }}>
      <div style={{ width: layout.width * scale, height: layout.height * scale, margin: '0 auto' }}>
        <div style={{ position: 'relative', width: layout.width, height: layout.height, transform: `scale(${scale})`, transformOrigin: '0 0' }}>
          <TreeCanvas
            nodes={layout.nodes}
            hiddenIds={['start']}
            latestId={latestId}
            compact
            cardWidth={CARD_W}
            selectedNodeId={selectedNodeId}
            onSelectNode={onSelectNode}
          />
        </div>
      </div>
    </div>
  );
}

export default function WorkflowBlocks({ blocks, isOpen, onToggle, selectedNodeId, onSelectNode, autoScroll, zoom = 1 }) {
  const scrollRef = useRef(null);
  const stepCount = blocks.reduce((sum, b) => sum + b.steps.length, 0);
  const latestId = [...blocks].reverse().find((b) => b.steps.length > 0)?.steps.slice(-1)[0]?.id;

  useEffect(() => {
    if (!autoScroll || !scrollRef.current) return;
    const el = scrollRef.current;
    el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
  }, [blocks.length, stepCount, autoScroll]);

  return (
    <div ref={scrollRef} style={{ position: 'absolute', inset: 0, overflowY: 'auto', overflowX: 'hidden', padding: '16px 16px 40px 72px' }}>
      <div style={{ zoom, maxWidth: '860px', margin: '0 auto' }}>
        {blocks.map((b, i) => {
          const color = agentColor(b.agentName);
          const open = isOpen(b);
          const prev = i > 0 ? blocks[i - 1] : null;
          const stateColor = STATE_COLOR[b.state];
          const sameNode = prev && prev.wfNodeId === b.wfNodeId;
          const bridgeLabel = !prev
            ? null
            : sameNode
              ? prev.state === 'failed'
                ? `Reintento de ${b.wfNodeName}`
                : `Nueva pasada · ${b.wfNodeName}`
              : `Pasa a ${b.agentName}`;
          const blocked = b.steps.filter((s) => s.status === 'BLOCK' || s.status === 'REPLAN').length;
          return (
            <div key={b.key}>
              {prev && (
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', margin: '0 0 0 0' }}>
                  <div style={{ width: 0, height: 14, borderLeft: '1px dashed var(--line)' }} />
                  <span style={{ fontSize: '11px', fontWeight: 600, color: 'var(--muted)', border: '1px solid var(--line)', borderRadius: 14, padding: '2px 10px', backgroundColor: 'var(--bg)' }}>{bridgeLabel}</span>
                  <div style={{ width: 0, height: 14, borderLeft: '1px dashed var(--line)' }} />
                </div>
              )}
              <div
                style={{
                  borderRadius: '9px',
                  border: `1px solid ${b.state === 'running' ? 'var(--accent)' : 'var(--line)'}`,
                  backgroundColor: 'var(--surface)',
                  overflow: 'hidden',
                }}
              >
                <div
                  onClick={() => onToggle(b)}
                  style={{ display: 'flex', alignItems: 'center', gap: '10px', padding: '10px 12px', cursor: 'pointer', userSelect: 'none' }}
                >
                  {open ? <ChevronDown size={14} style={{ color: 'var(--muted)' }} /> : <ChevronRight size={14} style={{ color: 'var(--muted)' }} />}
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div style={{ fontSize: '12.5px', fontWeight: 700, color, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {b.agentName}
                      {b.agentRole ? <span style={{ fontWeight: 400, color: 'var(--muted)' }}> · {b.agentRole}</span> : null}
                    </div>
                    <div style={{ fontSize: '11px', color: 'var(--text)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {b.wfNodeName}
                      {b.iteration > 1 ? ` · pasada ${b.iteration}` : ''}
                    </div>
                  </div>
                  <div style={{ textAlign: 'right', flexShrink: 0 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px', justifyContent: 'flex-end', fontSize: '11px', fontWeight: 600, color: stateColor }}>
                      {b.state === 'running' && <span className="pulse-dot" style={{ backgroundColor: stateColor }} />}
                      {STATE_LABEL[b.state]}
                    </div>
                    <div style={{ fontSize: '11px', color: 'var(--muted)' }}>
                      {b.steps.length} {b.steps.length === 1 ? 'paso' : 'pasos'}
                      {blocked > 0 ? ` · ${blocked} vetados` : ''}
                    </div>
                  </div>
                </div>
                {open && (
                  <div style={{ padding: '2px 12px 12px 12px', borderTop: '1px solid var(--line)' }}>
                    {b.steps.length === 0 ? (
                      <div style={{ fontSize: '11px', color: 'var(--muted)', padding: '8px 2px' }}>Esperando la primera acción del agente…</div>
                    ) : (
                      <BlockTree steps={b.steps} selectedNodeId={selectedNodeId} onSelectNode={onSelectNode} latestId={latestId} />
                    )}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
