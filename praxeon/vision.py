"""Imágenes adjuntas al chat (FE-006, nota N-003).

Una foto de móvil lleva la ubicación GPS, la fecha, el modelo del teléfono y a veces una miniatura del original. Antes de que una imagen
llegue al modelo —y antes incluso de guardarla en el workspace— se VUELVE A CONSTRUIR desde sus píxeles: se valida por firma de bytes (no
por extensión), se aplica la orientación EXIF, se reduce a ≤2048 px y se recodifica sin metadatos. Nada del archivo original viaja ni se
guarda salvo los píxeles.

Principios, a propósito:
  * Un texto dentro de una imagen es un DATO no fiable: no hay OCR (D4) ni nada que lo convierta en instrucciones o cambie la política.
  * Un modelo sin visión NO recibe la imagen «en silencio»: se avisa y la misión no se inicia (``model_supports_vision``).
  * SVG no es una imagen aquí: es texto con posibles scripts. Se trata como un archivo de texto normal (adjunto de FE-005).
  * Pillow es un extra opcional (``praxeon[vision]``); sin él las imágenes se desactivan con un mensaje claro, no se envían sin limpiar.
"""

from __future__ import annotations

import base64
import io
import math
import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

MAX_SIDE = 2048                    # lado mayor tras reducir
MAX_PIXELS = 64_000_000            # por encima se rechaza ANTES de decodificar (bomba de descompresión)
MAX_OUTPUT_BYTES = 4_500_000       # lo que se envía por imagen; los proveedores aceptan 5 MB o más en base64
RASTER_EXTENSIONS = {".png", ".jpg", ".jpeg", ".jpe", ".jfif", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif", ".avif", ".ico"}
INSTALL_HINT = "Las imágenes necesitan Pillow: instala `pip install \"praxeon[vision]\"`."


class ImageError(ValueError):
    """La imagen no se puede aceptar (no es una imagen válida, es demasiado grande, está dañada…)."""


class VisionUnavailable(ImageError):
    """Falta Pillow: no se aceptan imágenes (nunca se envían sin limpiar sus metadatos)."""


def available() -> bool:
    try:
        import PIL  # noqa: F401
    except ImportError:
        return False
    return True


# ------------------------------------------------------------------ firma de bytes ---------------------------------------------------
def sniff(data: bytes) -> str | None:
    """Formato por la FIRMA del archivo: «png», «jpeg», «gif», «webp» o None. La extensión no cuenta."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def looks_like_svg(data: bytes) -> bool:
    head = data[:2048].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    return head.startswith(b"<svg") or (head.startswith((b"<?xml", b"<!doctype svg")) and b"<svg" in data[:4096].lower())


def has_image_extension(name: str) -> bool:
    return os.path.splitext(name or "")[1].lower() in RASTER_EXTENSIONS


# ------------------------------------------------------------------ procesado ---------------------------------------------------------
@dataclass(frozen=True)
class ProcessedImage:
    data: bytes
    mime: str
    width: int
    height: int
    original_width: int
    original_height: int
    had_metadata: bool           # el original traía EXIF, ICC, XMP, comentarios…: nada de eso se conserva
    resized: bool
    first_frame_only: bool       # un GIF animado se queda con su primer fotograma


def _has_metadata(img: Any) -> bool:
    info = getattr(img, "info", {}) or {}
    try:
        exif = bool(img.getexif())
    except Exception:  # noqa: BLE001 - un EXIF corrupto cuenta como «traía metadatos»; da igual cuál
        exif = True
    keys = {k for k in info if k not in ("dpi", "transparency", "background", "duration", "loop", "gamma", "interlace", "progressive", "progression", "version")}
    return exif or bool(keys)


def _encode(img: Any, fmt: str) -> bytes:
    buf = io.BytesIO()
    if fmt == "JPEG":
        img.save(buf, "JPEG", quality=85, optimize=True)
    else:
        img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def process_image(data: bytes) -> ProcessedImage:
    """Valida, orienta, reduce y recodifica SIN metadatos. Lanza ``ImageError`` (o ``VisionUnavailable``)."""
    if looks_like_svg(data):
        raise ImageError("SVG no se admite como imagen: puede contener scripts. Adjúntalo como archivo de texto si lo necesitas.")
    kind = sniff(data)
    if kind is None:
        raise ImageError("El archivo no es una imagen PNG, JPEG, WebP o GIF válida.")
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise VisionUnavailable(INSTALL_HINT) from exc

    try:
        img = Image.open(io.BytesIO(data))
        original_w, original_h = img.size
        if original_w <= 0 or original_h <= 0 or original_w * original_h > MAX_PIXELS:
            raise ImageError(f"La imagen es demasiado grande ({original_w}×{original_h} píxeles).")
        had_metadata = _has_metadata(img)
        animated = bool(getattr(img, "is_animated", False)) and getattr(img, "n_frames", 1) > 1
        if animated:
            img.seek(0)
        img.load()
        img = ImageOps.exif_transpose(img) or img                    # la orientación se aplica ANTES de tirar el EXIF
    except ImageError:
        raise
    except Exception as exc:  # noqa: BLE001 - Pillow lanza de todo con un archivo roto (OSError, SyntaxError, DecompressionBombError…)
        raise ImageError("La imagen está dañada o no se puede leer.") from exc

    if img.mode in ("P", "PA"):
        img = img.convert("RGBA" if "transparency" in img.info or img.mode == "PA" else "RGB")
    elif img.mode in ("LA",):
        img = img.convert("RGBA")
    elif img.mode not in ("RGB", "RGBA", "L"):
        img = img.convert("RGB")
    resized = max(img.size) > MAX_SIDE
    if resized:
        img.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)

    # Se reconstruye desde los píxeles: ni `info`, ni EXIF, ni ICC, ni XMP, ni comentarios pasan al resultado
    clean = Image.frombytes(img.mode, img.size, img.tobytes())
    has_alpha = clean.mode == "RGBA"
    fmt = "JPEG" if kind == "jpeg" and not has_alpha else "PNG"
    out = _encode(clean, fmt)
    while len(out) > MAX_OUTPUT_BYTES:                               # captura enorme: JPEG si no hay transparencia, y si no, más pequeña
        if fmt == "PNG" and not has_alpha:
            fmt = "JPEG"
            out = _encode(clean.convert("RGB"), fmt)
            continue
        w, h = clean.size
        if min(w, h) <= 256:
            raise ImageError("La imagen es demasiado pesada incluso reducida.")
        clean = clean.resize((max(1, int(w * 0.75)), max(1, int(h * 0.75))), Image.Resampling.LANCZOS)
        out = _encode(clean if fmt == "PNG" or clean.mode != "RGBA" else clean.convert("RGB"), fmt)
        resized = True
    return ProcessedImage(
        data=out, mime="image/jpeg" if fmt == "JPEG" else "image/png", width=clean.size[0], height=clean.size[1],
        original_width=original_w, original_height=original_h, had_metadata=had_metadata, resized=resized, first_frame_only=animated,
    )


def image_size(data: bytes) -> tuple[int, int] | None:
    """Dimensiones de una imagen ya limpia (None si no se puede leer)."""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as img:
            return img.size
    except Exception:  # noqa: BLE001 - solo informativo
        return None


def estimate_tokens(width: int, height: int) -> int:
    """Tokens que cuesta una imagen, de forma aproximada (≈ píxeles/750, entre 85 y 1600), para el presupuesto de contexto."""
    return min(1600, max(85, math.ceil((max(1, width) * max(1, height)) / 750)))


# ------------------------------------------------------------------ capacidad de visión por modelo ------------------------------------
_VISION_PATTERNS: dict[str, tuple[str, ...]] = {
    "anthropic": ("claude-3", "claude-sonnet", "claude-opus", "claude-haiku", "claude-4", "claude-5", "claude-fable"),
    "openai": ("gpt-4o", "gpt-4.1", "gpt-4-turbo", "gpt-4-vision", "gpt-5", "o1", "o3", "o4", "chatgpt-4o"),
    "gemini": ("gemini",),
    "groq": ("vision", "llama-4", "scout", "maverick"),
    "ollama": ("llava", "vision", "minicpm-v", "moondream", "bakllava", "gemma3", "qwen2.5vl", "qwen2-vl", "qwen3-vl", "granite3.2-vision", "mistral-small3.1", "llama4", "pixtral"),
    "openrouter": ("gpt-4o", "gpt-4.1", "gpt-5", "claude-3", "claude-sonnet", "claude-opus", "claude-haiku", "gemini", "vision", "llava", "-vl", "pixtral", "llama-4"),
}
_NO_VISION = {"openai": ("o1-mini", "o1-preview", "o3-mini", "gpt-3.5", "gpt-4-0", "gpt-4-32k"), "anthropic": ("claude-2", "claude-instant")}
_SIMULATOR = {"simulator", "simulated", "mock", "sim"}


def _extra_patterns() -> tuple[str, ...]:
    """Modelos que la persona declara con visión (`PRAXEON_VISION_MODELS=nombre1,nombre2`), para los que el catálogo no conoce."""
    raw = os.getenv("PRAXEON_VISION_MODELS", "")
    return tuple(p.strip().lower() for p in raw.split(",") if p.strip())


def model_supports_vision(provider: str | None, model: str | None) -> bool:
    """¿Este modelo acepta imágenes? Conservador: lo que no se conoce NO se da por capaz (se avisa en vez de enviar a ciegas)."""
    prov = (provider or "").strip().lower()
    name = (model or "").strip().lower()
    if prov in _SIMULATOR:
        return True
    if prov == "claude_cli":
        return False                                                  # la CLI de Claude no admite imágenes por este canal
    if any(p in name for p in _extra_patterns()):
        return True
    if any(bad in name for bad in _NO_VISION.get(prov, ())):
        return False
    return any(p in name for p in _VISION_PATTERNS.get(prov, ()))


def is_remote(provider: str | None, base_url: str | None = None) -> bool:
    """¿La imagen saldría de esta máquina? Todo proveedor salvo el simulador, la CLI local y un Ollama/endpoint en este equipo."""
    prov = (provider or "").strip().lower()
    if prov in _SIMULATOR or prov == "claude_cli":
        return False
    if prov == "ollama" and not base_url:
        return False
    if base_url:
        host = (urlparse(base_url).hostname or "").lower()
        return host not in ("localhost", "127.0.0.1", "::1", "0.0.0.0") and not host.endswith(".localhost")
    return True


def capability(provider: str | None, model: str | None, base_url: str | None = None) -> dict[str, Any]:
    """Resumen para la interfaz: ¿admite imágenes?, ¿saldrían de la máquina?, ¿está Pillow? y el motivo si no."""
    prov = (provider or "").strip().lower()
    reason = None
    supported = model_supports_vision(prov, model)
    if not available():
        reason = INSTALL_HINT
    elif prov == "claude_cli":
        reason = "La CLI de Claude no admite imágenes por este canal: usa el proveedor Anthropic con su clave de API."
    elif not supported:
        reason = f"El modelo «{model or 'por defecto'}» de {prov or 'este proveedor'} no admite imágenes. Elige uno con visión o quita la imagen."
    return {
        "available": available(),
        "supported": supported and available(),
        "remote": is_remote(prov, base_url),
        "provider": prov,
        "model": model or "",
        "reason": reason,
    }


# ------------------------------------------------------------------ bloques de contenido por proveedor -----------------------------------
# Cada imagen del mensaje es `{"mime": "image/png", "data": "<base64>"}`. Cada proveedor la pide con su forma; se construye aquí, sin red,
# para poder probarlas una a una.
def encode_image(data: bytes, mime: str, width: int | None = None, height: int | None = None) -> dict[str, Any]:
    """Imagen de un mensaje: `{mime, data (base64), tokens?}`; con las dimensiones se añade su coste estimado en tokens."""
    out: dict[str, Any] = {"mime": mime, "data": base64.b64encode(data).decode("ascii")}
    if width and height:
        out["tokens"] = estimate_tokens(width, height)
    return out


def openai_content(text: str, images: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """OpenAI y compatibles (Groq, OpenRouter, Ollama en /v1…): `image_url` con una URL de datos."""
    return [{"type": "text", "text": text}] + [
        {"type": "image_url", "image_url": {"url": f"data:{im['mime']};base64,{im['data']}"}} for im in images
    ]


def anthropic_content(text: str, images: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Anthropic: las imágenes van ANTES del texto, como bloques `image` con la fuente en base64."""
    return [{"type": "image", "source": {"type": "base64", "media_type": im["mime"], "data": im["data"]}} for im in images] + [{"type": "text", "text": text}]


def ollama_images(images: list[dict[str, Any]]) -> list[str]:
    """Ollama nativo (/api/chat): `images` es una lista de base64 sin cabecera."""
    return [im["data"] for im in images]


_B64 = re.compile(r"^[A-Za-z0-9+/=\r\n]+$")


def valid_images(images: Any) -> list[dict[str, Any]]:
    """Filtra lo que llega en `images` de un mensaje: solo `{mime, data}` con tipo de imagen y base64 plausible (y sus tokens si los hay)."""
    out: list[dict[str, Any]] = []
    for im in images or []:
        if isinstance(im, dict) and str(im.get("mime", "")).startswith("image/") and isinstance(im.get("data"), str) and _B64.match(im["data"]):
            item: dict[str, Any] = {"mime": str(im["mime"]), "data": im["data"]}
            if isinstance(im.get("tokens"), int):
                item["tokens"] = im["tokens"]
            out.append(item)
    return out
