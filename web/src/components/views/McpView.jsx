import React, { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, ChevronDown, ChevronRight, Pencil, Plus, Power, RefreshCw, RotateCw, ShieldCheck, Trash2, X } from 'lucide-react';
import * as api from '../../services/api';
import { parseEnv } from '../../utils/mcpForm';
import { confirmDialog } from '../Dialogs';
import { ViewPage, ViewHeader, EmptyState } from '../ui';

const STATUS = {
  disabled: { label: 'Desactivado', color: 'var(--muted)' },
  stopped: { label: 'Detenido', color: 'var(--muted)' },
  running: { label: 'Activo', color: 'var(--ok)' },
  pending_approval: { label: 'Pendiente de aprobar', color: 'var(--accent)' },
  changed: { label: 'Cambió: reaprobar', color: 'var(--bad)' },
  error: { label: 'Error', color: 'var(--bad)' },
};

const NAME_RE = /^[a-z][a-z0-9_]{0,31}$/;
const input = {
  width: '100%', backgroundColor: 'var(--bg)', border: '1px solid var(--line)', borderRadius: '9px',
  padding: '7px 10px', fontSize: '12.5px', color: 'var(--text)', fontFamily: 'inherit',
};
const label = { fontSize: 'var(--fs-secondary)', color: 'var(--muted)', display: 'block', marginBottom: '4px' };
const card = { backgroundColor: 'var(--surface)', borderRadius: '9px', border: '1px solid var(--line)', padding: '16px' };

const tri = (value) => (value === true ? 'yes' : value === false ? 'no' : '');
const fromTri = (value) => (value === 'yes' ? true : value === 'no' ? false : null);

// ---- formulario de alta / edición -------------------------------------------------------------

function emptyForm() {
  return {
    mode: 'new', kind: 'local', name: '', command: '', argsText: '', envText: '', cwd: '', timeout: 60, trustHints: false, envKeys: [],
    url: '', shownUrl: '', headersText: '', headerKeys: [], transport: 'auto',
  };
}

function formFromServer(server) {
  return {
    mode: 'edit',
    kind: server.kind === 'remote' ? 'remote' : 'local',
    name: server.name,
    command: server.command || '',
    // la URL completa no se devuelve (puede llevar el token): vacío significa «conservar la guardada»
    url: '',
    shownUrl: server.url || '',
    headersText: (server.header_keys || []).map((k) => `${k}=`).join('\n'),
    headerKeys: server.header_keys || [],
    transport: server.transport || 'auto',
    argsText: (server.args || []).join('\n'),
    // los valores no se devuelven nunca: se muestran las claves vacías y vacío significa «conservar el guardado»
    envText: (server.env_keys || []).map((k) => `${k}=`).join('\n'),
    cwd: server.cwd || '',
    timeout: server.timeout_s || 60,
    trustHints: Boolean(server.trust_hints),
    envKeys: server.env_keys || [],
  };
}

function ServerForm({ form, setForm, onSubmit, onCancel, busy, error }) {
  const set = (patch) => setForm({ ...form, ...patch });
  const nameOk = form.mode === 'edit' || NAME_RE.test(form.name) && !form.name.includes('__');
  const remote = form.kind === 'remote';
  const target = remote ? (form.mode === 'edit' || form.url.trim()) : form.command.trim();
  const canSave = nameOk && target && !busy;
  return (
    <div style={{ ...card, display: 'flex', flexDirection: 'column', gap: '12px', borderColor: 'var(--line)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h3 style={{ fontSize: 'var(--fs-chat)', fontWeight: 600 }}>
          {form.mode === 'new' ? 'Nuevo servidor MCP' : `Editar «${form.name}»`}
        </h3>
        <button className="btn btn-secondary" onClick={onCancel} style={{ padding: '4px 8px' }} aria-label="Cerrar formulario">
          <X size={14} />
        </button>
      </div>

      {form.mode === 'new' && (
        <div>
          <label style={label}>Nombre (minúsculas, dígitos y «_»; sin «__»)</label>
          <input style={{ ...input, borderColor: form.name && !nameOk ? 'var(--bad)' : 'var(--line)' }} value={form.name}
            onChange={(e) => set({ name: e.target.value })} placeholder="github" />
        </div>
      )}
      {form.mode === 'new' && (
        <div role="radiogroup" aria-label="Tipo de servidor" style={{ display: 'flex', gap: '16px', fontSize: '12.5px', color: 'var(--text)' }}>
          <label style={{ display: 'flex', gap: '6px', alignItems: 'center' }}>
            <input type="radio" name="mcp-kind" checked={!remote} onChange={() => set({ kind: 'local' })} /> Local (comando en esta máquina)
          </label>
          <label style={{ display: 'flex', gap: '6px', alignItems: 'center' }}>
            <input type="radio" name="mcp-kind" checked={remote} onChange={() => set({ kind: 'remote' })} /> Remoto (URL)
          </label>
        </div>
      )}
      {remote && (
        <>
          <div>
            <label style={label}>URL del servidor (SSE o HTTP streamable)</label>
            <input style={input} value={form.url} onChange={(e) => set({ url: e.target.value })} autoComplete="off" spellCheck={false}
              placeholder={form.mode === 'edit' ? `${form.shownUrl} (vacío = conservar la guardada)` : 'https://servidor.ejemplo.com/mcp/sse?token=${MI_TOKEN}'} />
            <span style={{ fontSize: '11px', color: 'var(--muted)' }}>
              El token no lo escribas literal: usa «${'{'}VARIABLE{'}'}» y define la variable en tu entorno o en el .env de PRAXEON. La URL se guarda sin mostrarse nunca en pantalla.
            </span>
          </div>
          <div>
            <label style={label}>Cabeceras HTTP (CLAVE=valor, una por línea; p. ej. Authorization=Bearer ${'{'}MI_TOKEN{'}'})</label>
            <textarea style={{ ...input, minHeight: '48px', fontFamily: 'var(--font-mono)' }} value={form.headersText}
              onChange={(e) => set({ headersText: e.target.value })} autoComplete="off" spellCheck={false} />
            {form.mode === 'edit' && form.headerKeys.length > 0 && (
              <span style={{ fontSize: '11px', color: 'var(--muted)' }}>Deja el valor vacío para conservar el guardado (nunca se muestra).</span>
            )}
          </div>
          <div>
            <label style={label}>Transporte</label>
            <select style={input} value={form.transport} onChange={(e) => set({ transport: e.target.value })} aria-label="Transporte">
              <option value="auto">Automático (una URL acabada en /sse es SSE; si no, HTTP streamable)</option>
              <option value="sse">SSE clásico</option>
              <option value="streamable_http">HTTP streamable</option>
            </select>
          </div>
        </>
      )}
      {!remote && (
        <>
      <div>
        <label style={label}>Comando</label>
        <input style={input} value={form.command} onChange={(e) => set({ command: e.target.value })} placeholder="npx, uvx, python…" />
      </div>
      <div>
        <label style={label}>Argumentos (uno por línea)</label>
        <textarea style={{ ...input, minHeight: '64px', fontFamily: 'var(--font-mono)' }} value={form.argsText}
          onChange={(e) => set({ argsText: e.target.value })} placeholder={'-y\n@modelcontextprotocol/server-filesystem\nC:/mi/carpeta'} />
      </div>
      <div>
        <label style={label}>
          Variables de entorno (CLAVE=valor, una por línea; «CLAVE=${'{'}VARIABLE{'}'}» toma el valor del entorno de PRAXEON)
        </label>
        <textarea style={{ ...input, minHeight: '56px', fontFamily: 'var(--font-mono)' }} value={form.envText}
          onChange={(e) => set({ envText: e.target.value })} placeholder="GITHUB_TOKEN=${MI_TOKEN_GITHUB}" />
        {form.mode === 'edit' && form.envKeys.length > 0 && (
          <span style={{ fontSize: '11px', color: 'var(--muted)' }}>Deja el valor vacío para conservar el guardado (nunca se muestra).</span>
        )}
      </div>
      <div>
        <label style={label}>Carpeta de trabajo (opcional)</label>
        <input style={input} value={form.cwd} onChange={(e) => set({ cwd: e.target.value })} />
      </div>
        </>
      )}
      <div style={{ maxWidth: '220px' }}>
        <label style={label}>Tiempo máx. por llamada (s)</label>
        <input style={input} type="number" min="1" value={form.timeout} onChange={(e) => set({ timeout: e.target.value })} />
      </div>
      <label style={{ display: 'flex', gap: '8px', alignItems: 'flex-start', fontSize: '12.5px', color: 'var(--text)' }}>
        <input type="checkbox" checked={form.trustHints} onChange={(e) => set({ trustHints: e.target.checked })} />
        <span>
          Fiarme de lo que el servidor anuncia sobre sus herramientas (solo lectura / destructiva).
          <span style={{ display: 'block', fontSize: '11px', color: 'var(--muted)' }}>
            Sin esto, toda herramienta MCP pide confirmación humana. Lo anuncia el propio servidor, así que actívalo solo con servidores de confianza.
          </span>
        </span>
      </label>
      {form.mode === 'edit' && (
        <div style={{ fontSize: '11px', color: 'var(--accent)' }}>
          Si cambias lo que se ejecuta o a qué te conectas (comando, argumentos, entorno, carpeta, URL, cabeceras o transporte), el servidor se desactivará y habrá que revisarlo y aprobarlo de nuevo.
        </div>
      )}
      {error && <div style={{ fontSize: '12.5px', color: 'var(--bad)' }}>{error}</div>}
      <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
        <button className="btn btn-secondary" onClick={onCancel}>Cancelar</button>
        <button className="btn btn-primary" onClick={onSubmit} disabled={!canSave}>Guardar</button>
      </div>
    </div>
  );
}

// ---- herramientas de un servidor ---------------------------------------------------------------

function ToolRow({ tool, onOverride }) {
  const ov = tool.override || {};
  const hints = tool.annotations || {};
  const update = (patch) => onOverride(tool.name, { risk: ov.risk ?? null, read_only: ov.read_only ?? null, confirm: ov.confirm ?? null, disabled: Boolean(ov.disabled), ...patch });
  const small = { ...input, padding: '3px 6px', width: 'auto', fontSize: '11px' };
  return (
    <div style={{ borderTop: '1px solid var(--line)', padding: '10px 0', display: 'flex', flexDirection: 'column', gap: '6px' }}>
      <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' }}>
        <code style={{ fontSize: '12.5px', color: tool.exposed ? 'var(--ok)' : 'var(--muted)' }} title="Nombre con el que lo ve el agente">{tool.exposed_as}</code>
        <span style={{ fontSize: '11px', color: tool.exposed ? 'var(--ok)' : 'var(--muted)' }}>
          {tool.exposed ? 'expuesta al agente' : ov.disabled ? 'desactivada' : 'no expuesta'}
        </span>
        {hints.readOnlyHint === true && <span className="badge" title="Lo anuncia el servidor; no se cree salvo que lo permitas">anuncia solo lectura</span>}
        {hints.destructiveHint === true && <span className="badge badge-danger" title="Lo anuncia el servidor">anuncia destructiva</span>}
      </div>
      {tool.description && <div style={{ fontSize: '12.5px', color: 'var(--text)' }}>{tool.description}</div>}
      <code style={{ fontSize: '11px', color: 'var(--muted)' }}>{tool.exposed_as}({tool.signature})</code>
      <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap', alignItems: 'center', fontSize: '11px', color: 'var(--text)' }}>
        <label>Riesgo{' '}
          <select style={small} value={ov.risk || ''} onChange={(e) => update({ risk: e.target.value || null })} aria-label={`Riesgo de ${tool.name}`}>
            <option value="">Auto</option>
            <option value="LOW">Bajo</option>
            <option value="MEDIUM">Medio</option>
            <option value="HIGH">Alto</option>
            <option value="CRITICAL">Crítico</option>
          </select>
        </label>
        <label>Solo lectura{' '}
          <select style={small} value={tri(ov.read_only)} onChange={(e) => update({ read_only: fromTri(e.target.value) })} aria-label={`Solo lectura de ${tool.name}`}>
            <option value="">Auto</option><option value="yes">Sí</option><option value="no">No</option>
          </select>
        </label>
        <label>Pedir confirmación{' '}
          <select style={small} value={tri(ov.confirm)} onChange={(e) => update({ confirm: fromTri(e.target.value) })} aria-label={`Confirmación de ${tool.name}`}>
            <option value="">Auto</option><option value="yes">Sí</option><option value="no">No</option>
          </select>
        </label>
        <label style={{ display: 'flex', gap: '4px', alignItems: 'center' }}>
          <input type="checkbox" checked={Boolean(ov.disabled)} onChange={(e) => update({ disabled: e.target.checked })} /> Desactivar
        </label>
      </div>
    </div>
  );
}

function ServerCard({ server, busy, expanded, onToggle, actions }) {
  const st = STATUS[server.status] || STATUS.stopped;
  const needsApproval = server.status === 'pending_approval' || server.status === 'changed';
  const remote = server.kind === 'remote';
  const argsLine = remote ? server.url : [server.command, ...(server.args || [])].join(' ');
  return (
    <div style={card}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', alignItems: 'flex-start', flexWrap: 'wrap' }}>
        <div style={{ minWidth: 0, flex: 1 }}>
          <button onClick={onToggle} style={{ background: 'none', border: 'none', color: 'var(--text)', display: 'flex', alignItems: 'center', gap: '6px', padding: 0, cursor: 'pointer' }}
            aria-expanded={expanded} aria-label={`Herramientas de ${server.name}`}>
            {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            <span style={{ fontSize: 'var(--fs-chat)', fontWeight: 600 }}>{server.name}</span>
          </button>
          <div style={{ fontSize: '11.5px', color: 'var(--muted)', fontFamily: 'var(--font-mono)', wordBreak: 'break-all', marginTop: '4px' }}>{argsLine}</div>
          {remote && <div style={{ fontSize: '11px', color: 'var(--muted)', marginTop: '2px' }}>Remoto · transporte {server.transport}{server.header_keys?.length > 0 ? ` · cabeceras: ${server.header_keys.join(', ')}` : ''}</div>}
          {server.env_keys?.length > 0 && <div style={{ fontSize: '11px', color: 'var(--muted)', marginTop: '2px' }}>Entorno: {server.env_keys.join(', ')}</div>}
          {(server.warnings || []).map((w) => (
            <div key={w} style={{ fontSize: '11px', color: 'var(--accent)', marginTop: '6px', display: 'flex', gap: '6px' }}>
              <AlertTriangle size={13} style={{ flexShrink: 0, marginTop: '1px' }} /><span>{w}</span>
            </div>
          ))}
        </div>
        <span style={{ fontSize: '11px', fontWeight: 600, color: st.color, border: `1px solid color-mix(in srgb, ${st.color} 45%, transparent)`, borderRadius: '14px', padding: '2px 10px', whiteSpace: 'nowrap' }}>
          {st.label}
        </span>
      </div>

      {server.error && (
        <div style={{ marginTop: '10px', fontSize: '12.5px', color: 'var(--bad)', display: 'flex', gap: '6px' }}>
          <AlertTriangle size={14} style={{ flexShrink: 0 }} /><span style={{ wordBreak: 'break-word' }}>{server.error}</span>
        </div>
      )}

      {needsApproval && (
        <div style={{ marginTop: '10px', padding: '10px 12px', borderRadius: '9px', backgroundColor: 'color-mix(in srgb, var(--accent) 10%, transparent)', border: '1px solid color-mix(in srgb, var(--accent) 35%, transparent)', fontSize: '12.5px', color: 'var(--accent)', display: 'flex', gap: '12px', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap' }}>
          <span>
            {server.status === 'changed'
              ? 'El servidor cambió sus herramientas desde que las aprobaste. No se exponen hasta que las revises de nuevo.'
              : `Revisa las ${server.tools.length} herramientas (despliega la tarjeta) y aprueba para exponerlas a los agentes.`}
          </span>
          <button className="btn btn-primary" disabled={busy || !server.tools_hash} onClick={actions.approve} style={{ padding: '5px 12px' }}>
            <ShieldCheck size={13} /> Aprobar {server.tools.length} herramientas
          </button>
        </div>
      )}

      <div style={{ display: 'flex', gap: '8px', marginTop: '12px', flexWrap: 'wrap' }}>
        {server.enabled ? (
          <button className="btn btn-secondary" disabled={busy} onClick={actions.disable}><Power size={13} /> Desactivar</button>
        ) : (
          <button className="btn btn-secondary" disabled={busy} onClick={actions.enable}><Power size={13} /> Activar y revisar</button>
        )}
        {server.enabled && <button className="btn btn-secondary" disabled={busy} onClick={actions.restart}><RotateCw size={13} /> Reiniciar</button>}
        <button className="btn btn-secondary" disabled={busy} onClick={actions.edit}><Pencil size={13} /> Editar</button>
        <button className="btn btn-danger" disabled={busy} onClick={actions.remove}><Trash2 size={13} /> Eliminar</button>
      </div>

      {expanded && (
        <div style={{ marginTop: '12px' }}>
          {server.tools.length === 0 ? (
            <div style={{ fontSize: '12.5px', color: 'var(--muted)' }}>
              {server.enabled ? 'El servidor no ha anunciado herramientas.' : 'Activa el servidor para ver sus herramientas.'}
            </div>
          ) : (
            server.tools.map((tool) => <ToolRow key={tool.name} tool={tool} onOverride={actions.override} />)
          )}
        </div>
      )}
    </div>
  );
}

// ---- vista --------------------------------------------------------------------------------------

export default function McpView() {
  const [servers, setServers] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');
  const [expanded, setExpanded] = useState({});
  const [form, setForm] = useState(null);
  const [formError, setFormError] = useState('');

  const refresh = useCallback(async () => {
    try {
      setServers(await api.fetchMcpServers());
      setError('');
    } catch (err) {
      setError(err.message);
      setServers((prev) => prev || []);
    }
  }, []);

  useEffect(() => {
    let alive = true;
    api.fetchMcpServers()
      .then((list) => alive && setServers(list))
      .catch((err) => alive && (setError(err.message), setServers([])));
    return () => { alive = false; };
  }, []);

  const run = async (name, fn, { expand = false } = {}) => {
    setBusy(name);
    setError('');
    try {
      await fn();
      if (expand) setExpanded((e) => ({ ...e, [name]: true }));
      await refresh();
    } catch (err) {
      setError(`${name}: ${err.message}`);
    } finally {
      setBusy('');
    }
  };

  const submitForm = async () => {
    const common = { trust_hints: form.trustHints, timeout_s: Number(form.timeout) || 60 };
    const config = form.kind === 'remote'
      ? { ...common, url: form.url.trim() || null, headers: parseEnv(form.headersText, form.headerKeys), transport: form.transport }
      : {
          ...common,
          command: form.command.trim(),
          args: form.argsText.split('\n').map((l) => l.trim()).filter(Boolean),
          env: parseEnv(form.envText, form.envKeys),
          cwd: form.cwd.trim() || null,
        };
    setBusy(form.name);
    setFormError('');
    try {
      await api.saveMcpServer(form.name, config);
      setForm(null);
      await refresh();
    } catch (err) {
      setFormError(err.message);
    } finally {
      setBusy('');
    }
  };

  const actionsFor = (server) => ({
    enable: () => run(server.name, () => api.enableMcpServer(server.name), { expand: true }),
    disable: () => run(server.name, () => api.disableMcpServer(server.name)),
    restart: () => run(server.name, () => api.restartMcpServer(server.name)),
    approve: () => run(server.name, () => api.approveMcpServer(server.name, server.tools_hash)),
    override: (tool, body) => run(server.name, () => api.setMcpToolOverride(server.name, tool, body)),
    edit: () => { setFormError(''); setForm(formFromServer(server)); },
    remove: async () => {
      if (await confirmDialog(`¿Eliminar el servidor «${server.name}»? Sus herramientas dejarán de estar disponibles.`, { confirmLabel: 'Eliminar', danger: true })) {
        run(server.name, () => api.deleteMcpServer(server.name));
      }
    },
  });

  return (
    <ViewPage maxWidth={1000} gap={18}>
      <ViewHeader
        title="Herramientas externas (MCP)"
        description="Cada herramienta pasa por la misma supervisión que las nativas. Un servidor es un programa que se ejecuta con tus permisos: nace desactivado, no expone nada hasta que revisas y apruebas sus herramientas, y si las cambia deja de exponerlas. Las que tienen efectos siempre esperan tu confirmación, incluso en Full Access autónomo."
        actions={
          <>
            <button className="btn btn-secondary" onClick={refresh} disabled={Boolean(busy)}><RefreshCw size={13} /> Actualizar</button>
            <button className="btn btn-primary" onClick={() => { setFormError(''); setForm(emptyForm()); }}><Plus size={13} /> Añadir servidor</button>
          </>
        }
      />

      {error && (
        <div role="alert" style={{ padding: '10px 14px', borderRadius: 'var(--r-ui)', border: '1px solid var(--bad)', color: 'var(--bad)', fontSize: 'var(--fs-secondary)', overflowWrap: 'anywhere' }}>
          {error}
        </div>
      )}

      {form && <ServerForm form={form} setForm={setForm} onSubmit={submitForm} onCancel={() => setForm(null)} busy={Boolean(busy)} error={formError} />}

      {servers === null ? (
        <div style={{ color: 'var(--muted)' }}>Cargando…</div>
      ) : servers.length === 0 && !form ? (
        <EmptyState
          title="Aún no hay servidores"
          actions={<button className="btn btn-primary" onClick={() => { setFormError(''); setForm(emptyForm()); }}><Plus size={13} /> Añadir servidor</button>}
        >
          Indica el comando que arranca el servidor (por ejemplo <code>npx</code> o <code>uvx</code> con el paquete del servidor). Cuando lo actives verás sus herramientas para revisarlas antes de aprobarlas.
        </EmptyState>
      ) : (
        servers.map((server) => (
          <ServerCard key={server.name} server={server} busy={busy === server.name} expanded={Boolean(expanded[server.name])}
            onToggle={() => setExpanded((e) => ({ ...e, [server.name]: !e[server.name] }))} actions={actionsFor(server)} />
        ))
      )}
    </ViewPage>
  );
}
