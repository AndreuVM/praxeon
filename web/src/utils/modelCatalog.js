// Modelos disponibles por proveedor para el chat. Los de fábrica reflejan PROVIDER_PRESETS (praxeon/agent_llm.py);
// los que añade el usuario se guardan en este navegador/ventana y se pueden quitar.

export const PROVIDERS = [
  { value: 'simulator', label: 'Simulado (offline)' },
  { value: 'groq', label: 'Groq' },
  { value: 'ollama', label: 'Ollama' },
  { value: 'gemini', label: 'Gemini' },
  { value: 'openai', label: 'OpenAI' },
  { value: 'openrouter', label: 'OpenRouter' },
];

export const BUILTIN_MODELS = {
  groq: ['llama-3.3-70b-versatile', 'qwen-2.5-coder-32b', 'deepseek-r1-distill-llama-70b', 'llama-3.1-8b-instant'],
  ollama: ['qwen2.5-coder:7b', 'qwen2.5-coder:1.5b', 'qwen2.5-coder:14b', 'llama3.2:3b', 'deepseek-r1:7b', 'mistral:7b'],
  gemini: ['gemini-3.6-flash', 'gemma-4-26b-a4b-it', 'gemma-4-31b-it'],
  openai: ['gpt-4o-mini', 'gpt-4o'],
  openrouter: ['qwen/qwen-2.5-coder-32b-instruct:free', 'meta-llama/llama-3.3-70b-instruct:free', 'deepseek/deepseek-r1:free'],
};

const CUSTOM_KEY = 'praxeon_custom_models';
const CHOICE_KEY = 'praxeon_model_choice';

function readJson(key) {
  try {
    const value = JSON.parse(localStorage.getItem(key) || '{}');
    return value && typeof value === 'object' ? value : {};
  } catch {
    return {};
  }
}

function writeJson(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* sin almacenamiento: la lista dura lo que dure la ventana */
  }
}

export const hasModels = (provider) => provider !== 'simulator' && Boolean(BUILTIN_MODELS[provider]);

export function customModels(provider) {
  const list = readJson(CUSTOM_KEY)[provider];
  return Array.isArray(list) ? list : [];
}

export function modelsFor(provider, extra = []) {
  const all = [...(BUILTIN_MODELS[provider] || []), ...customModels(provider), ...extra.filter(Boolean)];
  return [...new Set(all)];
}

export function isBuiltin(provider, model) {
  return (BUILTIN_MODELS[provider] || []).includes(model);
}

export function addCustomModel(provider, model) {
  const name = String(model || '').trim();
  if (!name) return false;
  const all = readJson(CUSTOM_KEY);
  const list = Array.isArray(all[provider]) ? all[provider] : [];
  if (!list.includes(name) && !isBuiltin(provider, name)) {
    writeJson(CUSTOM_KEY, { ...all, [provider]: [...list, name] });
  }
  return true;
}

export function removeCustomModel(provider, model) {
  const all = readJson(CUSTOM_KEY);
  const list = Array.isArray(all[provider]) ? all[provider] : [];
  writeJson(CUSTOM_KEY, { ...all, [provider]: list.filter((m) => m !== model) });
}

// Último modelo elegido en cada proveedor (vacío = el predeterminado del proveedor)
export function savedChoice(provider) {
  return String(readJson(CHOICE_KEY)[provider] || '');
}

export function saveChoice(provider, model) {
  writeJson(CHOICE_KEY, { ...readJson(CHOICE_KEY), [provider]: model || '' });
}
