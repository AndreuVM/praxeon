"""Módulo de cliente LLM desacoplado para el Agente Autónomo en PRAXEON.

Permite sustituir o complementar Google Gemini con proveedores locales (Ollama, LM Studio)
o en la nube con tiers gratuitos generosos (Groq, OpenRouter, OpenAI-compatible) mediante la API
estándar de chat completions (usando urllib nativo, sin dependencias externas pesadas).
"""

from abc import ABC, abstractmethod
import atexit
import json
import logging
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional
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
    "anthropic": {
        "base_url": "https://api.anthropic.com",
        "default_model": "claude-sonnet-5-5",
        "description": "Anthropic Claude por API (facturación por tokens, independiente de la suscripción de claude.ai)",
        "env_key": "ANTHROPIC_API_KEY",
        "requires_key": True,
        "recommended_models": ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5", "claude-fable-5-1"],
    },
    "claude_cli": {
        "base_url": None,
        "default_model": "sonnet",
        "description": "CLI de Claude en segundo plano (usa la sesión de tu suscripción de claude.ai, sin clave de API)",
        "env_key": None,
        "requires_key": False,
        "recommended_models": ["sonnet", "opus", "haiku"],
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

    # True si el proveedor guarda la conversación en una sesión abierta: no hace falta comprimir el historial
    stateful_session = False
    # Tokens reales de la última llamada ({prompt_tokens, output_tokens, cached_tokens}); None si no se informaron
    last_usage: Optional[Dict[str, int]] = None

    def _record_usage(self, prompt: Any, output: Any, cached: Any = 0) -> None:
        def _n(value: Any) -> int:
            try:
                return max(0, int(value or 0))
            except (TypeError, ValueError):
                return 0

        self.last_usage = {"prompt_tokens": _n(prompt), "output_tokens": _n(output), "cached_tokens": _n(cached)}

    def _usage_from_openai(self, usage: Any) -> None:
        """Formato OpenAI/Groq/OpenRouter/Ollama(/v1): usage.prompt_tokens_details.cached_tokens."""
        if isinstance(usage, dict):
            details = usage.get("prompt_tokens_details")
            cached = details.get("cached_tokens") if isinstance(details, dict) else 0
            self._record_usage(usage.get("prompt_tokens"), usage.get("completion_tokens"), cached)

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

    def end_session(self) -> None:
        """Libera recursos de la conversación (procesos abiertos, etc.). Por defecto no hace nada."""
        return None


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
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # nosec B310
                    resp_bytes = resp.read()
                    data = json.loads(resp_bytes.decode("utf-8"))
                    self._usage_from_openai(data.get("usage"))
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


class OllamaLLM(OpenAICompatibleLLM):
    """Ollama por su API nativa (/api/chat).

    El endpoint compatible con OpenAI (/v1) ignora `num_ctx` y Ollama usa entonces su contexto por defecto (4096 tokens
    en muchas instalaciones): un prompt más largo se trunca por el principio, justo donde van las instrucciones. La API
    nativa permite fijar el contexto. El valor debe ser constante entre llamadas: cambiarlo recarga el modelo y pierde
    la caché del prefijo, que Ollama reutiliza por sí mismo mientras el principio del prompt no cambia.
    """

    def __init__(self, *args: Any, num_ctx: Optional[int] = None, keep_alive: str = "30m", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._num_ctx = int(num_ctx or os.getenv("PRAXEON_OLLAMA_NUM_CTX", "8192"))
        self._keep_alive = keep_alive
        root = self._base_url[:-3] if self._base_url.endswith("/v1") else self._base_url
        self._native_endpoint = f"{root}/api/chat"
        self._use_native = True

    @property
    def num_ctx(self) -> int:
        return self._num_ctx

    def generate(self, messages: List[Dict[str, str]], system_prompt: Optional[str] = None) -> str:
        if not self._use_native:
            return super().generate(messages, system_prompt)
        payload_messages: List[Dict[str, str]] = []
        if system_prompt:
            payload_messages.append({"role": "system", "content": system_prompt})
        for m in messages:
            role = m.get("role", "user")
            payload_messages.append({"role": role if role in ("system", "user", "assistant") else "user", "content": m.get("content", "")})
        payload = {
            "model": self._model,
            "messages": payload_messages,
            "stream": False,
            "keep_alive": self._keep_alive,
            "options": {"num_ctx": self._num_ctx, "num_predict": self._max_tokens, "temperature": self._temperature},
        }
        req = urllib.request.Request(
            self._native_endpoint, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "Praxeon/1.0.0 (Autonomous Agent Runtime)"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # nosec B310
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as http_err:
            if http_err.code == 404:   # Ollama muy antiguo, o un servidor que solo habla /v1
                self._use_native = False
                return super().generate(messages, system_prompt)
            detail = ""
            try:
                detail = http_err.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            raise RuntimeError(f"[ollama] Error HTTP {http_err.code} llamando a {self._native_endpoint}: {detail}") from http_err
        except urllib.error.URLError:
            return super().generate(messages, system_prompt)   # reutiliza sus mensajes de error (daemon apagado, timeout...)

        prompt_tokens = int(data.get("prompt_eval_count") or 0)
        self._record_usage(prompt_tokens, data.get("eval_count"))
        if prompt_tokens >= self._num_ctx * 0.95:
            logging.getLogger(__name__).warning(
                "[ollama] El prompt (%d tokens) llena el contexto num_ctx=%d: Ollama puede haber descartado el principio "
                "(instrucciones). Sube PRAXEON_OLLAMA_NUM_CTX si tu VRAM lo permite.", prompt_tokens, self._num_ctx,
            )
        return str((data.get("message") or {}).get("content") or "").strip()


class AnthropicLLM(BaseAgentLLM):
    """Cliente de la API de mensajes de Anthropic (Claude). Se factura por tokens con la clave de API."""

    API_VERSION = "2023-06-01"
    RETRY_STATUS = (429, 500, 502, 503, 529)

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-5-5",
        base_url: str = "https://api.anthropic.com",
        timeout: float = 180.0,
        max_tokens: int = 8192,
        temperature: Optional[float] = None,
        max_retries: int = 2,
        retry_delay: float = 2.0,
        prompt_cache: Optional[bool] = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._endpoint = f"{base_url.rstrip('/')}/v1/messages"
        # Caching automático de prompt (el punto de corte se mueve al último bloque): solo contra la API oficial
        self._prompt_cache = self._endpoint.startswith("https://api.anthropic.com/") if prompt_cache is None else prompt_cache
        self._timeout = timeout
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._max_retries = max_retries
        self._retry_delay = retry_delay

    @property
    def provider_name(self) -> str:
        return "anthropic"

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def endpoint(self) -> str:
        return self._endpoint

    @staticmethod
    def _build_messages(messages: List[Dict[str, str]], system_prompt: Optional[str]):
        """La API exige turnos que alternen user/assistant empezando por user y sin textos vacíos."""
        system_parts = [system_prompt] if system_prompt else []
        turns: List[Dict[str, str]] = []
        for m in messages:
            text = str(m.get("content", "")).strip()
            if not text:
                continue
            role = m.get("role", "user")
            if role == "system":
                system_parts.append(text)
                continue
            role = "assistant" if role == "assistant" else "user"
            if turns and turns[-1]["role"] == role:
                turns[-1]["content"] += "\n\n" + text
            else:
                turns.append({"role": role, "content": text})
        if not turns or turns[0]["role"] != "user":
            turns.insert(0, {"role": "user", "content": "Comienza."})
        return "\n\n".join(system_parts), turns

    def generate(self, messages: List[Dict[str, str]], system_prompt: Optional[str] = None) -> str:
        system, turns = self._build_messages(messages, system_prompt)
        payload: Dict[str, Any] = {"model": self._model, "max_tokens": self._max_tokens, "messages": turns}
        if system:
            payload["system"] = system
        if self._prompt_cache:
            payload["cache_control"] = {"type": "ephemeral"}
        if self._temperature is not None:
            payload["temperature"] = self._temperature
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self._api_key,
            "anthropic-version": self.API_VERSION,
            "User-Agent": "Praxeon/1.0.0 (Autonomous Agent Runtime)",
        }

        attempt = 0
        while True:
            req = urllib.request.Request(self._endpoint, data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # nosec B310
                    data = json.loads(resp.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as http_err:
                detail = ""
                try:
                    detail = http_err.read().decode("utf-8", errors="replace")
                except Exception:
                    pass
                if http_err.code in self.RETRY_STATUS and attempt < self._max_retries:
                    attempt += 1
                    try:
                        wait = float(http_err.headers.get("retry-after") or self._retry_delay)
                    except (TypeError, ValueError):
                        wait = self._retry_delay
                    time.sleep(min(max(wait, 0.0), 20.0))
                    continue
                if http_err.code in (401, 403):
                    raise RuntimeError(
                        f"[anthropic] Error de autenticación (HTTP {http_err.code}). Verifica ANTHROPIC_API_KEY. Detalle: {detail}"
                    ) from http_err
                if http_err.code == 429:
                    raise RuntimeError(f"[anthropic] Límite de uso o de saldo excedido (HTTP 429). Detalle: {detail}") from http_err
                if http_err.code == 529:
                    raise RuntimeError(f"[anthropic] La API está sobrecargada (HTTP 529). Detalle: {detail}") from http_err
                raise RuntimeError(f"[anthropic] Error HTTP {http_err.code} llamando a {self._endpoint}: {detail}") from http_err
            except urllib.error.URLError as url_err:
                if "timed out" in str(getattr(url_err, "reason", url_err)).lower():
                    raise TimeoutError(
                        f"[anthropic] Timeout tras {self._timeout:.0f}s esperando al modelo '{self._model}'. "
                        "Puedes aumentar PRAXEON_LLM_TIMEOUT."
                    ) from url_err
                raise ConnectionError(f"[anthropic] Error de conexión hacia {self._endpoint}: {url_err.reason}") from url_err

        usage = data.get("usage") or {}
        read = usage.get("cache_read_input_tokens") or 0
        written = usage.get("cache_creation_input_tokens") or 0
        self._record_usage((usage.get("input_tokens") or 0) + read + written, usage.get("output_tokens"), read)
        text = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text").strip()
        if data.get("stop_reason") == "max_tokens":
            logging.getLogger(__name__).warning(
                "[anthropic] La respuesta se cortó por max_tokens (%d): el modelo no pudo terminar. Sube el límite si escribe archivos largos.",
                self._max_tokens,
            )
        return text


class _CliSession:
    """Un proceso `claude -p` en modo stream-json que se mantiene abierto entre turnos."""

    def __init__(self, cmd: List[str], env: Dict[str, str], workdir: str) -> None:
        self.workdir = workdir
        self.users: List[str] = []   # textos user ya entregados al proceso (para validar que la conversación continúa)
        self.n_turns = 0             # nº de mensajes de la conversación que el proceso ya conoce
        self._lines: "queue.Queue[Optional[str]]" = queue.Queue()
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", cwd=workdir, env=env,
        )
        threading.Thread(target=self._pump, name="praxeon-claude-cli", daemon=True).start()

    def _pump(self) -> None:
        try:
            for line in self.proc.stdout:  # type: ignore[union-attr]
                self._lines.put(line)
        except (OSError, ValueError):
            pass
        self._lines.put(None)

    def alive(self) -> bool:
        return self.proc.poll() is None

    def ask(self, text: str, timeout: float) -> Dict[str, Any]:
        """Envía un turno de usuario y espera el evento `result` de la CLI."""
        message = {"type": "user", "message": {"role": "user", "content": text}}
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()
        deadline = time.monotonic() + timeout
        tail: List[str] = []
        while True:
            try:
                line = self._lines.get(timeout=max(0.1, deadline - time.monotonic()))
            except queue.Empty:
                raise TimeoutError(f"[claude_cli] Timeout tras {timeout:.0f}s esperando a la CLI de Claude.") from None
            if line is None:
                raise RuntimeError(f"[claude_cli] La CLI terminó inesperadamente. Salida: {' '.join(tail)[-400:]}")
            try:
                event = json.loads(line)
            except ValueError:
                tail.append(line.strip())
                continue
            if isinstance(event, dict) and event.get("type") == "result":
                return event
            if time.monotonic() > deadline:
                raise TimeoutError(f"[claude_cli] Timeout tras {timeout:.0f}s esperando a la CLI de Claude.")

    def close(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
            self.proc.wait(timeout=3)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        shutil.rmtree(self.workdir, ignore_errors=True)


class ClaudeCLILLM(BaseAgentLLM):
    """Usa la CLI `claude` instalada como LLM puro (sin herramientas), con la sesión de la suscripción de claude.ai.

    Mantiene un proceso abierto por conversación (modo stream-json): el primer turno lo arranca y los siguientes solo
    envían el mensaje nuevo, sin reiniciar la CLI ni reenviar el historial. Si la conversación no continúa la anterior
    (reintento, historial comprimido) o el proceso muere, se abre uno nuevo con el historial completo. Se quitan del
    entorno las claves de API de Anthropic para que use el inicio de sesión de la suscripción y no facture por tokens.
    """

    _STRIP_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
    MAX_SESSIONS = 4
    stateful_session = True   # la CLI guarda la conversación: el historial no se comprime

    def __init__(self, model: str = "sonnet", executable: Optional[str] = None, timeout: float = 300.0) -> None:
        self._model = model
        self._executable = executable or shutil.which("claude")
        self._timeout = timeout
        self._sessions: Dict[Any, _CliSession] = {}
        self._lock = threading.RLock()
        if not self._executable:
            raise ValueError(
                "No se encuentra la CLI 'claude' en el PATH. Instálala e inicia sesión con tu cuenta (ejecuta 'claude' una vez)."
            )
        atexit.register(self.end_session)

    @property
    def provider_name(self) -> str:
        return "claude_cli"

    @property
    def model_name(self) -> str:
        return self._model

    @staticmethod
    def _transcript(turns: List[Any]) -> str:
        lines = [f"[{'ASSISTANT' if role == 'assistant' else 'USER'}]: {text}" for role, text in turns]
        return "\n\n".join(lines) + "\n\n[ASSISTANT]:"

    def _open(self, system: str) -> _CliSession:
        env = {k: v for k, v in os.environ.items() if k not in self._STRIP_ENV}
        workdir = tempfile.mkdtemp(prefix="praxeon-claude-")
        cmd = [
            self._executable, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
            "--tools", "", "--no-session-persistence", "--setting-sources", "", "--strict-mcp-config",
            "--disable-slash-commands", "--model", self._model,
        ]
        if system:
            sp_file = Path(workdir) / "system.txt"
            sp_file.write_text(system, encoding="utf-8")
            cmd += ["--system-prompt-file", str(sp_file)]
        try:
            return _CliSession(cmd, env, workdir)
        except OSError as exc:
            shutil.rmtree(workdir, ignore_errors=True)
            raise ConnectionError(f"[claude_cli] No se pudo ejecutar la CLI de Claude: {exc}") from exc

    def _drop(self, key: Any) -> None:
        session = self._sessions.pop(key, None)
        if session is not None:
            session.close()

    def end_session(self) -> None:
        """Cierra todos los procesos abiertos (al terminar el nodo o la misión)."""
        with self._lock:
            for key in list(self._sessions):
                self._drop(key)

    def generate(self, messages: List[Dict[str, str]], system_prompt: Optional[str] = None) -> str:
        system_parts = [system_prompt] if system_prompt else []
        turns: List[Any] = []
        for m in messages:
            text = str(m.get("content", "")).strip()
            if not text:
                continue
            role = m.get("role", "user")
            if role == "system":
                system_parts.append(text)
            else:
                turns.append(("assistant" if role == "assistant" else "user", text))
        if not turns:
            turns = [("user", "Comienza.")]
        system = "\n\n".join(system_parts)

        with self._lock:
            key = (system, turns[0])
            session = self._sessions.get(key)
            outgoing: Optional[str] = None
            if session is not None:
                outgoing = self._continuation(session, turns) if session.alive() else None
                if outgoing is None:
                    self._drop(key)
                    session = None
            if session is None:
                while len(self._sessions) >= self.MAX_SESSIONS:
                    self._drop(next(iter(self._sessions)))
                session = self._open(system)
                self._sessions[key] = session
                outgoing = turns[0][1] if len(turns) == 1 and turns[0][0] == "user" else self._transcript(turns)

            try:
                data = session.ask(outgoing or "", self._timeout)
            except Exception:
                self._drop(key)   # el estado del proceso es incierto: el siguiente turno empieza de cero
                raise
            if data.get("is_error"):
                self._drop(key)
                detail = str(data.get("result") or "")[:500]
                if "login" in detail.lower() or "authenticat" in detail.lower():
                    raise RuntimeError(f"[claude_cli] La CLI no tiene sesión iniciada. Ejecuta 'claude' y haz login. Detalle: {detail}")
                raise RuntimeError(f"[claude_cli] La CLI devolvió un error: {detail}")
            if data.get("permission_denials"):
                logging.getLogger(__name__).warning("[claude_cli] La CLI denegó usos de herramientas")
            usage = data.get("usage") or {}
            read = usage.get("cache_read_input_tokens") or 0
            self._record_usage(
                (usage.get("input_tokens") or 0) + read + (usage.get("cache_creation_input_tokens") or 0),
                usage.get("output_tokens"), read,
            )
            session.n_turns = len(turns)
            session.users = [t for r, t in turns if r == "user"]
            return str(data.get("result") or "").strip()

    @staticmethod
    def _continuation(session: _CliSession, turns: List[Any]) -> Optional[str]:
        """Mensaje nuevo si `turns` continúa la conversación que el proceso ya conoce; None si hay que reiniciar."""
        known = session.n_turns
        if known == 0 or len(turns) <= known:
            return None
        if [t for r, t in turns[:known] if r == "user"] != session.users:
            return None                       # el historial cambió (compresión, reintento): reiniciar
        rest = turns[known:]
        if rest and rest[0][0] == "assistant":
            rest = rest[1:]                   # la respuesta del modelo ya está en su contexto
        if not rest or any(r != "user" for r, _ in rest):
            return None
        return "\n\n".join(t for _, t in rest)


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
        from google.genai import types

        is_antigravity = "antigravity" in self._model.lower()
        if is_antigravity:
            # La API de interacciones recibe texto plano
            parts_txt: List[str] = []
            if system_prompt:
                parts_txt.append(f"[SYSTEM]: {system_prompt}")
            for m in messages:
                parts_txt.append(f"[{m.get('role', 'user').upper()}]: {m.get('content', '')}")
            parts_txt.append("[ASSISTANT]:\n")
            res = self._client.interactions.create(
                model="antigravity-preview-09-2026",
                input="\n\n".join(parts_txt),
            )
            return (res.output_text or "").strip()

        # Turnos con su rol real y el prompt de sistema como system_instruction: con una transcripción plana
        # («[USER]: ... [ASSISTANT]:») el modelo tiende a continuarla e inventar los turnos siguientes.
        contents: List[Any] = []
        last_role = None
        for m in messages:
            text = str(m.get("content", ""))
            if not text.strip():
                continue
            role = "model" if m.get("role") == "assistant" else "user"
            if role == last_role:
                contents[-1] = types.Content(role=role, parts=[types.Part(text=contents[-1].parts[0].text + "\n\n" + text)])
            else:
                contents.append(types.Content(role=role, parts=[types.Part(text=text)]))
            last_role = role
        if not contents or contents[0].role != "user":
            contents.insert(0, types.Content(role="user", parts=[types.Part(text="Comienza.")]))

        timeout_ms = int(float(os.getenv("PRAXEON_LLM_TIMEOUT", "180")) * 1000)
        config = types.GenerateContentConfig(
            system_instruction=system_prompt or None,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            http_options=types.HttpOptions(timeout=timeout_ms),
        )
        res = self._client.models.generate_content(model=self._model, contents=contents, config=config)
        meta = getattr(res, "usage_metadata", None)
        if meta is not None:
            self._record_usage(
                getattr(meta, "prompt_token_count", 0), getattr(meta, "candidates_token_count", 0),
                getattr(meta, "cached_content_token_count", 0),
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
            with urllib.request.urlopen(req, timeout=0.35) as resp:  # nosec B310
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
            with urllib.request.urlopen(req, timeout=0.6) as resp:  # nosec B310
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

        return OllamaLLM(
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

    # Proveedor: Anthropic Claude (API de pago por tokens; nunca se elige en modo auto para no generar gasto sin avisar)
    elif prov_key == "anthropic":
        preset = PROVIDER_PRESETS["anthropic"]
        if _is_custom_endpoint("anthropic", base_url):
            if not api_key:
                raise ValueError(
                    "Para un endpoint personalizado de Anthropic (base_url) la clave de API debe darse explícitamente; "
                    "no se hereda de las variables de entorno del servidor."
                )
            key = api_key
        else:
            key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not key or not key.strip():
            raise ValueError(
                "Para usar Anthropic configura ANTHROPIC_API_KEY (consola de desarrolladores de Anthropic) o indica la clave "
                "en los ajustes del chat. La API se factura por tokens, aparte de la suscripción de claude.ai."
            )
        return AnthropicLLM(
            api_key=key.strip(),
            model=model or preset["default_model"],
            base_url=base_url or preset["base_url"],
            timeout=timeout,
            max_tokens=max(max_tokens, 8192),  # escribir archivos necesita más de los 1024 por defecto
        )

    # Proveedor: CLI de Claude (suscripción; nunca se elige en modo auto)
    elif prov_key in ("claude_cli", "claude-cli"):
        return ClaudeCLILLM(model=model or PROVIDER_PRESETS["claude_cli"]["default_model"], timeout=max(timeout, 300.0))

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
            f"auto, groq, ollama, openrouter, lmstudio, openai, anthropic, claude_cli, gemini, simulator."
        )
