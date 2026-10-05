import React from 'react';
import {
  Activity,
  FolderKanban,
  GitFork,
  Bot,
  Workflow,
  Cpu,
  Shield,
  Settings,
} from 'lucide-react';

export default function TopNav({
  activeNav = 'live',
  onNavSelect,
  session,
  currentDecisionCount = 0,
}) {
  const navItems = [
    { id: 'live', label: 'Live Canvas', icon: Activity, badge: null },
    { id: 'sessions', label: 'Sessions', icon: FolderKanban, badge: null },
    { id: 'decisions', label: 'Decisions', icon: GitFork, badge: currentDecisionCount > 0 ? currentDecisionCount : null },
    { id: 'agents', label: 'Agents', icon: Bot, badge: null },
    { id: 'workflows', label: 'Workflows', icon: Workflow, badge: 'Visual' },
    { id: 'providers', label: 'Providers', icon: Cpu, badge: null },
    { id: 'security', label: 'Security', icon: Shield, badge: 'Hardened' },
    { id: 'settings', label: 'Settings', icon: Settings, badge: null },
  ];

  return (
    <nav
      aria-label="Navegación de vistas principales"
      style={{
        height: '38px',
        backgroundColor: '#0c0f14',
        borderBottom: '1px solid #1a202c',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'flex-start',
        padding: '0 12px',
        userSelect: 'none',
        flexShrink: 0,
        zIndex: 15,
      }}
    >
      {/* Horizontal Tabs Bar */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '4px',
          height: '100%',
          overflowX: 'auto',
          scrollbarWidth: 'none',
        }}
      >
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
                gap: '7px',
                height: '30px',
                padding: '0 12px',
                borderRadius: '6px',
                border: 'none',
                background: isActive ? 'rgba(56, 189, 248, 0.12)' : 'transparent',
                color: isActive ? '#f0f6fc' : '#8b949e',
                fontSize: '12px',
                fontWeight: isActive ? '600' : '400',
                cursor: 'pointer',
                whiteSpace: 'nowrap',
                transition: 'all 0.15s ease',
                position: 'relative',
                boxShadow: isActive ? 'inset 0 0 0 1px rgba(56, 189, 248, 0.35)' : 'none',
              }}
              onMouseEnter={(e) => {
                if (!isActive) {
                  e.currentTarget.style.background = '#151b26';
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
                size={14}
                style={{
                  color: isActive ? '#38bdf8' : '#6b7280',
                  strokeWidth: isActive ? 2.2 : 1.8,
                }}
              />
              <span>{item.label}</span>

              {item.badge && (
                <span
                  style={{
                    fontSize: '9.5px',
                    padding: '1px 5px',
                    borderRadius: '10px',
                    fontWeight: '700',
                    backgroundColor: item.id === 'security'
                      ? 'rgba(63, 185, 80, 0.15)'
                      : 'rgba(56, 189, 248, 0.2)',
                    color: item.id === 'security' ? '#3fb950' : '#38bdf8',
                    border: `1px solid ${item.id === 'security' ? 'rgba(63, 185, 80, 0.35)' : 'rgba(56, 189, 248, 0.3)'}`,
                    lineHeight: '1',
                  }}
                >
                  {item.badge}
                </span>
              )}

              {/* Active Bottom Glow Line */}
              {isActive && (
                <div
                  style={{
                    position: 'absolute',
                    bottom: '-4px',
                    left: '15%',
                    right: '15%',
                    height: '2px',
                    borderRadius: '2px',
                    backgroundColor: '#38bdf8',
                    boxShadow: '0 0 8px rgba(56, 189, 248, 0.8)',
                  }}
                />
              )}
            </button>
          );
        })}
      </div>
    </nav>
  );
}
