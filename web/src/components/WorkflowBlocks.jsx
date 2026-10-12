import React, { useEffect, useRef } from 'react';
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
const STATE_COLOR = { running: '#38bdf8', done: '#34d399', failed: '#f87171' };

const PAD = 12;

// Árbol de pasos de un bloque: mismo aspecto, uniones y animaciones que el árbol clásico, con una raíz virtual oculta
function BlockTree({ steps, selectedNodeId, onSelectNode, latestId }) {
  const layout = React.useMemo(() => {
    const ids = new Set(steps.map((st) => st.id));
    const root = { id: 'start', type: 'start', label: '', status: 'SYSTEM', parentId: null, x: 0, y: 0 };
    const local = [root, ...steps.map((st) => ({ ...st, parentId: ids.has(st.parentId) ? st.parentId : 'start' }))];
    const laid = computeTreeLayout(local);
    const real = laid.filter((n) => n.id !== 'start');
    const minX = Math.min(...real.map((n) => n.x));
    const maxX = Math.max(...real.map((n) => n.x));
    const minY = Math.min(...real.map((n) => n.y));
    const maxY = Math.max(...real.map((n) => n.y));
    return {
      nodes: laid.map((n) => ({ ...n, x: n.x - minX + PAD, y: n.y - minY + PAD })),
      width: maxX - minX + 205 + PAD * 2,
      height: maxY - minY + 44 + PAD * 2,
    };
  }, [steps]);

  return (
    <div style={{ overflowX: 'auto', paddingTop: 6 }}>
      <div style={{ position: 'relative', width: layout.width, height: layout.height, margin: '0 auto' }}>
        <TreeCanvas
          nodes={layout.nodes}
          hiddenIds={['start']}
          latestId={latestId}
          compact
          selectedNodeId={selectedNodeId}
          onSelectNode={onSelectNode}
        />
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
      <div style={{ zoom, maxWidth: '640px', margin: '0 auto' }}>
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
                  <div style={{ width: 0, height: 14, borderLeft: `2px dashed ${color}` }} />
                  <span style={{ fontSize: '10.5px', fontWeight: 600, color, border: `1px solid ${color}`, borderRadius: 9999, padding: '2px 10px', backgroundColor: '#0a0e16' }}>{bridgeLabel}</span>
                  <div style={{ width: 0, height: 14, borderLeft: `2px dashed ${color}` }} />
                </div>
              )}
              <div
                style={{
                  borderRadius: '10px',
                  border: `1px solid ${b.state === 'running' ? color : '#1e293b'}`,
                  borderLeft: `5px solid ${color}`,
                  backgroundColor: '#111826',
                  boxShadow: b.state === 'running' ? `0 0 14px ${color}33` : 'none',
                  overflow: 'hidden',
                }}
              >
                <div
                  onClick={() => onToggle(b)}
                  style={{ display: 'flex', alignItems: 'center', gap: '10px', padding: '10px 12px', cursor: 'pointer', userSelect: 'none' }}
                >
                  {open ? <ChevronDown size={14} style={{ color: '#8b949e' }} /> : <ChevronRight size={14} style={{ color: '#8b949e' }} />}
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div style={{ fontSize: '12.5px', fontWeight: 700, color, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {b.agentName}
                      {b.agentRole ? <span style={{ fontWeight: 400, color: '#8b949e' }}> · {b.agentRole}</span> : null}
                    </div>
                    <div style={{ fontSize: '11px', color: '#94a3b8', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {b.wfNodeName}
                      {b.iteration > 1 ? ` · pasada ${b.iteration}` : ''}
                    </div>
                  </div>
                  <div style={{ textAlign: 'right', flexShrink: 0 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px', justifyContent: 'flex-end', fontSize: '11px', fontWeight: 600, color: stateColor }}>
                      {b.state === 'running' && <span className="pulse-dot" style={{ backgroundColor: stateColor }} />}
                      {STATE_LABEL[b.state]}
                    </div>
                    <div style={{ fontSize: '10px', color: '#64748b' }}>
                      {b.steps.length} {b.steps.length === 1 ? 'paso' : 'pasos'}
                      {blocked > 0 ? ` · ${blocked} vetados` : ''}
                    </div>
                  </div>
                </div>
                {open && (
                  <div style={{ padding: '2px 12px 12px 12px', borderTop: '1px solid #1b2638' }}>
                    {b.steps.length === 0 ? (
                      <div style={{ fontSize: '11px', color: '#64748b', padding: '8px 2px' }}>Esperando la primera acción del agente…</div>
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
