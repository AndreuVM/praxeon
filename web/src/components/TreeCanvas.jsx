import React from 'react';
import { AlertTriangle, Check, Clock, Play, RefreshCw, RotateCcw, Sparkles, X } from 'lucide-react';
import { agentColor } from '../utils/agentColor';

// Árbol de nodos con sus uniones animadas. Lo usa el lienzo clásico y, dentro de cada bloque de agente,
// el árbol de pasos. Los nodos llegan con su posición (x, y) ya calculada por computeTreeLayout.

export const cardHeight = (node, compact = false) => (node.agentName && !compact ? 58 : 44);

const STATUS_TEXT = {
  BLOCK: 'vetado',
  REPLAN: 'replanteado',
  REVIEW: 'requiere revisión',
  EXECUTING: 'ejecutando…',
  PENDING: 'pendiente',
};

export function getNodeVisuals(node, isSelected) {
  let icon = Check;
  let iconBg = 'color-mix(in srgb, var(--ok) 15%, transparent)';
  let iconBorder = 'color-mix(in srgb, var(--ok) 35%, transparent)';
  let iconColor = 'var(--ok)';
  let borderColor = 'var(--line)';
  let bg = 'var(--surface)';

  if (node.type === 'start') {
    icon = Play;
    iconBg = 'var(--line)';
    iconBorder = 'var(--line)';
    iconColor = 'var(--text)';
    bg = 'var(--line)';
  } else if (node.status === 'PROPOSE' || node.type === 'hub') {
    icon = Sparkles;
    iconBg = 'color-mix(in srgb, var(--accent) 20%, transparent)';
    iconBorder = 'color-mix(in srgb, var(--accent) 40%, transparent)';
    iconColor = 'var(--muted)';
    borderColor = 'color-mix(in srgb, var(--accent) 40%, transparent)';
    bg = 'var(--line)';
  } else if (node.status === 'BLOCK') {
    icon = X;
    iconBg = 'color-mix(in srgb, var(--bad) 18%, transparent)';
    iconBorder = 'color-mix(in srgb, var(--bad) 35%, transparent)';
    iconColor = 'var(--bad)';
    borderColor = 'color-mix(in srgb, var(--bad) 28%, transparent)';
    bg = 'var(--surface)';
  } else if (node.status === 'REVIEW') {
    icon = AlertTriangle;
    iconBg = 'color-mix(in srgb, var(--accent) 18%, transparent)';
    iconBorder = 'color-mix(in srgb, var(--accent) 35%, transparent)';
    iconColor = 'var(--accent)';
    borderColor = 'color-mix(in srgb, var(--accent) 28%, transparent)';
    bg = 'var(--surface)';
  } else if (node.status === 'REPLAN') {
    icon = RotateCcw;
    iconBg = 'color-mix(in srgb, var(--muted) 20%, transparent)';
    iconBorder = 'color-mix(in srgb, var(--muted) 40%, transparent)';
    iconColor = 'var(--muted)';
    borderColor = 'color-mix(in srgb, var(--muted) 35%, transparent)';
    bg = 'var(--surface)';
  } else if (node.status === 'PENDING') {
    icon = Clock;
    iconBg = 'color-mix(in srgb, var(--muted) 20%, transparent)';
    iconBorder = 'color-mix(in srgb, var(--muted) 35%, transparent)';
    iconColor = 'var(--muted)';
    borderColor = 'var(--line)';
    bg = 'var(--surface)';
  } else if (node.status === 'EXECUTING') {
    icon = RefreshCw;
    iconBg = 'color-mix(in srgb, var(--accent) 20%, transparent)';
    iconBorder = 'color-mix(in srgb, var(--accent) 45%, transparent)';
    iconColor = 'var(--accent)';
    borderColor = 'color-mix(in srgb, var(--accent) 45%, transparent)';
    bg = 'var(--surface)';
  }

  if (isSelected) {
    borderColor = 'var(--line)';
    bg = 'var(--line)';
  }

  return { icon, iconBg, iconBorder, iconColor, borderColor, bg };
}

function edgeColors(node) {
  let strokeColor = 'var(--line)';
  let particleColor = 'var(--accent)';
  if (node.status === 'ALLOW') {
    strokeColor = 'color-mix(in srgb, var(--ok) 70%, transparent)';
    particleColor = 'var(--ok)';
  } else if (node.status === 'PROPOSE' || node.status === 'REPLAN') {
    strokeColor = 'color-mix(in srgb, var(--muted) 75%, transparent)';
    particleColor = 'var(--muted)';
  } else if (node.status === 'REVIEW') {
    strokeColor = 'color-mix(in srgb, var(--accent) 75%, transparent)';
    particleColor = 'var(--accent)';
  } else if (node.status === 'BLOCK') {
    strokeColor = 'color-mix(in srgb, var(--bad) 70%, transparent)';
    particleColor = 'var(--bad)';
  } else if (node.status === 'EXECUTING') {
    strokeColor = 'color-mix(in srgb, var(--accent) 85%, transparent)';
    particleColor = 'var(--accent)';
  }
  return { strokeColor, particleColor };
}

function Edges({ nodes, nodeMap, hidden, compact, cardWidth }) {
  return nodes.map((node) => {
    if (hidden.has(node.id) || !node.parentId || !nodeMap[node.parentId] || hidden.has(node.parentId)) return null;
    const parent = nodeMap[node.parentId];

    const startX = parent.x + (parent.type === 'start' ? 40 : cardWidth / 2);
    const startY = parent.y + (parent.type === 'start' ? 28 : cardHeight(parent, compact) - 2);
    const endX = node.x + (node.type === 'start' ? 40 : cardWidth / 2);
    const endY = node.y;
    const midY = (startY + endY) / 2;
    const pathData = `M ${startX} ${startY} C ${startX} ${midY}, ${endX} ${midY}, ${endX} ${endY}`;

    let { strokeColor, particleColor } = edgeColors(node);
    const handoffColor = !compact && node.handoff ? agentColor(node.agentName) : null;
    if (handoffColor) {
      strokeColor = handoffColor;
      particleColor = handoffColor;
    }
    const pill = handoffColor ? `Pasa a ${node.agentName}` : null;
    const pillW = pill ? 14 + pill.length * 5.9 : 0;
    const midX = (startX + endX) / 2;

    return (
      <g key={`edge-${node.parentId}-${node.id}`}>
        {/* Trazado principal animado al unirse al grafo */}
        <path
          className="edge-path-animated"
          d={pathData}
          fill="none"
          stroke={strokeColor}
          strokeWidth="2"
          strokeDasharray={node.status === 'REVIEW' ? '4 3' : handoffColor ? '7 4' : 'none'}
        />
        {/* Partícula en movimiento continuo indicando flujo de datos en tiempo real */}
        <circle r="2.5" fill={particleColor} opacity="0.95">
          <animateMotion dur="2.2s" repeatCount="indefinite" path={pathData} />
        </circle>
        {/* Punta de flecha */}
        <polygon points={`${endX},${endY} ${endX - 4},${endY - 6} ${endX + 4},${endY - 6}`} fill={strokeColor} />
        {pill && (
          <g>
            <rect x={midX - pillW / 2} y={midY - 9} width={pillW} height="18" rx="9" fill="var(--bg)" stroke={handoffColor} strokeWidth="1.2" />
            <text x={midX} y={midY + 3.5} textAnchor="middle" fontSize="10" fontWeight="600" fill={handoffColor}>
              {pill}
            </text>
          </g>
        )}
      </g>
    );
  });
}

function NodeCard({ node, isSelected, isLatest, onSelectNode, compact, cardWidth }) {
  const visuals = getNodeVisuals(node, isSelected);
  const Icon = visuals.icon;
  const isExecuting = node.status === 'EXECUTING';

  if (node.type === 'start') {
    return (
      <div
        className="tree-node"
        onClick={() => onSelectNode?.(node.id)}
        style={{
          position: 'absolute',
          left: `${node.x}px`,
          top: `${node.y}px`,
          width: '80px',
          height: '28px',
          borderRadius: '9999px',
          backgroundColor: visuals.bg,
          border: `1px solid ${visuals.borderColor}`,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          gap: '6px',
          color: 'var(--text)',
          fontSize: '11px',
          fontWeight: '600',
          cursor: 'pointer',
          userSelect: 'none',
          boxShadow: isSelected ? 'none' : 'none',
          transition: 'all 0.25s ease',
        }}
      >
        <Play size={11} style={{ fill: 'var(--text)' }} />
        <span>Start</span>
      </div>
    );
  }

  const showAgent = Boolean(node.agentName) && !compact;
  const detail = compact ? STATUS_TEXT[node.status] || '' : node.subtitle;

  return (
    <div
      className={`tree-node ${node.isNew ? 'tree-node-enter' : ''} ${isLatest || isExecuting ? 'tree-node-active-pulse' : ''}`}
      onClick={(e) => {
        e.stopPropagation();
        onSelectNode?.(node.id);
      }}
      style={{
        position: 'absolute',
        left: `${node.x}px`,
        top: `${node.y}px`,
        width: `${cardWidth}px`,
        height: `${cardHeight(node, compact)}px`,
        borderRadius: '9px',
        backgroundColor: visuals.bg,
        border: `1px solid ${visuals.borderColor}`,
        borderLeft: showAgent ? `4px solid ${agentColor(node.agentName)}` : `1px solid ${visuals.borderColor}`,
        display: 'flex',
        alignItems: 'center',
        padding: '0 10px',
        gap: '9px',
        cursor: 'pointer',
        userSelect: 'none',
        transition: 'all 0.22s ease',
        boxShadow: isSelected
          ? 'none'
          : isLatest
            ? 'none'
            : 'none',
      }}
    >
      <div
        style={{
          width: '24px',
          height: '24px',
          borderRadius: '50%',
          backgroundColor: visuals.iconBg,
          border: `1px solid ${visuals.iconBorder}`,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: visuals.iconColor,
          flexShrink: 0,
        }}
      >
        <Icon size={12} strokeWidth={2.4} style={{ animation: isExecuting ? 'softPulse 1s infinite' : 'none' }} />
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <span
          style={{ fontSize: '12.5px', fontWeight: '600', color: isSelected ? 'var(--text)' : 'var(--text)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}
        >
          {node.label}
        </span>
        {showAgent && (
          <span
            title={`${node.agentName}${node.agentRole ? ` — ${node.agentRole}` : ''}`}
            style={{ fontSize: '11px', fontWeight: 700, color: agentColor(node.agentName), whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}
          >
            {node.agentName}
            {node.agentRole ? <span style={{ fontWeight: 400, color: 'var(--muted)' }}> · {node.agentRole}</span> : null}
          </span>
        )}
        {detail ? (
          <span style={{ fontSize: '11px', color: 'var(--muted)', marginTop: '1px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{detail}</span>
        ) : null}
      </div>
    </div>
  );
}

/**
 * Dibuja `nodes` (con x, y) y las uniones animadas entre ellos.
 *  - hiddenIds: nodos que no se dibujan (p. ej. la raíz virtual de un bloque) ni sus uniones
 *  - latestId:  nodo más reciente (se resalta con pulso); por defecto, el último de la lista
 *  - compact:   tarjetas sin la línea del agente (dentro de un bloque el agente ya está en la cabecera)
 */
export default function TreeCanvas({ nodes, selectedNodeId, onSelectNode, hiddenIds = [], latestId, compact = false, cardWidth = 205 }) {
  const hidden = React.useMemo(() => new Set(hiddenIds), [hiddenIds]);
  const nodeMap = React.useMemo(() => Object.fromEntries(nodes.map((n) => [n.id, n])), [nodes]);
  const latest = latestId ?? [...nodes].reverse().find((n) => n.type !== 'start')?.id;

  return (
    <>
      <svg style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', pointerEvents: 'none' }}>
        <Edges nodes={nodes} nodeMap={nodeMap} hidden={hidden} compact={compact} cardWidth={cardWidth} />
      </svg>
      {nodes
        .filter((n) => !hidden.has(n.id))
        .map((node) => (
          <NodeCard
            key={node.id}
            node={node}
            isSelected={selectedNodeId === node.id}
            isLatest={node.id === latest && node.type !== 'start'}
            onSelectNode={onSelectNode}
            compact={compact}
            cardWidth={cardWidth}
          />
        ))}
    </>
  );
}
