import React, { useState, useRef } from 'react';
import {
  Workflow,
  ExternalLink,
  RefreshCw,
  Sparkles,
  Layers,
  ShieldCheck,
  CheckCircle2,
  Info,
  Maximize2,
  Cpu,
  GitBranch,
} from 'lucide-react';

export default function WorkflowsView() {
  const [editorUrl, setEditorUrl] = useState('/v1/workflows/editor/ui');
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [showGuide, setShowGuide] = useState(false);
  const iframeRef = useRef(null);

  const handleReload = () => {
    setIsRefreshing(true);
    if (iframeRef.current) {
      iframeRef.current.src = `${editorUrl}?t=${Date.now()}`;
    }
    setTimeout(() => setIsRefreshing(false), 500);
  };

  const handleOpenStandalone = () => {
    window.open(editorUrl, '_blank', 'noopener,noreferrer');
  };

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        flex: 1,
        height: '100%',
        backgroundColor: '#080c14',
        color: '#f8fafc',
        overflow: 'hidden',
        position: 'relative',
      }}
    >
      {/* Top Controls Toolbar */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '8px 16px',
          backgroundColor: '#0c1017',
          borderBottom: '1px solid #1e293b',
          flexShrink: 0,
          gap: '12px',
        }}
      >
        {/* Left: Branding & Status */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: '28px',
              height: '28px',
              borderRadius: '6px',
              background: 'linear-gradient(135deg, rgba(56, 189, 248, 0.2), rgba(129, 140, 248, 0.2))',
              border: '1px solid rgba(56, 189, 248, 0.35)',
              color: '#38bdf8',
            }}
          >
            <Workflow size={16} />
          </div>

          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <span style={{ fontSize: '13px', fontWeight: '700', letterSpacing: '-0.01em', color: '#f8fafc' }}>
                Visual Workflow Orchestrator
              </span>
              <span
                style={{
                  fontSize: '9.5px',
                  fontWeight: '700',
                  padding: '2px 6px',
                  borderRadius: '10px',
                  backgroundColor: 'rgba(56, 189, 248, 0.15)',
                  color: '#38bdf8',
                  border: '1px solid rgba(56, 189, 248, 0.3)',
                }}
              >
                Fase 6 Live Engine
              </span>
            </div>
            <div style={{ fontSize: '11px', color: '#64748b' }}>
              Editor drag & drop interactivo con retroceso determinista y ejecución paso a paso
            </div>
          </div>
        </div>

        {/* Right: Actions */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <button
            onClick={() => setShowGuide(!showGuide)}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              padding: '5px 10px',
              borderRadius: '6px',
              border: '1px solid #334155',
              backgroundColor: showGuide ? 'rgba(56, 189, 248, 0.12)' : '#1e293b',
              color: showGuide ? '#38bdf8' : '#cbd5e1',
              fontSize: '11px',
              fontWeight: '500',
              cursor: 'pointer',
              transition: 'all 0.15s ease',
            }}
            title="Mostrar u ocultar guía rápida de atajos y características"
          >
            <Info size={13} />
            <span>Guía Rápida</span>
          </button>

          <button
            onClick={handleReload}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              padding: '5px 10px',
              borderRadius: '6px',
              border: '1px solid #334155',
              backgroundColor: '#1e293b',
              color: '#cbd5e1',
              fontSize: '11px',
              fontWeight: '500',
              cursor: 'pointer',
              transition: 'all 0.15s ease',
            }}
            title="Recargar lienzo del editor"
          >
            <RefreshCw
              size={13}
              style={{
                animation: isRefreshing ? 'spin 0.7s linear infinite' : 'none',
              }}
            />
            <span>Recargar</span>
          </button>

          <button
            onClick={handleOpenStandalone}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              padding: '5px 12px',
              borderRadius: '6px',
              border: '1px solid rgba(56, 189, 248, 0.4)',
              backgroundColor: 'rgba(56, 189, 248, 0.15)',
              color: '#38bdf8',
              fontSize: '11px',
              fontWeight: '600',
              cursor: 'pointer',
              transition: 'all 0.15s ease',
            }}
            title="Abrir editor en ventana completa e independiente"
          >
            <ExternalLink size={13} />
            <span>Abrir en Pestaña Completa</span>
          </button>
        </div>
      </div>

      {/* Optional Collapsible Quick Guide */}
      {showGuide && (
        <div
          style={{
            backgroundColor: '#0f172a',
            borderBottom: '1px solid #1e293b',
            padding: '10px 16px',
            fontSize: '11.5px',
            color: '#94a3b8',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: '16px',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '20px', flexWrap: 'wrap' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <GitBranch size={13} style={{ color: '#38bdf8' }} />
              <span><strong>Arrastrar nodos:</strong> Añade pasos de agente, herramientas o decisiones al lienzo.</span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <Cpu size={13} style={{ color: '#10b981' }} />
              <span><strong>Conectar puertos:</strong> Arrastra desde el puerto de salida para crear aristas con condición.</span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <Sparkles size={13} style={{ color: '#f59e0b' }} />
              <span><strong>Backtracking:</strong> Haz clic en cualquier nodo previo ejecutado para rebobinar el estado.</span>
            </div>
          </div>
          <a
            href="/docs#/Workflows"
            target="_blank"
            rel="noreferrer"
            style={{
              color: '#38bdf8',
              textDecoration: 'none',
              fontSize: '11px',
              fontWeight: '600',
              whiteSpace: 'nowrap',
            }}
          >
            Ver API REST Docs &rarr;
          </a>
        </div>
      )}

      {/* Main Iframe Canvas Container */}
      <div
        style={{
          flex: 1,
          width: '100%',
          height: '100%',
          position: 'relative',
          backgroundColor: '#080c14',
        }}
      >
        <iframe
          ref={iframeRef}
          src={editorUrl}
          title="PRAXEON Visual Workflow Orchestrator"
          style={{
            width: '100%',
            height: '100%',
            border: 'none',
            display: 'block',
            backgroundColor: '#080c14',
          }}
          onError={() => {
            // Si /v1/workflows/editor/ui falla por estar sirviéndose en estático puro
            if (editorUrl !== '/workflow_editor.html') {
              setEditorUrl('/workflow_editor.html');
            }
          }}
        />
      </div>
    </div>
  );
}
