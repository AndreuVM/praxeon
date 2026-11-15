import React from 'react';
import { ShieldAlert } from 'lucide-react';
import { agySelection, confirmGroup, toggleGroup } from '../utils/agyTools';

const RISK_LABEL = { bajo: 'Riesgo bajo', medio: 'Riesgo medio', alto: 'Riesgo alto', critico: 'Riesgo crítico' };
const RISK_COLOR = { bajo: 'var(--muted)', medio: 'var(--warn, #d9a441)', alto: 'var(--bad, #e5484d)', critico: 'var(--bad, #e5484d)' };

/** Catálogo de herramientas de agy, con su aviso y una confirmación explícita por grupo. Nada se guarda: se vuelve a pedir cada vez. */
export default function AgyToolsPicker({ config, onChange, disabled = false, catalog }) {
  const { data, loaded } = catalog;
  const selection = agySelection(config);
  if (!loaded) return <div style={{ fontSize: 12, color: 'var(--muted)' }}>Cargando las herramientas de agy…</div>;
  if (!data) return <div style={{ fontSize: 12, color: 'var(--muted)' }}>No se pudieron cargar las herramientas de agy.</div>;

  return (
    <fieldset style={{ border: '1px solid var(--line)', borderRadius: 8, padding: '8px 10px', margin: 0, display: 'flex', flexDirection: 'column', gap: 8 }} disabled={disabled}>
      <legend style={{ fontSize: 'var(--fs-label)', color: 'var(--muted)', padding: '0 4px' }}>Herramientas propias de agy (desactivadas por defecto)</legend>
      <div style={{ fontSize: 12, color: 'var(--muted)' }}>
        agy es un agente con herramientas propias. PRAXEON las bloquea todas salvo las que actives aquí, y las que actives las ejecuta agy por su cuenta, sin pasar por
        la política, el supervisor ni el sandbox de PRAXEON. Son tu decisión: se vuelven a pedir cada vez y no se recuerdan.
      </div>
      {data.groups.map((g) => {
        const on = selection.tools.includes(g.id);
        const confirmed = selection.ack.includes(g.id);
        const unavailable = g.needs_images && data.images_available === false;
        return (
          <div key={g.id} style={{ border: '1px solid var(--line)', borderRadius: 6, padding: '6px 8px', display: 'flex', flexDirection: 'column', gap: 6 }}>
            <label style={{ display: 'flex', alignItems: 'flex-start', gap: 8, cursor: unavailable ? 'not-allowed' : 'pointer' }}>
              <input
                type="checkbox"
                checked={on}
                disabled={unavailable}
                onChange={(e) => onChange(toggleGroup(config, g.id, e.target.checked))}
                aria-label={`Activar ${g.title}`}
              />
              <span style={{ flex: 1 }}>
                <strong style={{ fontWeight: 600 }}>{g.title}</strong>
                <span style={{ marginLeft: 8, fontSize: 11, color: RISK_COLOR[g.risk] || 'var(--muted)' }}>{RISK_LABEL[g.risk] || g.risk}</span>
                <span style={{ display: 'block', fontSize: 12, color: 'var(--muted)' }}>{g.summary}</span>
                {unavailable && <span style={{ display: 'block', fontSize: 12, color: 'var(--bad, #e5484d)' }}>Necesita Pillow: instala «praxeon[vision]».</span>}
              </span>
            </label>
            {on && (
              <div role="group" aria-label={`Confirmación de ${g.title}`} style={{ borderTop: '1px solid var(--line)', paddingTop: 6, display: 'flex', flexDirection: 'column', gap: 6 }}>
                <div style={{ fontSize: 12, display: 'flex', gap: 6, alignItems: 'flex-start', color: RISK_COLOR[g.risk] || 'var(--text)' }}>
                  <ShieldAlert size={14} style={{ flex: 'none', marginTop: 1 }} aria-hidden="true" />
                  <span>{g.warning}</span>
                </div>
                <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, cursor: 'pointer' }}>
                  <input type="checkbox" checked={confirmed} onChange={(e) => onChange(confirmGroup(config, g.id, e.target.checked))} aria-label={`Confirmo ${g.title}`} />
                  <span>Entiendo el riesgo y quiero activarlo en esta misión</span>
                </label>
              </div>
            )}
          </div>
        );
      })}
    </fieldset>
  );
}
