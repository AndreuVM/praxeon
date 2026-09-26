import React from 'react';
import {
  Activity,
  FolderKanban,
  GitFork,
  Bot,
  Cpu,
  Shield,
  Settings,
} from 'lucide-react';

export default function Sidebar({ activeNav = 'live', onNavSelect }) {
  const navItems = [
    { id: 'live', label: 'Live', icon: Activity },
    { id: 'sessions', label: 'Sessions', icon: FolderKanban },
    { id: 'decisions', label: 'Decisions', icon: GitFork },
    { id: 'agents', label: 'Agents', icon: Bot },
    { id: 'providers', label: 'Providers', icon: Cpu },
    { id: 'security', label: 'Security', icon: Shield },
    { id: 'settings', label: 'Settings', icon: Settings },
  ];

  return (
    <aside style={{
      width: '185px',
      backgroundColor: '#0c0f14',
      borderRight: '1px solid #1a202c',
      display: 'flex',
      flexDirection: 'column',
      justifyContent: 'space-between',
      padding: '16px 10px',
      flexShrink: 0,
      userSelect: 'none',
    }}>
      {/* Top Nav List */}
      <nav style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
        {navItems.map((item) => {
          const Icon = item.icon;
          const isActive = activeNav === item.id;
          return (
            <button
              key={item.id}
              onClick={() => onNavSelect?.(item.id)}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '11px',
                padding: '9px 12px',
                borderRadius: '8px',
                border: isActive ? '1px solid #283244' : '1px solid transparent',
                background: isActive ? '#1a202c' : 'transparent',
                color: isActive ? '#f0f6fc' : '#8b949e',
                fontSize: '12.5px',
                fontWeight: isActive ? '600' : '400',
                cursor: 'pointer',
                textAlign: 'left',
                transition: 'all 0.15s ease',
                boxShadow: isActive ? 'var(--shadow-clay-sm)' : 'none',
              }}
              onMouseEnter={(e) => {
                if (!isActive) {
                  e.currentTarget.style.background = '#141922';
                  e.currentTarget.style.color = '#c9d1d9';
                }
              }}
              onMouseLeave={(e) => {
                if (!isActive) {
                  e.currentTarget.style.background = 'transparent';
                  e.currentTarget.style.color = '#8b949e';
                }
              }}
            >
              <Icon
                size={16}
                style={{
                  color: isActive ? '#f0f6fc' : '#6b7280',
                  strokeWidth: isActive ? 2.2 : 1.8,
                }}
              />
              <span>{item.label}</span>
            </button>
          );
        })}
      </nav>

      {/* Bottom Footer */}
      <div style={{
        padding: '12px 10px',
        borderTop: '1px solid #1a202c',
        display: 'flex',
        flexDirection: 'column',
        gap: '4px',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <span style={{
            width: '6px',
            height: '6px',
            borderRadius: '50%',
            backgroundColor: '#8b949e',
            display: 'inline-block',
          }} />
          <span style={{ fontSize: '11px', color: '#8b949e', fontWeight: '500' }}>
            PRAXEON v1.0.0
          </span>
        </div>
        <span style={{ fontSize: '10.5px', color: '#6b7280', paddingLeft: '12px' }}>
          Open source
        </span>
      </div>
    </aside>
  );
}

