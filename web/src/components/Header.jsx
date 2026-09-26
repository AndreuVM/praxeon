import React from 'react';
import { ChevronDown, User, Layers, Cpu, Play } from 'lucide-react';

export default function Header({
  sessionId = '7f3a2c',
  runtimeActive = true,
  onOpenSessions,
  onOpenPropose,
}) {
  return (
    <header style={{
      height: '52px',
      backgroundColor: '#0b0f17',
      borderBottom: '1px solid #1a2333',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      padding: '0 18px',
      userSelect: 'none',
      zIndex: 20,
    }}>
      {/* Brand & Subtitle */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '20px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <span style={{
            fontSize: '15px',
            fontWeight: '800',
            letterSpacing: '0.14em',
            color: '#ffffff',
            fontFamily: 'var(--font-sans)',
          }}>
            PRAXEON
          </span>
        </div>

        <span style={{
          fontSize: '12px',
          color: '#64748b',
          fontWeight: '400',
          letterSpacing: '0.02em',
          borderLeft: '1px solid #1e293b',
          paddingLeft: '18px',
        }}>
          Runtime supervision for autonomous AI agents
        </span>
      </div>

      {/* Right Controls */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
        {/* Propose Action Quick Trigger */}
        <button
          onClick={onOpenPropose}
          className="btn btn-secondary"
          style={{
            fontSize: '11.5px',
            padding: '5px 11px',
            backgroundColor: '#162235',
            borderColor: '#25354e',
            color: '#38bdf8',
          }}
          title="Proponer nueva acción al runtime de supervisión"
        >
          <Play size={12} style={{ fill: '#38bdf8' }} />
          Propose Action
        </button>

        {/* Runtime Active Badge */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: '7px',
          padding: '4px 12px',
          borderRadius: '9999px',
          backgroundColor: runtimeActive ? 'rgba(16, 185, 129, 0.12)' : 'rgba(239, 68, 68, 0.12)',
          border: `1px solid ${runtimeActive ? 'rgba(16, 185, 129, 0.28)' : 'rgba(239, 68, 68, 0.28)'}`,
          fontSize: '11.5px',
          fontWeight: '500',
          color: runtimeActive ? '#34d399' : '#f87171',
        }}>
          <span className="pulse-dot" style={{
            backgroundColor: runtimeActive ? '#10b981' : '#ef4444',
            boxShadow: runtimeActive ? '0 0 8px rgba(16, 185, 129, 0.6)' : 'none',
          }} />
          <span>{runtimeActive ? 'Runtime Active' : 'Offline / Standalone'}</span>
        </div>

        {/* Session Selector Pill */}
        <button
          onClick={onOpenSessions}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            padding: '4px 12px',
            borderRadius: '9999px',
            backgroundColor: '#151d2c',
            border: '1px solid #243248',
            color: '#cbd5e1',
            fontSize: '11.5px',
            fontWeight: '500',
            cursor: 'pointer',
            transition: 'all 0.15s ease',
          }}
          onMouseEnter={(e) => (e.currentTarget.style.borderColor = '#3b82f6')}
          onMouseLeave={(e) => (e.currentTarget.style.borderColor = '#243248')}
        >
          <Cpu size={13} style={{ color: '#94a3b8' }} />
          <span>Session #{sessionId}</span>
          <ChevronDown size={13} style={{ color: '#64748b' }} />
        </button>

        {/* User Icon */}
        <div style={{
          width: '28px',
          height: '28px',
          borderRadius: '50%',
          backgroundColor: '#182234',
          border: '1px solid #27374d',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: '#94a3b8',
        }}>
          <User size={14} />
        </div>
      </div>
    </header>
  );
}
