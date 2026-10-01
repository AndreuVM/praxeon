"""Módulo de cliente LLM desacoplado para el Agente Autónomo en PRAXEON.

Permite sustituir o complementar Google Gemini con proveedores locales (Ollama, LM Studio)
o en la nube con tiers gratuitos generosos (Groq, OpenRouter, OpenAI-compatible) mediante la API
estándar de chat completions (usando urllib nativo, sin dependencias externas pesadas).
"""

from abc import ABC, abstractmethod
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple
import urllib.error
import urllib.parse
import urllib.request


PROVIDER_PRESETS: Dict[str, Dict[str, Any]] = {
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "llama-3.3-70b-versatile",
        "description": "Groq Cloud (Tier gratuito ultrarrápido: 30 RPM, 14.400 peticiones/día)",
        "env_key": "GROQ_API_KEY",
        "requires_key": True,
        "recommended_models": [
            "llama-3.3-70b-versatile",
            "qwen-2.5-coder-32b",
            "deepseek-r1-distill-llama-70b",
            "llama-3.1-8b-instant",
        ],
    },
    "ollama": {
        "base_url": "http://127.0.0.1:11434/v1",
        "default_model": "qwen2.5-coder:7b",
        "description": "Ollama Local (100% privado, offline, sin coste ni límites de cuota)",
        "env_key": "OLLAMA_API_KEY",
        "requires_key": False,
        "recommended_models": [
            "qwen2.5-coder:7b",
            "qwen2.5-coder:1.5b",
            "qwen2.5-coder:14b",
            "llama3.2:3b",
            "deepseek-r1:7b",
            "mistral:7b",
        ],
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "qwen/qwen-2.5-coder-32b-instruct:free",
        "description": "OpenRouter (Modelos gratuitos con sufijo :free y catálogo multimodelo)",
        "env_key": "OPENROUTER_API_KEY",
        "requires_key": True,
        "recommended_models": [
            "qwen/qwen-2.5-coder-32b-instruct:free",
            "meta-llama/llama-3.3-70b-instruct:free",
            "deepseek/deepseek-r1:free",
        ],
    },
    "lmstudio": {
        "base_url": "http://localhost:1234/v1",
        "default_model": "local-model",
        "description": "LM Studio Local (Servidor local compatible con OpenAI en http://localhost:1234)",
        "env_key": "LM_STUDIO_API_KEY",
        "requires_key": False,
        "recommended_models": ["local-model"],
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
        "description": "OpenAI API Oficial",
        "env_key": "OPENAI_API_KEY",
        "requires_key": True,
        "recommended_models": ["gpt-4o-mini", "gpt-4o"],
    },
    "gemini": {
        "base_url": None,
        "default_model": "gemini-3.6-flash",
        "description": "Google GenAI / Gemini",
        "env_key": "GEMINI_API_KEY",
        "requires_key": True,
        "recommended_models": [
            "gemini-3.6-flash",
            "gemma-4-26b-a4b-it",
            "gemma-4-31b-it",
        ],
    },
}


class SafeNoAuthForwardRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Manejador de redirección seguro (Finding 5):
    Elimina cabeceras de autorización si una redirección cambia de host o dominio,
    impidiendo la fuga de credenciales a destinos no aprobados.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is not None:
            orig_netloc = urllib.parse.urlparse(req.get_full_url()).netloc.lower()
            new_netloc = urllib.parse.urlparse(newurl).netloc.lower()
            if orig_netloc and new_netloc and orig_netloc != new_netloc:
                for h in ("Authorization", "authorization", "x-api-key", "X-API-Key"):
                    new_req.headers.pop(h, None)
                    new_req.unredirected_hdrs.pop(h, None)
        return new_req


def _is_custom_endpoint(provider_name: str, base_url: Optional[str]) -> bool:
    """Verifica si base_url apunta a un destino distinto al origin oficial del proveedor (Finding 5)."""
    if not base_url:
        return False
    preset = PROVIDER_PRESETS.get(provider_name)
    if not preset or not preset.get("base_url"):
        return True
    user_host = urllib.parse.urlparse(base_url.rstrip("/")).netloc.lower()
    preset_host = urllib.parse.urlparse(preset["base_url"].rstrip("/")).netloc.lower()
    return bool(user_host and preset_host and user_host != preset_host)


# Configurar el opener seguro por defecto para proteger redirecciones
try:
    urllib.request.install_opener(urllib.request.build_opener(SafeNoAuthForwardRedirectHandler()))
except Exception:
    pass


class BaseAgentLLM(ABC):
    """Interfaz base para modelos LLM generativos del Agente Autónomo."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Nombre del proveedor del LLM."""
        pass

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Nombre o identificador del modelo."""
        pass

    @abstractmethod
    def generate(self, messages: List[Dict[str, str]], system_prompt: Optional[str] = None) -> str:
        """Genera una respuesta en formato texto para el turno del agente."""
        pass


class OpenAICompatibleLLM(BaseAgentLLM):
    """Cliente universal compatible con OpenAI (Ollama, LM Studio, Groq, OpenRouter, Mistral, etc.)."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: Optional[str] = None,
        provider_name: str = "openai_compatible",
        timeout: float = 120.0,
        temperature: float = 0.2,
        max_tokens: int = 1024,
    ) -> None:
        clean_url = base_url.rstrip("/")
        if not clean_url.endswith("/v1"):
            clean_url = f"{clean_url}/v1"
        self._endpoint = f"{clean_url}/chat/completions"
        self._base_url = clean_url
        self._model = model
        self._api_key = api_key or "sk-no-key-required"
        self._provider_name = provider_name
        self._timeout = timeout
        self._temperature = temperature
        self._max_tokens = max_tokens

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def endpoint(self) -> str:
        return self._endpoint

    def generate(self, messages: List[Dict[str, str]], system_prompt: Optional[str] = None) -> str:
        payload_messages: List[Dict[str, str]] = []
        if system_prompt:
            payload_messages.append({"role": "system", "content": system_prompt})

        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role not in ("system", "user", "assistant"):
                role = "user"
            payload_messages.append({"role": role, "content": content})

        payload = {
            "model": self._model,
            "messages": payload_messages,
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
            "stream": False,
        }

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Praxeon/1.0.0 (Autonomous Agent Runtime)",
        }
        if self._api_key and self._api_key != "none":
            headers["Authorization"] = f"Bearer {self._api_key}"

        if "openrouter" in self._provider_name.lower() or "openrouter" in self._base_url:
            headers["HTTP-Referer"] = "https://github.com/AndreuVM/praxeon"
            headers["X-Title"] = "PRAXEON Agent Supervisor"

        req_bytes = json.dumps(payload).encode("utf-8")

        # Intentar con endpoint configurado y con fallback de loopback (localhost <-> 127.0.0.1)
        endpoints_to_try = [self._endpoint]
        if "localhost" in self._endpoint:
            endpoints_to_try.append(self._endpoint.replace("localhost", "127.0.0.1"))
        elif "127.0.0.1" in self._endpoint:
            endpoints_to_try.append(self._endpoint.replace("127.0.0.1", "localhost"))

        last_url_err = None
        for ep in endpoints_to_try:
            req = urllib.request.Request(ep, data=req_bytes, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    resp_bytes = resp.read()
                    data = json.loads(resp_bytes.decode("utf-8"))
                    choices = data.get("choices", [])
                    if not choices:
                        return ""
                    msg = choices[0].get("message", {})
                    # Si respondió con un endpoint alternativo, guardarlo para futuras llamadas
                    self._endpoint = ep
                    return (msg.get("content") or "").strip()
            except urllib.error.HTTPError as http_err:
                body = ""
                try:
                    body = http_err.read().decode("utf-8", errors="replace")
                except Exception:
                    pass
                if http_err.code == 429:
                    raise RuntimeError(
                        f"[{self._provider_name}] Límite de cuota excedido (HTTP 429) en {ep}. "
                        f"Detalle: {body}"
                    ) from http_err
                elif http_err.code in (401, 403):
                    raise RuntimeError(
                        f"[{self._provider_name}] Error de autenticación (HTTP {http_err.code}). "
                        f"Verifica la clave de API para {self._provider_name}. Detalle: {body}"
                    ) from http_err
                else:
                    raise RuntimeError(
                        f"[{self._provider_name}] Error HTTP {http_err.code} llamando a {ep}: {body}"
                    ) from http_err
            except urllib.error.URLError as url_err:
                last_url_err = url_err
                err_str = str(getattr(url_err, "reason", url_err)).lower()
                if "timed out" in err_str:
                    raise TimeoutError(
                        f"[{self._provider_name}] Timeout tras {self._timeout:.0f}s esperando respuesta del modelo '{self.model_name}'. "
                        "El modelo puede estar cargando pesos en RAM/VRAM o el hardware requiere más tiempo. "
                        "Puedes aumentar PRAXEON_LLM_TIMEOUT en tu entorno si tu hardware requiere más tiempo."
                    ) from url_err
                continue

        if last_url_err:
            if "localhost" in self._endpoint or "127.0.0.1" in self._endpoint:
                raise ConnectionError(
                    f"[{self._provider_name}] No se pudo conectar al servidor local en {self._base_url}. "
                    "Verifica que el servicio esté activo ejecutando 'ollama serve' en tu terminal o abriendo la app de Ollama."
                ) from last_url_err
            raise ConnectionError(
                f"[{self._provider_name}] Error de conexión de red hacia {self._endpoint}: {last_url_err.reason}"
            ) from last_url_err


class GeminiLLM(BaseAgentLLM):
    """Cliente para la API de Google GenAI / Gemini."""

    def __init__(self, api_key: str, model: str = "gemini-3.6-flash") -> None:
        self._api_key = api_key
        self._model = model
        try:
            from google import genai
            from google.genai import types
            self._client = genai.Client(api_key=api_key)
            self._config = types.GenerateContentConfig(
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)
            )
        except ImportError as imp_err:
            raise ImportError(
                "El paquete 'google-genai' no está instalado. Ejecuta 'pip install google-genai'."
            ) from imp_err

    @property
    def provider_name(self) -> str:
        return "gemini"

    @property
    def model_name(self) -> str:
        return self._model

    def generate(self, messages: List[Dict[str, str]], system_prompt: Optional[str] = None) -> str:
        prompt_parts: List[str] = []
        if system_prompt:
            prompt_parts.append(f"[SYSTEM]: {system_prompt}")

        for m in messages:
            role = m.get("role", "user").upper()
            prompt_parts.append(f"[{role}]: {m.get('content', '')}")
        prompt_parts.append("[ASSISTANT]:\n")

        full_prompt = "\n\n".join(prompt_parts)

        is_antigravity = "antigravity" in self._model.lower()
        if is_antigravity:
            res = self._client.interactions.create(
                model="antigravity-preview-09-2026",
                input=full_prompt,
            )
            return (res.output_text or "").strip()
        else:
            res = self._client.models.generate_content(
                model=self._model,
                contents=full_prompt,
                config=self._config,
            )
            return (res.text or "").strip()


class SimulatedAgentLLM(BaseAgentLLM):
    """Simulador determinista de agente para demostraciones offline y tests sin llamadas a API."""

    def __init__(self, model_name: str = "agent-simulator") -> None:
        self._model_name = model_name
        self._step_counter = 0

    @property
    def provider_name(self) -> str:
        return "simulator"

    @property
    def model_name(self) -> str:
        return self._model_name

    def generate(self, messages: List[Dict[str, str]], system_prompt: Optional[str] = None) -> str:
        self._step_counter += 1
        last_msg = messages[-1]["content"] if messages else ""

        if self._step_counter == 1:
            return (
                "Thought: Primero exploraré el directorio del proyecto para entender su estructura.\n"
                'Action: run_command("dir /b")'
            )
        elif self._step_counter == 2:
            return (
                "Thought: Leeré el archivo de configuración para verificar dependencias.\n"
                'Action: read_file("pyproject.toml")'
            )
        else:
            return (
                "Thought: He recopilado la información necesaria y he concluido la tarea.\n"
                'Action: finish("Tarea completada con éxito tras inspeccionar el entorno.")'
            )


def is_ollama_online(host: str = "http://127.0.0.1:11434") -> bool:
    """Comprueba de forma no bloqueante (<350ms) si el daemon de Ollama está activo."""
    hosts = [host]
    if "127.0.0.1" in host:
        hosts.append(host.replace("127.0.0.1", "localhost"))
    elif "localhost" in host:
        hosts.append(host.replace("localhost", "127.0.0.1"))

    for h in hosts:
        try:
            url = f"{h.rstrip('/')}/api/tags"
            req = urllib.request.Request(url, headers={"User-Agent": "Praxeon/1.0.0"})
            with urllib.request.urlopen(req, timeout=0.35) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            continue
    return False


def get_ollama_installed_models(host: str = "http://127.0.0.1:11434") -> List[str]:
    """Obtiene la lista de nombres de modelos descargados e instalados en Ollama."""
    hosts = [host]
    if "127.0.0.1" in host:
        hosts.append(host.replace("127.0.0.1", "localhost"))
    elif "localhost" in host:
        hosts.append(host.replace("localhost", "127.0.0.1"))

    for h in hosts:
        try:
            url = f"{h.rstrip('/')}/api/tags"
            req = urllib.request.Request(url, headers={"User-Agent": "Praxeon/1.0.0"})
            with urllib.request.urlopen(req, timeout=0.6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = [m.get("name") for m in data.get("models", []) if m.get("name")]
                if models:
                    return models
        except Exception:
            continue
    return []


def try_start_ollama_daemon() -> bool:
    """Intenta arrancar en segundo plano el daemon local de Ollama si está instalado pero apagado."""
    if is_ollama_online():
        return True

    ollama_exe = shutil.which("ollama")
    if not ollama_exe and sys.platform == "win32":
        local_app = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
        if local_app.is_file():
            ollama_exe = str(local_app)

    if not ollama_exe:
        return False

    try:
        flags = 0
        if sys.platform == "win32":
            # DETACHED_PROCESS = 0x00000008, CREATE_NO_WINDOW = 0x08000000
            flags = 0x00000008 | 0x08000000
        subprocess.Popen(
            [ollama_exe, "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
            close_fds=True,
        )
        for _ in range(20):
            time.sleep(0.2)
            if is_ollama_online():
                return True
    except Exception:
        pass
    return False


def create_agent_llm(
    provider: Optional[str] = None,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout: float = 120.0,
    max_tokens: int = 1024,
) -> BaseAgentLLM:
    """Fábrica universal para inicializar el cliente LLM del Agente Autónomo.

    Args:
        provider: 'auto', 'groq', 'ollama', 'openrouter', 'lmstudio', 'openai', 'gemini' o 'simulator'.
        model: Identificador del modelo (si no se indica, usa el recomendado del proveedor).
        api_key: Clave de API (o tomada automáticamente de variables de entorno).
        base_url: URL base personalizada para endpoints OpenAI-compatible.
        timeout: Timeout de conexión y generación en segundos.

    Returns:
        Instancia de BaseAgentLLM lista para generar respuestas.
    """
    prov_key = (provider or "auto").lower().strip()

    # Detección automática inteligente cuando provider es 'auto'
    if prov_key == "auto":
        groq_key = api_key or os.getenv("GROQ_API_KEY")
        openrouter_key = api_key or os.getenv("OPENROUTER_API_KEY")
        openai_key = api_key or os.getenv("OPENAI_API_KEY")
        gemini_key = api_key or os.getenv("GEMINI_API_KEY")

        # 1. Si hay clave de Groq configurada, usar Groq (máxima velocidad y cuota gratis generosa)
        if groq_key and groq_key.strip():
            prov_key = "groq"
        # 2. Si Ollama está corriendo en localhost o 127.0.0.1, usar Ollama
        elif is_ollama_online():
            prov_key = "ollama"
        # 3. Si hay clave de OpenRouter, usar OpenRouter
        elif openrouter_key and openrouter_key.strip():
            prov_key = "openrouter"
        # 4. Si hay clave de Gemini, usar Gemini
        elif gemini_key and gemini_key.strip():
            prov_key = "gemini"
        # 5. Si hay clave de OpenAI, usar OpenAI
        elif openai_key and openai_key.strip():
            prov_key = "openai"
        # 6. Fallback a simulador offline si no hay claves ni servicios locales
        else:
            return SimulatedAgentLLM(model_name="simulated-agent-v04")

    # Proveedor: Groq Cloud
    if prov_key == "groq":
        preset = PROVIDER_PRESETS["groq"]
        is_custom = _is_custom_endpoint("groq", base_url)
        if is_custom:
            if not api_key:
                raise ValueError(
                    "Para endpoints personalizados de Groq (base_url), la clave de API debe ser suministrada "
                    "explícitamente y no puede heredarse de las variables de entorno del servidor (Finding 5)."
                )
            key = api_key
        else:
            key = api_key or os.getenv("GROQ_API_KEY")
        if not key or not key.strip():
            raise ValueError(
                "Para usar Groq debes configurar la variable GROQ_API_KEY en tu archivo .env "
                "o pasarla con --api-key. Consigue una clave gratuita en: https://console.groq.com/keys"
            )
        target_model = model or preset["default_model"]
        return OpenAICompatibleLLM(
            base_url=base_url or preset["base_url"],
            model=target_model,
            api_key=key,
            provider_name="groq",
            timeout=timeout,
            max_tokens=max_tokens,
        )

    # Proveedor: Ollama Local
    elif prov_key == "ollama":
        preset = PROVIDER_PRESETS["ollama"]
        # 1. Si no responde, intentar arrancar el daemon local automáticamente
        if not is_ollama_online():
            try_start_ollama_daemon()

        target_url = base_url or os.getenv("OLLAMA_HOST") or preset["base_url"]
        target_model = model
        if not target_model:
            # 2. Si no se especificó modelo concreto, detectar qué modelos tiene ya descargados
            installed = get_ollama_installed_models(target_url.replace("/v1", ""))
            if installed:
                # Priorizar modelos de coding (ej. qwen2.5-coder) si están presentes
                coder_candidates = [m for m in installed if "coder" in m.lower()]
                target_model = coder_candidates[0] if coder_candidates else installed[0]
            else:
                target_model = preset["default_model"]

        return OpenAICompatibleLLM(
            base_url=target_url,
            model=target_model,
            api_key=api_key or "ollama",
            provider_name="ollama",
            timeout=timeout,
            max_tokens=max_tokens,
        )

    # Proveedor: OpenRouter
    elif prov_key == "openrouter":
        preset = PROVIDER_PRESETS["openrouter"]
        is_custom = _is_custom_endpoint("openrouter", base_url)
        if is_custom:
            if not api_key:
                raise ValueError(
                    "Para endpoints personalizados de OpenRouter (base_url), la clave de API debe ser suministrada "
                    "explícitamente y no puede heredarse de las variables de entorno del servidor (Finding 5)."
                )
            key = api_key
        else:
            key = api_key or os.getenv("OPENROUTER_API_KEY")
        if not key or not key.strip():
            raise ValueError(
                "Para usar OpenRouter debes configurar la variable OPENROUTER_API_KEY en tu archivo .env. "
                "Consigue una clave en: https://openrouter.ai/keys"
            )
        target_model = model or preset["default_model"]
        return OpenAICompatibleLLM(
            base_url=base_url or preset["base_url"],
            model=target_model,
            api_key=key,
            provider_name="openrouter",
            timeout=timeout,
            max_tokens=max_tokens,
        )

    # Proveedor: LM Studio Local
    elif prov_key == "lmstudio":
        preset = PROVIDER_PRESETS["lmstudio"]
        target_url = base_url or preset["base_url"]
        target_model = model or preset["default_model"]
        return OpenAICompatibleLLM(
            base_url=target_url,
            model=target_model,
            api_key="lm-studio",
            provider_name="lmstudio",
            timeout=timeout,
            max_tokens=max_tokens,
        )

    # Proveedor: OpenAI Oficial o Endpoint Genérico
    elif prov_key in ("openai", "custom", "openai_compatible"):
        preset = PROVIDER_PRESETS["openai"]
        is_custom = _is_custom_endpoint("openai", base_url) if prov_key == "openai" else True
        if is_custom and base_url:
            # Finding 5: Endpoint personalizado no debe heredar OPENAI_API_KEY ambiental
            key = api_key
        else:
            key = api_key or os.getenv("OPENAI_API_KEY")
        target_url = base_url or preset["base_url"]
        target_model = model or preset["default_model"]
        return OpenAICompatibleLLM(
            base_url=target_url,
            model=target_model,
            api_key=key,
            provider_name=prov_key,
            timeout=timeout,
            max_tokens=max_tokens,
        )

    # Proveedor: Google Gemini
    elif prov_key == "gemini":
        key = api_key or os.getenv("GEMINI_API_KEY")
        if not key or not key.strip():
            raise ValueError(
                "Para usar Gemini debes configurar GEMINI_API_KEY en tu .env o usar otro proveedor como "
                "'--provider groq' o '--provider ollama'."
            )
        target_model = model or PROVIDER_PRESETS["gemini"]["default_model"]
        return GeminiLLM(api_key=key, model=target_model)

    elif prov_key in ("simulator", "simulated"):
        return SimulatedAgentLLM(model_name=model or "simulated-agent-v04")

    else:
        # Si pasan un proveedor desconocido pero con base_url, asumimos OpenAI-compatible
        if base_url:
            return OpenAICompatibleLLM(
                base_url=base_url,
                model=model or "custom-model",
                api_key=api_key,
                provider_name=prov_key,
                timeout=timeout,
            )
        raise ValueError(
            f"Proveedor de LLM desconocido: '{provider}'. Opciones válidas: "
            f"auto, groq, ollama, openrouter, lmstudio, openai, gemini, simulator."
        )
