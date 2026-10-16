import React, { useMemo, useState } from 'react';
import { Layers } from 'lucide-react';
import { Chip, Field } from '../ui';
import {
  CASCADE_PRESETS,
  loadCascadeConfig,
  normalizeCascade,
  saveCascadeConfig,
} from '../../utils/cascadeConfig';

const BACKEND_LABEL = {
  local: 'Local (este equipo)',
  hosted: 'Servicio propio (HTTP)',
  api: 'Nube (API)',
};

const FALLBACK_PROVIDERS = [
  { provider_id: 'laya', name: 'LAYA System-1', supported_backends: ['local', 'hosted'] },
  { provider_id: 'kev', name: 'KEV System-1', supported_backends: ['local', 'hosted'] },
  { provider_id: 'typesafe', name: 'TypeSafe AI', supported_backends: ['api'] },
];

const isRemote = (backend) => backend === 'hosted' || backend === 'api';

function LevelFields({ id, title, level, providers, onChange }) {
  const current = providers.find((p) => p.provider_id === level.provider);
  const backends = current?.supported_backends?.length ? current.supported_backends : ['local'];
  const set = (patch) => onChange({ ...level, ...patch });
  const pickProvider = (value) => {
    const next = providers.find((p) => p.provider_id === value);
    const nextBackends = next?.supported_backends?.length ? next.supported_backends : ['local'];
    set({ provider: value, backend: nextBackends.includes(level.backend) ? level.backend : nextBackends[0] });
  };
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', minWidth: 0 }}>
      <h3 style={{ fontSize: 'var(--fs-ui)', fontWeight: 600 }}>{title}</h3>
      <Field label="Modelo" htmlFor={`${id}-prov`}>
        <select id={`${id}-prov`} value={level.provider} onChange={(e) => pickProvider(e.target.value)} style={{ width: '100%' }}>
          {providers.map((p) => (
            <option key={p.provider_id} value={p.provider_id} disabled={p.available === false}>
              {p.name}{p.available === false ? ' (no instalado)' : ''}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Dónde se ejecuta" htmlFor={`${id}-back`}>
        <select id={`${id}-back`} value={level.backend} onChange={(e) => set({ backend: e.target.value })} style={{ width: '100%' }}>
          {backends.map((b) => (
            <option key={b} value={b}>{BACKEND_LABEL[b] || b}</option>
          ))}
        </select>
      </Field>
      {isRemote(level.backend) && (
        <Field
          label="Endpoint (opcional)"
          htmlFor={`${id}-url`}
          hint="Si lo dejas vacío se usa la URL del servidor (KEV_ENDPOINT_URL, LAYA_ENDPOINT_URL…). Las claves se leen del entorno del servidor, nunca de aquí."
        >
          <input id={`${id}-url`} value={level.endpoint} placeholder="https://…" onChange={(e) => set({ endpoint: e.target.value })} style={{ width: '100%' }} />
        </Field>
      )}
    </div>
  );
}

/** Editor de la cascada de supervisión: nivel 1 (rápido) y nivel 2 (respaldo), local+local o local+nube. */
export default function CascadeEditor({ decisionProviders }) {
  const providers = useMemo(() => {
    const list = (decisionProviders || []).filter((p) => !['cascade', 'mock', 'replay'].includes(p.provider_id));
    return list.length ? list : FALLBACK_PROVIDERS;
  }, [decisionProviders]);

  const [config, setConfig] = useState(() => loadCascadeConfig());
  const [saved, setSaved] = useState(false);
  const update = (patch) => {
    setConfig((c) => normalizeCascade({ ...c, ...patch }));
    setSaved(false);
  };

  const remoteLevels = [config.primary, config.secondary].filter((l) => isRemote(l.backend)).length;
  const mode = remoteLevels === 0 ? 'Local + local' : remoteLevels === 1 ? 'Local + nube (API)' : 'Nube + nube';

  const save = () => {
    setConfig(saveCascadeConfig(config));
    setSaved(true);
  };

  return (
    <section>
      <h2 style={{ fontSize: 'var(--fs-chat)', fontWeight: 600, marginBottom: '4px', display: 'flex', alignItems: 'center', gap: '8px' }}>
        <Layers size={16} strokeWidth={1.8} /> Cascada de supervisión
      </h2>
      <p style={{ color: 'var(--muted)', marginBottom: '12px', maxWidth: '72ch' }}>
        El nivel 1 evalúa cada acción; si su confianza no llega al umbral, la decide el nivel 2. Se usa al elegir «Cascada» como supervisor de una misión.
      </p>
      <div style={{ background: 'var(--surface)', borderRadius: 'var(--r-card)', border: '1px solid var(--line)', padding: '18px', display: 'flex', flexDirection: 'column', gap: '18px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
          {Object.entries(CASCADE_PRESETS).map(([key, preset]) => (
            <button
              key={key}
              type="button"
              className="btn btn-secondary"
              title={preset.hint}
              onClick={() => { setConfig(normalizeCascade(preset.config)); setSaved(false); }}
            >
              {preset.label}
            </button>
          ))}
          <span style={{ flex: 1 }} />
          <Chip tone={remoteLevels === 0 ? 'ok' : undefined}>{mode}</Chip>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: '22px' }}>
          <LevelFields id="casc-1" title="Nivel 1 · decide primero" level={config.primary} providers={providers} onChange={(primary) => update({ primary })} />
          <LevelFields id="casc-2" title="Nivel 2 · respaldo" level={config.secondary} providers={providers} onChange={(secondary) => update({ secondary })} />
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: '22px' }}>
          <Field label={`Umbral del nivel 1 (${config.confidence_threshold.toFixed(2)})`} htmlFor="casc-th1" hint="Por debajo de esta confianza se escala al nivel 2.">
            <input id="casc-th1" type="range" min="0" max="1" step="0.05" value={config.confidence_threshold} onChange={(e) => update({ confidence_threshold: Number(e.target.value) })} style={{ width: '100%' }} />
          </Field>
          <Field label={`Umbral del nivel 2 (${config.secondary_threshold.toFixed(2)})`} htmlFor="casc-th2" hint="Si el nivel 2 tampoco llega, se marca incertidumbre dual y se pide revisión.">
            <input id="casc-th2" type="range" min="0" max="1" step="0.05" value={config.secondary_threshold} onChange={(e) => update({ secondary_threshold: Number(e.target.value) })} style={{ width: '100%' }} />
          </Field>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <button type="button" className="btn btn-primary" onClick={save}>Guardar cascada</button>
          {saved && <span role="status" style={{ color: 'var(--ok)', fontSize: 'var(--fs-secondary)' }}>Guardada: las nuevas misiones con supervisor «Cascada» la usarán.</span>}
        </div>
      </div>
    </section>
  );
}
