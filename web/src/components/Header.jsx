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
      <div style={{ display: 'flex', alignItems: 'center', gap: '20px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <span style={{
            fontSize: '15px',
            fontWeight: '800',
            letterSpacing: '0.14em',
            color: '#f0f6fc',
            fontFamily: 'var(--font-sans)',
          }}>
            PRAXEON
          </span>
        </div>

        <span style={{
          fontSize: '12px',
          color: '#8b949e',
          fontWeight: '400',
          letterSpacing: '0.02em',
          borderLeft: '1px solid #202735',
          paddingLeft: '18px',
          fontFamily: 'var(--font-mono)',
        }}>
          Runtime supervision for autonomous AI agents
        </span>
      </div>

      {/* Right Controls */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
        {/* Runtime Active Badge */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: '7px',
          padding: '4px 12px',
          borderRadius: '9999px',
          backgroundColor: runtimeActive ? 'rgba(63, 185, 80, 0.12)' : 'rgba(248, 81, 73, 0.12)',
          border: `1px solid ${runtimeActive ? 'rgba(63, 185, 80, 0.28)' : 'rgba(248, 81, 73, 0.28)'}`,
          fontSize: '11.5px',
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
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            padding: '5px 12px',
            borderRadius: '9999px',
            backgroundColor: '#161c26',
            border: '1px solid #242c3b',
            color: '#c9d1d9',
            fontSize: '11.5px',
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
          <Cpu size={13} style={{ color: '#8b949e' }} />
          <span>Session #{sessionId}</span>
          <ChevronDown size={13} style={{ color: '#8b949e' }} />
        </button>

        {/* User Icon */}
        <div style={{
          width: '28px',
          height: '28px',
          borderRadius: '50%',
          backgroundColor: '#161c26',
          border: '1px solid #242c3b',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: '#8b949e',
          boxShadow: 'var(--shadow-clay-sm)',
        }}>
          <User size={14} />
        </div>
      </div>
    </header>
  );
}

