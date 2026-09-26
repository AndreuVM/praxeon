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
      backgroundColor: '#0c111a',
      borderRight: '1px solid #1a2333',
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
                border: 'none',
                background: isActive ? '#182232' : 'transparent',
                color: isActive ? '#f8fafc' : '#8595a8',
                fontSize: '12.5px',
                fontWeight: isActive ? '600' : '400',
                cursor: 'pointer',
                textAlign: 'left',
                transition: 'all 0.15s ease',
              }}
              onMouseEnter={(e) => {
                if (!isActive) {
                  e.currentTarget.style.background = '#131b28';
                  e.currentTarget.style.color = '#cbd5e1';
                }
              }}
              onMouseLeave={(e) => {
                if (!isActive) {
                  e.currentTarget.style.background = 'transparent';
                  e.currentTarget.style.color = '#8595a8';
                }
              }}
            >
              <Icon
                size={16}
                style={{
                  color: isActive ? '#38bdf8' : '#64748b',
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
        borderTop: '1px solid #17202f',
        display: 'flex',
        flexDirection: 'column',
        gap: '4px',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <span style={{
            width: '6px',
            height: '6px',
            borderRadius: '50%',
            backgroundColor: '#38bdf8',
            display: 'inline-block',
          }} />
          <span style={{ fontSize: '11px', color: '#94a3b8', fontWeight: '500' }}>
            PRAXEON v1.0.0
          </span>
        </div>
        <span style={{ fontSize: '10.5px', color: '#52627a', paddingLeft: '12px' }}>
          Open source
        </span>
      </div>
    </aside>
  );
}
