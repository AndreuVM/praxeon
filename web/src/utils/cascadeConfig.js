// Configuración de la cascada de supervisión (nivel 1 → nivel 2). Se guarda en el navegador y se envía como
// `decision_model` al iniciar una misión con el supervisor «cascade». No contiene secretos: las claves y tokens
// de los modelos por API se leen de las variables de entorno del servidor (KEV_AUTH_TOKEN, TYPESAFE_API_KEY…).

const KEY = 'praxeon_cascade_v1';

export const CASCADE_PRESETS = {
  localLocal: {
    label: 'Local + local',
    hint: 'LAYA decide y, si no está seguro, KEV (ambos en este equipo).',
    config: {
      primary: { provider: 'laya', backend: 'local', endpoint: '' },
      secondary: { provider: 'kev', backend: 'local', endpoint: '' },
      confidence_threshold: 0.5,
      secondary_threshold: 0.5,
    },
  },
  localCloud: {
    label: 'Local + nube (API)',
    hint: 'LAYA decide y, si no está seguro, escala a TypeSafe AI por API.',
    config: {
      primary: { provider: 'laya', backend: 'local', endpoint: '' },
      secondary: { provider: 'typesafe', backend: 'api', endpoint: '' },
      confidence_threshold: 0.5,
      secondary_threshold: 0.5,
    },
  },
};

export const DEFAULT_CASCADE = CASCADE_PRESETS.localLocal.config;

const clamp = (v, fallback) => {
  const n = Number(v);
  return Number.isFinite(n) ? Math.min(1, Math.max(0, n)) : fallback;
};

function level(raw, fallback) {
  const l = raw && typeof raw === 'object' ? raw : {};
  return {
    provider: typeof l.provider === 'string' && l.provider ? l.provider : fallback.provider,
    backend: typeof l.backend === 'string' && l.backend ? l.backend : fallback.backend,
    endpoint: typeof l.endpoint === 'string' ? l.endpoint : '',
  };
}

export function normalizeCascade(raw) {
  const c = raw && typeof raw === 'object' ? raw : {};
  return {
    primary: level(c.primary, DEFAULT_CASCADE.primary),
    secondary: level(c.secondary, DEFAULT_CASCADE.secondary),
    confidence_threshold: clamp(c.confidence_threshold, DEFAULT_CASCADE.confidence_threshold),
    secondary_threshold: clamp(c.secondary_threshold, DEFAULT_CASCADE.secondary_threshold),
  };
}

export function loadCascadeConfig() {
  try {
    return normalizeCascade(JSON.parse(localStorage.getItem(KEY) || 'null'));
  } catch {
    return normalizeCascade(null);
  }
}

export function saveCascadeConfig(config) {
  const clean = normalizeCascade(config);
  try {
    localStorage.setItem(KEY, JSON.stringify(clean));
  } catch {
    // sin almacenamiento disponible: la configuración solo vale para esta sesión
  }
  return clean;
}

const levelPayload = (l) => ({
  provider: l.provider,
  model_id: l.provider,
  backend: l.backend,
  ...(l.endpoint?.trim() ? { endpoint: l.endpoint.trim() } : {}),
});

/** `decision_model` que entiende el backend para el proveedor «cascade». */
export function buildCascadeDecisionModel(config = loadCascadeConfig()) {
  const c = normalizeCascade(config);
  return {
    provider: 'cascade',
    model_id: 'cascade',
    custom_params: {
      primary: levelPayload(c.primary),
      secondary: levelPayload(c.secondary),
      confidence_threshold: c.confidence_threshold,
      secondary_threshold: c.secondary_threshold,
    },
  };
}
