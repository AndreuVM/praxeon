import React, { useCallback, useEffect, useState } from 'react';
import { Globe, Check } from 'lucide-react';
import * as api from '../services/api';

const card = { backgroundColor: 'var(--surface)', borderRadius: '9px', border: '1px solid var(--line)', padding: '24px', display: 'flex', flexDirection: 'column', gap: '18px' };
const input = { width: '100%', padding: '10px 14px', borderRadius: '9px', backgroundColor: 'var(--bg)', border: '1px solid var(--line)', color: 'var(--text)', fontSize: '12.5px', outline: 'none', boxSizing: 'border-box' };
const hint = { fontSize: '12.5px', color: 'var(--muted)', lineHeight: 1.5, margin: 0 };

/** Acceso a internet de los agentes (web_search con Brave y web_fetch). Se guarda en el servidor, aparte del resto de ajustes. */
export default function WebAccessPanel() {
  const [cfg, setCfg] = useState(null);
  const [domains, setDomains] = useState('');
  const [key, setKey] = useState('');
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const data = await api.fetchWebSettings();
      setCfg(data);
      setDomains((data.allowed_domains || []).join('\n'));
    } catch (e) {
      setMsg({ ok: false, text: e.message });
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const save = async (patch, okText) => {
    setBusy(true);
    try {
      const data = await api.saveWebSettings(patch);
      setCfg(data);
      setDomains((data.allowed_domains || []).join('\n'));
      setMsg({ ok: true, text: okText });
    } catch (e) {
      setMsg({ ok: false, text: e.message });
    } finally {
      setBusy(false);
    }
  };

  const test = async () => {
    setBusy(true);
    try {
      await api.testWebSearch();
      setMsg({ ok: true, text: 'La búsqueda funciona: Brave respondió correctamente.' });
    } catch (e) {
      setMsg({ ok: false, text: e.message });
    } finally {
      setBusy(false);
    }
  };

  if (!cfg) return <div style={card}>{msg ? <span role="alert" style={{ color: 'var(--bad)' }}>{msg.text}</span> : 'Cargando…'}</div>;

  return (
    <div style={card}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <Globe size={16} style={{ color: 'var(--accent)' }} />
        <strong style={{ fontSize: 13 }}>Acceso a internet de los agentes</strong>
      </div>
      <p style={hint}>
        Solo lectura: <code>web_search</code> (busca con Brave) y <code>web_fetch</code> (lee el texto de una página). Sirve para dar datos actuales como
        contexto; lo que se lee nunca se trata como instrucciones ni concede permisos. En <b>Full Access</b> se puede leer cualquier dominio público; en los
        demás modos, solo los dominios de la lista. Nunca se accede a localhost ni a redes privadas.
      </p>

      <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}>
        <input type="checkbox" checked={cfg.enabled} disabled={busy} onChange={(e) => save({ enabled: e.target.checked }, e.target.checked ? 'Acceso a internet activado.' : 'Acceso a internet desactivado.')} />
        Permitir que los agentes usen internet
      </label>

      <div>
        <label htmlFor="web-brave-key" style={{ fontSize: 13, fontWeight: 600 }}>Clave de Brave Search</label>
        <p style={{ ...hint, margin: '4px 0 8px' }}>
          Necesaria para <code>web_search</code> (<code>web_fetch</code> funciona sin ella). {cfg.has_key
            ? `Hay una clave configurada${cfg.key_source === 'env' ? ' (variable de entorno BRAVE_API_KEY)' : ''}.`
            : 'Aún no hay clave: sin ella los agentes solo pueden leer páginas cuya URL ya conozcan.'}
        </p>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <input id="web-brave-key" type="password" autoComplete="off" style={{ ...input, flex: 1, minWidth: 220, fontFamily: 'var(--font-mono)' }} value={key} placeholder={cfg.has_key ? '•••••••• (déjalo vacío para conservarla)' : 'BSA…'} onChange={(e) => setKey(e.target.value)} />
          <button type="button" className="btn btn-secondary" disabled={busy || !key.trim()} onClick={async () => { await save({ brave_api_key: key.trim() }, 'Clave guardada.'); setKey(''); }}>Guardar clave</button>
          {cfg.has_key && cfg.key_source === 'settings' && (
            <button type="button" className="btn btn-secondary" disabled={busy} onClick={() => save({ brave_api_key: '' }, 'Clave borrada.')}>Borrar</button>
          )}
          <button type="button" className="btn btn-secondary" disabled={busy || !cfg.has_key} onClick={test}>Probar búsqueda</button>
        </div>
      </div>

      <div>
        <label htmlFor="web-domains" style={{ fontSize: 13, fontWeight: 600 }}>Dominios permitidos fuera de Full Access</label>
        <p style={{ ...hint, margin: '4px 0 8px' }}>Uno por línea (incluye sus subdominios), p. ej. <code>python.org</code>. Vacío: ninguno.</p>
        <textarea id="web-domains" rows={5} style={{ ...input, fontFamily: 'var(--font-mono)', resize: 'vertical' }} value={domains} onChange={(e) => setDomains(e.target.value)} />
        <div style={{ marginTop: 8 }}>
          <button type="button" className="btn btn-secondary" disabled={busy} onClick={() => save({ allowed_domains: domains.split('\n').map((d) => d.trim()).filter(Boolean) }, 'Dominios guardados.')}>Guardar dominios</button>
        </div>
      </div>

      {msg && (
        <div role={msg.ok ? 'status' : 'alert'} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12.5, color: msg.ok ? 'var(--ok)' : 'var(--bad)' }}>
          {msg.ok && <Check size={14} />}{msg.text}
        </div>
      )}
    </div>
  );
}
