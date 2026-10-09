import React from 'react';
import { ChevronDown, User, Cpu, Shield, AlertTriangle, Box, Plus, Key } from 'lucide-react';

export default function Header({
  sessionId = '7f3a2c',
  runtimeActive = true,
  executionMode = 'local_restricted',
  operatorId = 'operator_admin',
  operatorRole = 'operator',
  isAutonomous = false,
  isDegraded = false,
  fallbackReason = null,
  decisionModel = null,
  onOpenSessions,
  onOpenPropose: _onOpenPropose,
  onNewSession,
  onOpenAuth,
}) {
  const getModeBadge = () => {
    const mode = (executionMode || '').toLowerCase();
    if (mode === 'full_access' || mode.startsWith('full_access')) {
      const isAuto = isAutonomous || mode === 'full_access_autonomous';
      return (
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
          padding: '3px 10px',
          borderRadius: '9999px',
          backgroundColor: isAuto ? 'rgba(239, 68, 68, 0.18)' : 'rgba(245, 158, 11, 0.16)',
          border: `1px solid ${isAuto ? 'rgba(239, 68, 68, 0.45)' : 'rgba(245, 158, 11, 0.4)'}`,
          fontSize: '11px',
          fontWeight: '700',
          color: isAuto ? '#f87171' : '#fbbf24',
          letterSpacing: '0.04em',
        }} title={isAuto ? "Full Access Autónomo: ejecución directa supervisada sin parada interactiva humana" : "Full Access Manual: ejecución directa requiriendo confirmación interactiva del operador"}>
          <AlertTriangle size={12} />
          <span>{isAuto ? 'FULL ACCESS (AUTONOMOUS)' : 'FULL ACCESS (MANUAL)'}</span>
        </div>
      );
    }
    if (mode === 'container') {
      return (
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
          padding: '3px 10px',
          borderRadius: '9999px',
          backgroundColor: 'rgba(56, 189, 248, 0.12)',
          border: '1px solid rgba(56, 189, 248, 0.3)',
          fontSize: '11px',
          fontWeight: '600',
          color: '#38bdf8',
        }} title="Aislamiento en contenedor Docker OCI">
          <Box size={12} />
          <span>CONTAINER</span>
        </div>
      );
    }
    return (
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: '6px',
        padding: '3px 10px',
        borderRadius: '9999px',
        backgroundColor: 'rgba(167, 139, 250, 0.12)',
        border: '1px solid rgba(167, 139, 250, 0.3)',
        fontSize: '11px',
        fontWeight: '600',
        color: '#c084fc',
      }} title="Sandbox local restringido con rutas limitadas">
        <Shield size={12} />
        <span>LOCAL RESTRICTED</span>
      </div>
    );
  };

  return (
    <header style={{
      height: '52px',
      backgroundColor: '#0c0f14',
      borderBottom: '1px solid #1a202c',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      padding: '0 18px',
      userSelect: 'none',
      zIndex: 20,
    }}>
      {/* Brand & Subtitle */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span style={{
            fontSize: '15px',
            fontWeight: '800',
            letterSpacing: '0.14em',
            color: '#f0f6fc',
            fontFamily: 'var(--font-sans)',
          }}>
            PRAXEON
          </span>
          <span style={{
            fontSize: '10px',
            fontWeight: '700',
            color: '#58a6ff',
            backgroundColor: 'rgba(88, 166, 255, 0.15)',
            border: '1px solid rgba(88, 166, 255, 0.3)',
            borderRadius: '4px',
            padding: '1px 5px',
          }}>
            v1.0.0
          </span>
        </div>

        <span style={{
          fontSize: '12px',
          color: '#8b949e',
          fontWeight: '400',
          letterSpacing: '0.02em',
          borderLeft: '1px solid #202735',
          paddingLeft: '16px',
          fontFamily: 'var(--font-mono)',
        }}>
          Runtime supervision for autonomous AI agents
        </span>
      </div>

      {/* Right Controls */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
        {/* Synthetic Fallback / Degraded Mode Badge */}
        {isDegraded && (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              padding: '3px 10px',
              borderRadius: '9999px',
              backgroundColor: 'rgba(234, 179, 8, 0.16)',
              border: '1px solid rgba(234, 179, 8, 0.45)',
              fontSize: '11px',
              fontWeight: '700',
              color: '#eab308',
              letterSpacing: '0.03em',
            }}
            title={fallbackReason || "LLM no disponible o fallo en inferencia. Operando bajo planificador degradado SYNTHETIC_FALLBACK"}
          >
            <AlertTriangle size={12} />
            <span>SYNTHETIC FALLBACK ⚠</span>
          </div>
        )}

        {/* Execution Mode Badge */}
        {getModeBadge()}

        {/* Runtime Active Badge */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: '7px',
          padding: '4px 10px',
          borderRadius: '9999px',
          backgroundColor: runtimeActive ? 'rgba(63, 185, 80, 0.12)' : 'rgba(248, 81, 73, 0.12)',
          border: `1px solid ${runtimeActive ? 'rgba(63, 185, 80, 0.28)' : 'rgba(248, 81, 73, 0.28)'}`,
          fontSize: '11px',
          fontWeight: '500',
          color: runtimeActive ? '#3fb950' : '#f85149',
        }}>
          <span className="pulse-dot" style={{
            backgroundColor: runtimeActive ? '#3fb950' : '#f85149',
            boxShadow: runtimeActive ? '0 0 6px rgba(63, 185, 80, 0.6)' : 'none',
          }} />
          <span>{runtimeActive ? 'Runtime Active' : 'Offline / Standalone'}</span>
        </div>

        {/* Session Selector Pill */}
        <button
          onClick={onOpenSessions}
          title="Ver y seleccionar sesiones registradas"
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '7px',
            padding: '4px 11px',
            borderRadius: '9999px',
            backgroundColor: '#161c26',
            border: '1px solid #242c3b',
            color: '#c9d1d9',
            fontSize: '11px',
            fontWeight: '500',
            cursor: 'pointer',
            transition: 'all 0.15s ease',
            boxShadow: 'var(--shadow-clay-sm)',
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.borderColor = '#384558';
            e.currentTarget.style.backgroundColor = '#1c2330';
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.borderColor = '#242c3b';
            e.currentTarget.style.backgroundColor = '#161c26';
          }}
        >
          <Cpu size={12} style={{ color: '#8b949e' }} />
          <span>Session #{sessionId}</span>
          <ChevronDown size={12} style={{ color: '#8b949e' }} />
        </button>

        {/* New Clean Session Button */}
        <button
          onClick={onNewSession}
          title="Crear una nueva sesión limpia (grafo y chat reseteados sin historial previo)"
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '5px',
            padding: '4px 10px',
            borderRadius: '9999px',
            backgroundColor: 'rgba(56, 139, 253, 0.12)',
            border: '1px solid rgba(56, 139, 253, 0.35)',
            color: '#58a6ff',
            fontSize: '11px',
            fontWeight: '600',
            cursor: 'pointer',
            transition: 'all 0.15s ease',
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.backgroundColor = 'rgba(56, 139, 253, 0.25)';
            e.currentTarget.style.borderColor = '#58a6ff';
            e.currentTarget.style.color = '#79c0ff';
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.backgroundColor = 'rgba(56, 139, 253, 0.12)';
            e.currentTarget.style.borderColor = 'rgba(56, 139, 253, 0.35)';
            e.currentTarget.style.color = '#58a6ff';
          }}
        >
          <Plus size={12} />
          <span>Nueva Sesión</span>
        </button>

        {/* API Key / Auth Status Button */}
        {onOpenAuth && (
          <button
            type="button"
            onClick={onOpenAuth}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '5px',
              padding: '4px 10px',
              borderRadius: '9999px',
              backgroundColor: '#161c26',
              border: '1px solid #242c3b',
              fontSize: '11px',
              color: '#8b949e',
              cursor: 'pointer',
              transition: 'all 0.15s ease',
            }}
            title="Configurar Clave Maestra de PRAXEON"
            onMouseEnter={(e) => {
              e.currentTarget.style.borderColor = '#58a6ff';
              e.currentTarget.style.color = '#58a6ff';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.borderColor = '#242c3b';
              e.currentTarget.style.color = '#8b949e';
            }}
          >
            <Key size={12} style={{ color: '#ebb338' }} />
            <span>API Key</span>
          </button>
        )}

        {/* Operator Role / Profile */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
          padding: '4px 10px',
          borderRadius: '9999px',
          backgroundColor: '#161c26',
          border: '1px solid #242c3b',
          fontSize: '11px',
          color: '#8b949e',
        }}>
          <User size={12} style={{ color: '#58a6ff' }} />
          <span style={{ color: '#c9d1d9', fontWeight: '500' }}>{operatorId}</span>
          <span style={{
            fontSize: '9.5px',
            backgroundColor: '#21262d',
            padding: '1px 5px',
            borderRadius: '3px',
            color: '#8b949e',
            textTransform: 'uppercase',
          }}>
            {operatorRole}
          </span>
        </div>
      </div>
    </header>
  );
}

