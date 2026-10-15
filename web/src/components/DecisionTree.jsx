import React, { useState, useRef, useEffect } from 'react';
import { Plus, Minus, Maximize2, Download } from 'lucide-react';
import { agentColor } from '../utils/agentColor';
import WorkflowBlocks, { buildBlocks } from './WorkflowBlocks';
import TreeCanvas from './TreeCanvas';

export { agentColor };

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
  const [blockOpen, setBlockOpen] = useState({}); // decisión explícita del usuario por bloque (abrir/cerrar)

  // Con un workflow, los pasos se agrupan en bloques (uno por agente y nodo) en lugar de un árbol gigante
  const hasWorkflow = nodes.some((n) => n.agentName);
  const blocks = React.useMemo(() => (hasWorkflow ? buildBlocks(nodes) : []), [nodes, hasWorkflow]);
  // Sin elección del usuario: se abre solo el bloque en curso; al completarse se pliega
  const isBlockOpen = (b) => (blockOpen[b.key] !== undefined ? blockOpen[b.key] : b.state === 'running' && b.index === blocks.length - 1);
  const toggleBlock = (b) => setBlockOpen((prev) => ({ ...prev, [b.key]: !isBlockOpen(b) }));
  const setAllBlocks = (value) => setBlockOpen(Object.fromEntries(blocks.map((b) => [b.key, value])));

  // Zoom controls
  const handleZoomIn = () => setZoom((z) => Math.min(z + 0.15, 1.8));
  const handleZoomOut = () => setZoom((z) => Math.max(z - 0.15, 0.25));

  // Ajustar: encuadra el árbol completo en el visor
  const fitView = React.useCallback(() => {
    const real = nodes.filter((n) => typeof n.x === 'number');
    if (real.length === 0) return;
    const minX = Math.min(...real.map((n) => n.x));
    const maxX = Math.max(...real.map((n) => n.x + 205));
    const minY = Math.min(...real.map((n) => n.y));
    const maxY = Math.max(...real.map((n) => n.y + 58));
    const vw = canvasRef.current?.clientWidth || 800;
    const vh = canvasRef.current?.clientHeight || 500;
    const z = Math.max(0.25, Math.min(1, (vw - 60) / (maxX - minX), (vh - 40) / (maxY - minY)));
    setZoom(z);
    setPan({ x: (vw - (maxX - minX) * z) / 2 - minX * z, y: 20 - minY * z });
  }, [nodes]);
  const handleResetZoom = fitView;

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

  // Si el visor cambia de tamaño (apertura o ampliación del cajón), se vuelve a encuadrar el árbol
  const fitRef = useRef(fitView);
  fitRef.current = fitView;
  const autoRef = useRef(autoLayout);
  autoRef.current = autoLayout;
  useEffect(() => {
    const el = canvasRef.current;
    if (!el || typeof ResizeObserver === 'undefined') return undefined;
    let timer = null;
    const ro = new ResizeObserver(() => {
      clearTimeout(timer);
      timer = setTimeout(() => { if (autoRef.current) fitRef.current(); }, 120);
    });
    ro.observe(el);
    return () => { clearTimeout(timer); ro.disconnect(); };
  }, []);

  // Seguimiento automático: centra el nodo nuevo (teniendo en cuenta el zoom); al cargar un árbol entero, lo encuadra
  const prevCountRef = useRef(0);
  useEffect(() => {
    const previous = prevCountRef.current;
    prevCountRef.current = nodes.length;
    if (!autoLayout || hasWorkflow || nodes.length <= 1) return;
    if (nodes.length - previous > 3) {
      fitView();
      return;
    }
    const latestNode = nodes[nodes.length - 1];
    if (latestNode && typeof latestNode.y === 'number') {
      const vw = canvasRef.current?.clientWidth || 800;
      const vh = canvasRef.current?.clientHeight || 500;
      setPan({ x: vw / 2 - (latestNode.x + 102) * zoom, y: vh * 0.6 - latestNode.y * zoom });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, autoLayout, hasWorkflow]);

  const canvasSize = React.useMemo(() => {
    let maxX = 0;
    let maxY = 0;
    nodes.forEach((n) => {
      maxX = Math.max(maxX, (n.x || 0) + 260);
      maxY = Math.max(maxY, (n.y || 0) + 140);
    });
    return { width: Math.max(1200, maxX), height: Math.max(900, maxY) };
  }, [nodes]);

  // Agente que tiene el control: el del último paso del árbol que pertenece a un workflow
  const controlling = React.useMemo(() => {
    for (let i = nodes.length - 1; i >= 0; i -= 1) if (nodes[i].agentName) return nodes[i];
    return null;
  }, [nodes]);
  const agentLegend = React.useMemo(() => {
    const seen = new Map();
    nodes.forEach((n) => {
      if (n.agentName && !seen.has(n.agentName)) seen.set(n.agentName, n.agentRole);
    });
    return [...seen.entries()];
  }, [nodes]);

  return (
    <div
      style={{
        flex: 1,
        display: 'flex',
        flexDirection: 'column',
        backgroundColor: 'var(--bg)',
        position: 'relative',
        overflow: 'hidden',
      }}
    >
      {/* Controles del mapa */}
      <div
        style={{
          minHeight: '42px',
          padding: '6px 16px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          flexWrap: 'wrap',
          gap: '8px 14px',
          borderBottom: '1px solid var(--line)',
          backgroundColor: 'var(--bg)',
          zIndex: 10,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', minWidth: 0 }}>
          {controlling ? (
            <span
              title={`Agente con el control ahora: ${controlling.agentName}${controlling.agentRole ? ` (${controlling.agentRole})` : ''} · nodo «${controlling.wfNodeName || controlling.wfNodeId}»`}
              style={{ display: 'flex', alignItems: 'center', gap: '7px', fontSize: 'var(--fs-secondary)', color: 'var(--text)', minWidth: 0 }}
            >
              <span className="pulse-dot" />
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                En control: {controlling.agentName}
                <span style={{ color: 'var(--muted)' }}> · {controlling.wfNodeName || controlling.wfNodeId}</span>
              </span>
            </span>
          ) : (
            <span style={{ fontSize: 'var(--fs-secondary)', color: 'var(--muted)' }}>{nodes.length > 1 ? `${nodes.length - 1} pasos` : 'Sin pasos todavía'}</span>
          )}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer', fontSize: 'var(--fs-secondary)', color: 'var(--muted)' }}>
            <span>Diseño automático</span>
            <button
              type="button"
              role="switch"
              aria-checked={autoLayout}
              aria-label="Diseño automático"
              onClick={() => setAutoLayout(!autoLayout)}
              style={{ width: '30px', height: '17px', borderRadius: '99px', border: 0, padding: 0, background: autoLayout ? 'var(--accent)' : 'var(--line)', position: 'relative' }}
            >
              <span style={{ position: 'absolute', top: '2px', left: autoLayout ? '15px' : '2px', width: '13px', height: '13px', borderRadius: '50%', background: autoLayout ? 'var(--on-accent)' : 'var(--muted)', transition: 'left var(--t-fast)' }} />
            </button>
          </label>

          {hasWorkflow && (
            <div style={{ display: 'flex', gap: '6px' }}>
              <button type="button" className="btn btn-secondary" onClick={() => setAllBlocks(true)} style={{ padding: '2px 9px', fontSize: 'var(--fs-secondary)' }}>
                Expandir todo
              </button>
              <button type="button" className="btn btn-secondary" onClick={() => setAllBlocks(false)} style={{ padding: '2px 9px', fontSize: 'var(--fs-secondary)' }}>
                Contraer todo
              </button>
            </div>
          )}

          <button onClick={handleResetZoom} className="btn-icon" title="Ajustar vista" aria-label="Ajustar vista">
            <Maximize2 size={15} />
          </button>
          <button className="btn-icon" title="Exportar árbol" aria-label="Exportar árbol">
            <Download size={15} />
          </button>
        </div>
      </div>

      {!hasWorkflow && agentLegend.length > 0 && (
        <div
          style={{ position: 'absolute', bottom: '12px', left: '16px', zIndex: 10, display: 'flex', flexDirection: 'column', gap: '4px', padding: '8px 10px', backgroundColor: 'var(--surface)', border: '1px solid var(--line)', borderRadius: '9px', fontSize: '11px', color: 'var(--text)' }}
        >
          <span style={{ color: 'var(--muted)', fontWeight: 600 }}>Agentes</span>
          {agentLegend.map(([name, role]) => (
            <span key={name} style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <span style={{ width: '9px', height: '9px', borderRadius: '6px', backgroundColor: agentColor(name) }} />
              {name}
              {role ? <span style={{ color: 'var(--muted)' }}>· {role}</span> : null}
            </span>
          ))}
          <span style={{ color: 'var(--muted)', display: 'flex', alignItems: 'center', gap: '6px' }}>
            <svg width="18" height="6"><line x1="0" y1="3" x2="18" y2="3" stroke="var(--muted)" strokeWidth="2" strokeDasharray="5 3" /></svg>
            traspaso de tarea
          </span>
        </div>
      )}

      {/* Floating Canvas Zoom Toolbar */}
      <div
        style={{
          position: 'absolute',
          top: '56px',
          left: '16px',
          zIndex: 10,
          display: 'flex',
          flexDirection: 'column',
          backgroundColor: 'var(--surface)',
          borderRadius: '9px',
          border: '1px solid var(--line)',
          overflow: 'hidden',
          boxShadow: 'none',
        }}
      >
        <button
          onClick={handleZoomIn}
          style={{
            padding: '7px 9px',
            background: 'transparent',
            border: 'none',
            color: 'var(--text)',
            cursor: 'pointer',
            borderBottom: '1px solid var(--line)',
          }}
          title="Acercar" aria-label="Acercar"
        >
          <Plus size={14} />
        </button>
        <button
          onClick={handleZoomOut}
          style={{
            padding: '7px 9px',
            background: 'transparent',
            border: 'none',
            color: 'var(--text)',
            cursor: 'pointer',
            borderBottom: '1px solid var(--line)',
          }}
          title="Alejar" aria-label="Alejar"
        >
          <Minus size={14} />
        </button>
        <button
          onClick={handleResetZoom}
          style={{
            padding: '7px 9px',
            background: 'transparent',
            border: 'none',
            color: 'var(--text)',
            cursor: 'pointer',
            fontSize: '11px',
            fontWeight: '600',
          }}
          title="Ajustar a la vista"
        >
          Ajustar
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
          backgroundImage: 'radial-gradient(var(--line) 1px, transparent 1px)',
          backgroundSize: '24px 24px',
        }}
      >
        {hasWorkflow ? (
          <WorkflowBlocks
            blocks={blocks}
            isOpen={isBlockOpen}
            onToggle={toggleBlock}
            selectedNodeId={selectedNodeId}
            onSelectNode={onSelectNode}
            autoScroll={autoLayout}
            zoom={zoom}
          />
        ) : (
        <div
          style={{
            transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
            transformOrigin: '0 0',
            position: 'absolute',
            width: `${canvasSize.width}px`,
            height: `${canvasSize.height}px`,
            transition: isDragging ? 'none' : 'transform 0.4s cubic-bezier(0.16, 1, 0.3, 1)',
          }}
        >
          <TreeCanvas nodes={nodes} selectedNodeId={selectedNodeId} onSelectNode={onSelectNode} />
        </div>
        )}
      </div>
    </div>
  );
}
