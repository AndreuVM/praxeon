import React, { useState, useRef, useEffect } from 'react';
import {
  Plus,
  Minus,
  Maximize2,
  Download,
  Play,
  Check,
  X,
  AlertTriangle,
  Clock,
  Sparkles,
  RefreshCw,
  RotateCcw,
} from 'lucide-react';

export default function DecisionTree({
  nodes = [],
  selectedNodeId = 'node-5',
  onSelectNode,
}) {
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 30, y: 10 });
  const [isDragging, setIsDragging] = useState(false);
  const [dragStart, setDragStart] = useState({ x: 0, y: 0 });
  const [autoLayout, setAutoLayout] = useState(true);
  const canvasRef = useRef(null);

  // Zoom controls
  const handleZoomIn = () => setZoom((z) => Math.min(z + 0.15, 1.8));
  const handleZoomOut = () => setZoom((z) => Math.max(z - 0.15, 0.55));
  const handleResetZoom = () => {
    setZoom(1);
    setPan({ x: 30, y: 10 });
  };

  // Pan controls
  const handleMouseDown = (e) => {
    // Only drag if clicking background, not a node
    if (e.target.closest('.tree-node')) return;
    setIsDragging(true);
    setDragStart({ x: e.clientX - pan.x, y: e.clientY - pan.y });
  };

  const handleMouseMove = (e) => {
    if (!isDragging) return;
    setPan({
      x: e.clientX - dragStart.x,
      y: e.clientY - dragStart.y,
    });
  };

  const handleMouseUp = () => setIsDragging(false);

  // Auto-scroll suave para centrar el nuevo nodo en el viewport
  useEffect(() => {
    if (!autoLayout || nodes.length <= 1) return;
    const latestNode = nodes[nodes.length - 1];
    if (latestNode && typeof latestNode.y === 'number') {
      const viewportHeight = canvasRef.current?.clientHeight || 500;
      const targetY = Math.min(20, Math.max(-1200, (viewportHeight * 0.35) - latestNode.y));
      setPan((p) => ({ ...p, y: targetY }));
    }
  }, [nodes, autoLayout]);

  // Helper to find parent coordinates for bezier curves
  const nodeMap = React.useMemo(() => {
    const map = {};
    nodes.forEach((n) => {
      map[n.id] = n;
    });
    return map;
  }, [nodes]);

  // Render SVG connecting curves between nodes
  const renderConnections = () => {
    return nodes.map((node) => {
      if (!node.parentId || !nodeMap[node.parentId]) return null;
      const parent = nodeMap[node.parentId];

      const startX = parent.x + (parent.type === 'start' ? 40 : 100);
      const startY = parent.y + (parent.type === 'start' ? 28 : 42);
      const endX = node.x + (node.type === 'start' ? 40 : 100);
      const endY = node.y;

      const midY = (startY + endY) / 2;
      const pathData = `M ${startX} ${startY} C ${startX} ${midY}, ${endX} ${midY}, ${endX} ${endY}`;

      // Pick edge color based on target node status
      let strokeColor = '#3b4961';
      let particleColor = '#60a5fa';
      if (node.status === 'ALLOW') {
        strokeColor = 'rgba(16, 185, 129, 0.7)';
        particleColor = '#34d399';
      } else if (node.status === 'PROPOSE') {
        strokeColor = 'rgba(168, 85, 247, 0.7)';
        particleColor = '#c084fc';
      } else if (node.status === 'REVIEW') {
        strokeColor = 'rgba(245, 158, 11, 0.75)';
        particleColor = '#fbbf24';
      } else if (node.status === 'BLOCK') {
        strokeColor = 'rgba(239, 68, 68, 0.7)';
        particleColor = '#f87171';
      } else if (node.status === 'REPLAN') {
        strokeColor = 'rgba(168, 85, 247, 0.75)';
        particleColor = '#c084fc';
      } else if (node.status === 'EXECUTING') {
        strokeColor = 'rgba(56, 189, 248, 0.85)';
        particleColor = '#38bdf8';
      }

      return (
        <g key={`edge-${node.parentId}-${node.id}`}>
          {/* Resplandor sutil del enlace */}
          <path
            d={pathData}
            fill="none"
            stroke={strokeColor}
            strokeWidth="3.5"
            opacity="0.25"
            filter="blur(1px)"
          />
          {/* Trazado principal animado al unirse al grafo */}
          <path
            className="edge-path-animated"
            d={pathData}
            fill="none"
            stroke={strokeColor}
            strokeWidth="2"
            strokeDasharray={node.status === 'REVIEW' ? '4 3' : 'none'}
          />
          {/* Partícula en movimiento continuo indicando flujo de datos en tiempo real */}
          <circle r="2.5" fill={particleColor} opacity="0.95">
            <animateMotion dur="2.2s" repeatCount="indefinite" path={pathData} />
          </circle>
          {/* Punta de flecha indicadora */}
          <polygon
            points={`${endX},${endY} ${endX - 4},${endY - 6} ${endX + 4},${endY - 6}`}
            fill={strokeColor}
          />
        </g>
      );
    });
  };

  // Node icon and style dispatcher
  const getNodeVisuals = (node, isSelected) => {
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
  };

  return (
    <div
      style={{
        flex: 1,
        display: 'flex',
        flexDirection: 'column',
        backgroundColor: '#0a0e16',
        position: 'relative',
        overflow: 'hidden',
      }}
    >
      {/* Top Header of Canvas */}
      <div
        style={{
          height: '42px',
          padding: '0 16px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          borderBottom: '1px solid #1a202c',
          backgroundColor: '#0c0f14',
          zIndex: 10,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span style={{ fontSize: '13px', fontWeight: '600', color: '#f0f6fc' }}>
            Decision Tree (Live)
          </span>
          <span className="pulse-dot" style={{ width: '6px', height: '6px' }} />
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
          {/* Auto layout switch */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <span style={{ fontSize: '11.5px', color: '#8b949e' }}>Auto layout</span>
            <div
              onClick={() => setAutoLayout(!autoLayout)}
              style={{
                width: '32px',
                height: '18px',
                borderRadius: '9999px',
                backgroundColor: autoLayout ? '#48546a' : '#222a36',
                position: 'relative',
                cursor: 'pointer',
                transition: 'background 0.2s ease',
              }}
            >
              <div
                style={{
                  width: '14px',
                  height: '14px',
                  borderRadius: '50%',
                  backgroundColor: '#ffffff',
                  position: 'absolute',
                  top: '2px',
                  left: autoLayout ? '16px' : '2px',
                  transition: 'left 0.2s ease',
                }}
              />
            </div>
          </div>

          {/* Action icons */}
          <button
            onClick={handleResetZoom}
            className="btn btn-secondary"
            style={{ padding: '4px', border: 'none', background: 'transparent', color: '#8595a8' }}
            title="Ajustar vista"
          >
            <Maximize2 size={15} />
          </button>
          <button
            className="btn btn-secondary"
            style={{ padding: '4px', border: 'none', background: 'transparent', color: '#8595a8' }}
            title="Exportar árbol"
          >
            <Download size={15} />
          </button>
        </div>
      </div>

      {/* Floating Canvas Zoom Toolbar */}
      <div
        style={{
          position: 'absolute',
          top: '56px',
          left: '16px',
          zIndex: 10,
          display: 'flex',
          flexDirection: 'column',
          backgroundColor: '#141924',
          borderRadius: '8px',
          border: '1px solid #1e2636',
          overflow: 'hidden',
          boxShadow: 'var(--shadow-clay-sm)',
        }}
      >
        <button
          onClick={handleZoomIn}
          style={{
            padding: '7px 9px',
            background: 'transparent',
            border: 'none',
            color: '#c9d1d9',
            cursor: 'pointer',
            borderBottom: '1px solid #1e2636',
          }}
          title="Zoom In"
        >
          <Plus size={14} />
        </button>
        <button
          onClick={handleZoomOut}
          style={{
            padding: '7px 9px',
            background: 'transparent',
            border: 'none',
            color: '#c9d1d9',
            cursor: 'pointer',
            borderBottom: '1px solid #1e2636',
          }}
          title="Zoom Out"
        >
          <Minus size={14} />
        </button>
        <button
          onClick={handleResetZoom}
          style={{
            padding: '7px 9px',
            background: 'transparent',
            border: 'none',
            color: '#cbd5e1',
            cursor: 'pointer',
            fontSize: '10px',
            fontWeight: '600',
          }}
          title="Ajustar"
        >
          1:1
        </button>
      </div>

      {/* Interactive Tree Viewport */}
      <div
        ref={canvasRef}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
        style={{
          flex: 1,
          cursor: isDragging ? 'grabbing' : 'grab',
          position: 'relative',
          overflow: 'hidden',
          backgroundImage: 'radial-gradient(#1e293b 1px, transparent 1px)',
          backgroundSize: '24px 24px',
        }}
      >
        <div
          style={{
            transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
            transformOrigin: '0 0',
            position: 'absolute',
            width: '1200px',
            height: '900px',
            transition: isDragging ? 'none' : 'transform 0.4s cubic-bezier(0.16, 1, 0.3, 1)',
          }}
        >
          {/* SVG Connection Paths */}
          <svg
            style={{
              position: 'absolute',
              top: 0,
              left: 0,
              width: '100%',
              height: '100%',
              pointerEvents: 'none',
            }}
          >
            {renderConnections()}
          </svg>

          {/* HTML Nodes */}
          {nodes.map((node, idx) => {
            const isSelected = selectedNodeId === node.id;
            const isLatest = idx === nodes.length - 1 && node.type !== 'start';
            const isExecuting = node.status === 'EXECUTING';
            const visuals = getNodeVisuals(node, isSelected);
            const Icon = visuals.icon;

            if (node.type === 'start') {
              return (
                <div
                  key={node.id}
                  className="tree-node"
                  onClick={() => onSelectNode(node.id)}
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

            return (
              <div
                key={node.id}
                className={`tree-node ${node.isNew ? 'tree-node-enter' : ''} ${isLatest || isExecuting ? 'tree-node-active-pulse' : ''}`}
                onClick={() => onSelectNode(node.id)}
                style={{
                  position: 'absolute',
                  left: `${node.x}px`,
                  top: `${node.y}px`,
                  width: '205px',
                  height: '44px',
                  borderRadius: '8px',
                  backgroundColor: visuals.bg,
                  border: `1px solid ${visuals.borderColor}`,
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
                {/* Node icon pill */}
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

                {/* Node Text labels */}
                <div style={{ display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
                  <span
                    style={{
                      fontSize: '11.5px',
                      fontWeight: '600',
                      color: isSelected ? '#ffffff' : '#f0f6fc',
                      whiteSpace: 'nowrap',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                    }}
                  >
                    {node.label}
                  </span>
                  <span
                    style={{
                      fontSize: '10px',
                      color: '#8b949e',
                      marginTop: '1px',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {node.subtitle}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
