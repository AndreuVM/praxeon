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
  let iconBg = 'rgba(16, 185, 129, 0.15)';
  let iconBorder = 'rgba(16, 185, 129, 0.35)';
  let iconColor = '#34d399';
  let borderColor = '#1e293b';
  let bg = '#121824';

  if (node.type === 'start') {
    icon = Play;
    iconBg = '#1e293b';
    iconBorder = '#334155';
    iconColor = '#cbd5e1';
    bg = '#162030';
  } else if (node.status === 'PROPOSE' || node.type === 'hub') {
    icon = Sparkles;
    iconBg = 'rgba(139, 92, 246, 0.2)';
    iconBorder = 'rgba(139, 92, 246, 0.4)';
    iconColor = '#c084fc';
    borderColor = 'rgba(139, 92, 246, 0.4)';
    bg = '#1a1f33';
  } else if (node.status === 'BLOCK') {
    icon = X;
    iconBg = 'rgba(239, 68, 68, 0.18)';
    iconBorder = 'rgba(239, 68, 68, 0.35)';
    iconColor = '#f87171';
    borderColor = 'rgba(239, 68, 68, 0.28)';
    bg = '#1a151b';
  } else if (node.status === 'REVIEW') {
    icon = AlertTriangle;
    iconBg = 'rgba(245, 158, 11, 0.18)';
    iconBorder = 'rgba(245, 158, 11, 0.35)';
    iconColor = '#fbbf24';
    borderColor = 'rgba(245, 158, 11, 0.28)';
    bg = '#1c1b18';
  } else if (node.status === 'REPLAN') {
    icon = RotateCcw;
    iconBg = 'rgba(168, 85, 247, 0.2)';
    iconBorder = 'rgba(168, 85, 247, 0.4)';
    iconColor = '#c084fc';
    borderColor = 'rgba(168, 85, 247, 0.35)';
    bg = '#181424';
  } else if (node.status === 'PENDING') {
    icon = Clock;
    iconBg = 'rgba(100, 116, 139, 0.2)';
    iconBorder = 'rgba(100, 116, 139, 0.35)';
    iconColor = '#94a3b8';
    borderColor = '#293548';
    bg = '#131a26';
  } else if (node.status === 'EXECUTING') {
    icon = RefreshCw;
    iconBg = 'rgba(56, 189, 248, 0.2)';
    iconBorder = 'rgba(56, 189, 248, 0.45)';
    iconColor = '#38bdf8';
    borderColor = 'rgba(56, 189, 248, 0.45)';
    bg = '#0e1726';
  }

  if (isSelected) {
    borderColor = '#5a6882';
    bg = '#1c2432';
  }

  return { icon, iconBg, iconBorder, iconColor, borderColor, bg };
}

function edgeColors(node) {
  let strokeColor = '#3b4961';
  let particleColor = '#60a5fa';
  if (node.status === 'ALLOW') {
    strokeColor = 'rgba(16, 185, 129, 0.7)';
    particleColor = '#34d399';
  } else if (node.status === 'PROPOSE' || node.status === 'REPLAN') {
    strokeColor = 'rgba(168, 85, 247, 0.75)';
    particleColor = '#c084fc';
  } else if (node.status === 'REVIEW') {
    strokeColor = 'rgba(245, 158, 11, 0.75)';
    particleColor = '#fbbf24';
  } else if (node.status === 'BLOCK') {
    strokeColor = 'rgba(239, 68, 68, 0.7)';
    particleColor = '#f87171';
  } else if (node.status === 'EXECUTING') {
    strokeColor = 'rgba(56, 189, 248, 0.85)';
    particleColor = '#38bdf8';
  }
  return { strokeColor, particleColor };
}

function Edges({ nodes, nodeMap, hidden, compact }) {
  return nodes.map((node) => {
    if (hidden.has(node.id) || !node.parentId || !nodeMap[node.parentId] || hidden.has(node.parentId)) return null;
    const parent = nodeMap[node.parentId];

    const startX = parent.x + (parent.type === 'start' ? 40 : 100);
    const startY = parent.y + (parent.type === 'start' ? 28 : cardHeight(parent, compact) - 2);
    const endX = node.x + (node.type === 'start' ? 40 : 100);
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
        {/* Resplandor sutil del enlace */}
        <path d={pathData} fill="none" stroke={strokeColor} strokeWidth="3.5" opacity="0.25" filter="blur(1px)" />
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
            <rect x={midX - pillW / 2} y={midY - 9} width={pillW} height="18" rx="9" fill="#0a0e16" stroke={handoffColor} strokeWidth="1.2" />
            <text x={midX} y={midY + 3.5} textAnchor="middle" fontSize="10" fontWeight="600" fill={handoffColor}>
              {pill}
            </text>
          </g>
        )}
      </g>
    );
  });
}

function NodeCard({ node, isSelected, isLatest, onSelectNode, compact }) {
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
          color: '#e2e8f0',
          fontSize: '11px',
          fontWeight: '600',
          cursor: 'pointer',
          userSelect: 'none',
          boxShadow: isSelected ? '0 0 12px rgba(56, 189, 248, 0.4)' : 'var(--shadow-sm)',
          transition: 'all 0.25s ease',
        }}
      >
        <Play size={11} style={{ fill: '#e2e8f0' }} />
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
        width: '205px',
        height: `${cardHeight(node, compact)}px`,
        borderRadius: '8px',
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
          ? '0 0 12px rgba(90, 104, 130, 0.5), inset 0 1px 0 rgba(255, 255, 255, 0.1)'
          : isLatest
            ? '0 0 12px rgba(56, 189, 248, 0.35), inset 0 1px 0 rgba(255, 255, 255, 0.08)'
            : 'var(--shadow-clay-sm)',
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
          style={{ fontSize: '11.5px', fontWeight: '600', color: isSelected ? '#ffffff' : '#f0f6fc', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}
        >
          {node.label}
        </span>
        {showAgent && (
          <span
            title={`${node.agentName}${node.agentRole ? ` — ${node.agentRole}` : ''}`}
            style={{ fontSize: '10.5px', fontWeight: 700, color: agentColor(node.agentName), whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}
          >
            {node.agentName}
            {node.agentRole ? <span style={{ fontWeight: 400, color: '#8b949e' }}> · {node.agentRole}</span> : null}
          </span>
        )}
        {detail ? (
          <span style={{ fontSize: '10px', color: '#8b949e', marginTop: '1px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{detail}</span>
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
export default function TreeCanvas({ nodes, selectedNodeId, onSelectNode, hiddenIds = [], latestId, compact = false }) {
  const hidden = React.useMemo(() => new Set(hiddenIds), [hiddenIds]);
  const nodeMap = React.useMemo(() => Object.fromEntries(nodes.map((n) => [n.id, n])), [nodes]);
  const latest = latestId ?? [...nodes].reverse().find((n) => n.type !== 'start')?.id;

  return (
    <>
      <svg style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', pointerEvents: 'none' }}>
        <Edges nodes={nodes} nodeMap={nodeMap} hidden={hidden} compact={compact} />
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
          />
        ))}
    </>
  );
}
