import React from 'react';
import {
  Activity,
  FolderKanban,
  FileCode2,
  GitFork,
  Bot,
  Workflow,
  Cpu,
  Plug,
  Shield,
  Settings,
  FolderOpen,
  Search,
  Sparkles,
  UserRound,
} from 'lucide-react';

// Todas las vistas son accesibles directamente desde el riel (RD-5.2)
export const NAV_ITEMS = [
  { id: 'live', label: 'Conversación', icon: Activity },
  { id: 'files', label: 'Archivos', icon: FileCode2 },
  { id: 'sessions', label: 'Sesiones', icon: FolderKanban },
  { id: 'decisions', label: 'Decisiones', icon: GitFork },
  { id: 'agents', label: 'Agentes', icon: Bot },
  { id: 'workflows', label: 'Flujos', icon: Workflow },
  { id: 'providers', label: 'Proveedores', icon: Cpu },
  { id: 'mcp', label: 'Herramientas (MCP)', icon: Plug },
  { id: 'skills', label: 'Skills', icon: Sparkles },
  { id: 'security', label: 'Seguridad', icon: Shield },
  { id: 'settings', label: 'Ajustes', icon: Settings },
];

const wsName = (p) => (p || '').split(/[\\/]/).filter(Boolean).pop() || p;

export default function Rail({
  active = 'live',
  onSelect,
  workspace = null,
  workspaceMismatch = false,
  workspaceOpen = false,
  onOpenWorkspace,
  onOpenPalette,
  onOpenAccount,
  operatorId = 'operator_admin',
  operatorRole = 'operator',
  pendingApprovals = 0,
}) {
  const main = NAV_ITEMS.filter((i) => i.id !== 'settings');
  const settings = NAV_ITEMS.find((i) => i.id === 'settings');
  const wsLabel = workspace?.path ? `Workspace: ${wsName(workspace.path)}` : 'Elegir workspace';

  const NavButton = ({ item }) => {
    const Icon = item.icon;
    return (
      <button
        type="button"
        className={`rail-btn${active === item.id ? ' on' : ''}`}
        data-label={item.label}
        aria-label={item.label}
        aria-current={active === item.id ? 'page' : undefined}
        onClick={() => onSelect?.(item.id)}
      >
        <Icon size={16} strokeWidth={1.6} />
        {item.id === 'live' && pendingApprovals > 0 && active !== 'live' && <span className="rail-dot" />}
      </button>
    );
  };

  return (
    <nav className="rail" aria-label="Navegación principal">
      <button
        type="button"
        className={`rail-btn${workspaceOpen ? ' on' : ''}`}
        data-label={wsLabel}
        aria-label={`Cambiar workspace. ${wsLabel}`}
        aria-haspopup="dialog"
        aria-expanded={workspaceOpen}
        onClick={onOpenWorkspace}
      >
        <FolderOpen size={16} strokeWidth={1.6} />
        {workspaceMismatch && <span className="rail-dot bad" />}
      </button>
      <span className="rail-sep" />

      {main.map((item) => (
        <NavButton key={item.id} item={item} />
      ))}

      <span className="rail-sp" />

      <button
        type="button"
        className="rail-btn"
        data-label="Buscar y ejecutar acciones (Ctrl K)"
        aria-label="Buscar y ejecutar acciones"
        onClick={onOpenPalette}
      >
        <Search size={16} strokeWidth={1.6} />
      </button>
      {settings && <NavButton item={settings} />}
      <button
        type="button"
        className="rail-btn"
        data-label={`${operatorId} (${operatorRole}) · Clave de API`}
        aria-label={`Operador ${operatorId}, rol ${operatorRole}. Configurar clave de API`}
        onClick={onOpenAccount}
      >
        <UserRound size={16} strokeWidth={1.6} />
      </button>
    </nav>
  );
}
