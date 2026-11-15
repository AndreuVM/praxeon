"""Imágenes adjuntas (FE-006): validación por firma, limpieza de EXIF/GPS, reducción y capacidad de visión por modelo."""

from __future__ import annotations

import base64
import io
import os
import sys

import pytest

from praxeon import vision
from praxeon.vision import ImageError, VisionUnavailable, process_image, sniff

PIL = pytest.importorskip("PIL")
from PIL import Image, PngImagePlugin  # noqa: E402


def png_bytes(size=(40, 20), color=(200, 30, 30), mode="RGB", **save):
    buf = io.BytesIO()
    Image.new(mode, size, color if mode == "RGB" else (*color, 128) if mode == "RGBA" else 0).save(buf, "PNG", **save)
    return buf.getvalue()


def photo_with_exif(size=(40, 20), orientation=1):
    """Una «foto de móvil»: JPEG con marca, modelo, fecha, orientación y UBICACIÓN GPS."""
    img = Image.new("RGB", size, (10, 120, 200))
    exif = Image.Exif()
    exif[0x010F] = "MarcaSecretaPhone"
    exif[0x0110] = "Modelo-X-12345"
    exif[0x0132] = "2026:11:15 09:30:00"
    exif[0x0112] = orientation
    gps = exif.get_ifd(0x8825)
    gps[1] = "N"
    gps[2] = (41.0, 23.0, 7.0)
    gps[3] = "E"
    gps[4] = (2.0, 10.0, 18.0)
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif.tobytes())
    return buf.getvalue()


# ------------------------------------------------------------------ firma de bytes ------------------------------------------------------
def test_sniff_por_firma_no_por_extension():
    assert sniff(png_bytes()) == "png"
    assert sniff(photo_with_exif()) == "jpeg"
    buf = io.BytesIO()
    Image.new("RGB", (4, 4)).save(buf, "GIF")
    assert sniff(buf.getvalue()) == "gif"
    buf = io.BytesIO()
    Image.new("RGB", (4, 4)).save(buf, "WEBP")
    assert sniff(buf.getvalue()) == "webp"
    for nope in (b"", b"hola", b"<svg xmlns='http://www.w3.org/2000/svg'/>", b"%PDF-1.4", b"RIFF\x00\x00\x00\x00WAVE", b"GIF9", b"\x89PNG"):
        assert sniff(nope) is None


def test_svg_se_rechaza_como_imagen():
    for svg in (b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>", b"  \n<?xml version='1.0'?>\n<svg/>",
                b"\xef\xbb\xbf<SVG onload='x()'/>"):
        assert vision.looks_like_svg(svg)
        with pytest.raises(ImageError, match="SVG"):
            process_image(svg)
    assert not vision.looks_like_svg(png_bytes())


def test_lo_que_no_es_una_imagen_se_rechaza_aunque_lo_diga_la_extension():
    for fake in (b"esto es texto", b"MZ\x90\x00 ejecutable", b"%PDF-1.7 ...", b"\x00" * 100):
        with pytest.raises(ImageError, match="no es una imagen"):
            process_image(fake)
    assert vision.has_image_extension("foto.JPG") and vision.has_image_extension("a.heic") and not vision.has_image_extension("notas.txt")
    assert not vision.has_image_extension("dibujo.svg")


def test_una_imagen_con_otro_nombre_se_reconoce_por_su_contenido():
    out = process_image(png_bytes())
    assert out.mime == "image/png"


# ------------------------------------------------------------------ limpieza de metadatos ---------------------------------------------
def test_se_quitan_exif_y_gps():
    original = photo_with_exif()
    assert b"MarcaSecretaPhone" in original and b"Exif" in original            # el original SÍ los lleva
    out = process_image(original)
    assert out.had_metadata is True
    assert out.mime == "image/jpeg"
    for secret in (b"MarcaSecretaPhone", b"Modelo-X-12345", b"2026:11:15", b"Exif", b"GPS"):
        assert secret not in out.data, secret
    reopened = Image.open(io.BytesIO(out.data))
    assert len(reopened.getexif()) == 0
    assert reopened.getexif().get_ifd(0x8825) == {}
    assert "exif" not in reopened.info and "icc_profile" not in reopened.info


def test_la_orientacion_se_aplica_antes_de_tirar_el_exif():
    out = process_image(photo_with_exif(size=(40, 20), orientation=6))         # 6 = girada 90°: una foto vertical guardada apaisada
    assert (out.width, out.height) == (20, 40)
    assert len(Image.open(io.BytesIO(out.data)).getexif()) == 0


def test_png_pierde_textos_y_perfil_de_color():
    info = PngImagePlugin.PngInfo()
    info.add_text("Comment", "TextoSecretoDelPng")
    info.add_text("Author", "Persona Privada")
    original = png_bytes(pnginfo=info, icc_profile=b"perfil-falso" * 20)
    assert b"TextoSecretoDelPng" in original
    out = process_image(original)
    assert out.had_metadata is True
    assert b"TextoSecretoDelPng" not in out.data and b"Persona Privada" not in out.data and b"iCCP" not in out.data
    assert Image.open(io.BytesIO(out.data)).info.get("icc_profile") is None


def test_una_imagen_sin_metadatos_se_reconoce_como_tal():
    assert process_image(png_bytes()).had_metadata is False


# ------------------------------------------------------------------ forma de la imagen ---------------------------------------------------
def test_se_reduce_a_2048_conservando_la_proporcion():
    big = Image.new("RGB", (5000, 3000), (1, 2, 3))
    buf = io.BytesIO()
    big.save(buf, "JPEG")
    out = process_image(buf.getvalue())
    assert max(out.width, out.height) == vision.MAX_SIDE and out.resized is True
    assert abs(out.width / out.height - 5000 / 3000) < 0.01
    assert (out.original_width, out.original_height) == (5000, 3000)
    small = process_image(png_bytes((300, 200)))
    assert (small.width, small.height) == (300, 200) and small.resized is False


def test_conserva_la_transparencia_del_png():
    out = process_image(png_bytes((10, 10), mode="RGBA"))
    img = Image.open(io.BytesIO(out.data))
    assert out.mime == "image/png" and img.mode == "RGBA" and img.getpixel((0, 0))[3] == 128


def test_paleta_con_transparencia_y_otros_modos():
    pal = Image.new("P", (8, 8))
    buf = io.BytesIO()
    pal.save(buf, "PNG", transparency=0)
    assert Image.open(io.BytesIO(process_image(buf.getvalue()).data)).mode == "RGBA"
    cmyk = io.BytesIO()
    Image.new("CMYK", (8, 8), (0, 0, 0, 0)).save(cmyk, "JPEG")
    assert Image.open(io.BytesIO(process_image(cmyk.getvalue()).data)).mode == "RGB"
    gray = io.BytesIO()
    Image.new("L", (8, 8), 100).save(gray, "PNG")
    assert Image.open(io.BytesIO(process_image(gray.getvalue()).data)).mode == "L"


def test_gif_animado_se_queda_con_el_primer_fotograma():
    frames = [Image.new("RGB", (16, 16), c) for c in ((255, 0, 0), (0, 255, 0), (0, 0, 255))]
    buf = io.BytesIO()
    frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:], duration=100, loop=0)
    out = process_image(buf.getvalue())
    assert out.first_frame_only is True and out.mime == "image/png"
    px = Image.open(io.BytesIO(out.data)).convert("RGB").getpixel((0, 0))
    assert px[0] > 200 and px[1] < 60                                    # el primero: rojo


def test_webp_se_acepta():
    buf = io.BytesIO()
    Image.new("RGB", (30, 30), (5, 6, 7)).save(buf, "WEBP")
    out = process_image(buf.getvalue())
    assert (out.width, out.height) == (30, 30) and out.mime == "image/png"


def test_imagen_enorme_en_pixeles_se_rechaza_sin_decodificar():
    huge = io.BytesIO()
    Image.new("1", (9000, 9000)).save(huge, "PNG")                       # 81 Mpx: pesa poco comprimida, pero descomprimida no
    assert len(huge.getvalue()) < 1_000_000
    with pytest.raises(ImageError, match="demasiado grande"):
        process_image(huge.getvalue())


def test_imagen_truncada_o_corrupta_se_rechaza_con_un_error_claro():
    good = photo_with_exif((200, 200))
    for broken in (good[: len(good) // 3], good[:30], png_bytes()[:40], b"\x89PNG\r\n\x1a\n" + b"\x00" * 50):
        with pytest.raises(ImageError, match="dañada|no es una imagen"):
            process_image(broken)


def test_el_resultado_respeta_el_tope_de_tamano():
    noise = Image.frombytes("RGB", (2048, 2048), os.urandom(2048 * 2048 * 3))
    buf = io.BytesIO()
    noise.save(buf, "PNG")
    assert len(buf.getvalue()) > vision.MAX_OUTPUT_BYTES
    out = process_image(buf.getvalue())
    assert len(out.data) <= vision.MAX_OUTPUT_BYTES and out.mime == "image/jpeg"


def test_sin_pillow_las_imagenes_se_desactivan_con_un_mensaje(monkeypatch):
    data = png_bytes()                                                  # se crea antes de «quitar» Pillow
    monkeypatch.setitem(sys.modules, "PIL", None)
    assert vision.available() is False
    with pytest.raises(VisionUnavailable, match="praxeon\\[vision\\]"):
        process_image(data)
    cap = vision.capability("openai", "gpt-4o")
    assert cap["available"] is False and cap["supported"] is False and "Pillow" in cap["reason"]


def test_tokens_estimados():
    assert vision.estimate_tokens(1, 1) == 85
    assert vision.estimate_tokens(750, 750) == 750
    assert vision.estimate_tokens(2048, 2048) == 1600
    assert vision.estimate_tokens(0, 0) == 85


# ------------------------------------------------------------------ capacidad de visión por modelo ---------------------------------------
@pytest.mark.parametrize(
    "provider,model,expected",
    [
        ("anthropic", "claude-sonnet-4-5", True), ("anthropic", "claude-3-haiku-20240307", True), ("anthropic", "claude-2.1", False),
        ("openai", "gpt-4o-mini", True), ("openai", "gpt-4.1", True), ("openai", "gpt-3.5-turbo", False), ("openai", "o1-mini", False),
        ("gemini", "gemini-2.0-flash", True), ("gemini", "", False),   # sin nombre de modelo no se da por capaz
        ("groq", "llama-3.2-90b-vision-preview", True), ("groq", "llama-3.3-70b-versatile", False), ("groq", "meta-llama/llama-4-scout-17b", True),
        ("ollama", "llava:13b", True), ("ollama", "llama3.2-vision", True), ("ollama", "qwen2.5-coder:7b", False), ("ollama", "gemma3:4b", True),
        ("openrouter", "anthropic/claude-3.5-sonnet", True), ("openrouter", "meta-llama/llama-3.1-8b", False),
        ("claude_cli", "sonnet", False), ("simulator", "", True), ("desconocido", "x", False), (None, None, False),
    ],
)
def test_capacidad_de_vision(provider, model, expected, monkeypatch):
    monkeypatch.delenv("PRAXEON_VISION_MODELS", raising=False)
    assert vision.model_supports_vision(provider, model) is expected


def test_la_persona_puede_declarar_modelos_con_vision(monkeypatch):
    assert vision.model_supports_vision("ollama", "mi-modelo-casero") is False
    monkeypatch.setenv("PRAXEON_VISION_MODELS", " mi-modelo-casero , otro ")
    assert vision.model_supports_vision("ollama", "Mi-Modelo-Casero:7b") is True


@pytest.mark.parametrize(
    "provider,base_url,remote",
    [("openai", None, True), ("anthropic", None, True), ("gemini", None, True), ("groq", None, True), ("openrouter", None, True),
     ("ollama", None, False), ("ollama", "http://localhost:11434", False), ("ollama", "http://127.0.0.1:11434", False),
     ("ollama", "http://192.168.1.50:11434", True), ("openai", "http://127.0.0.1:8080/v1", False), ("openai", "https://api.openai.com/v1", True),
     ("simulator", None, False), ("claude_cli", None, False),
     ("openai", "http://0.0.0.0:8080/v1", False), ("openai", "http://[::1]:8080/v1", False), ("openai", "http://127.5.5.5/v1", False),
     ("openai", "http://mi-servidor.local/v1", True), ("openai", "http://localhost.evil.com/v1", True), ("openai", "http://10.0.0.5/v1", True)],
)
def test_si_la_imagen_sale_de_la_maquina(provider, base_url, remote):
    assert vision.is_remote(provider, base_url) is remote


def test_capability_resume_para_la_interfaz(monkeypatch):
    monkeypatch.delenv("PRAXEON_VISION_MODELS", raising=False)
    ok = vision.capability("openai", "gpt-4o")
    assert ok == {"available": True, "supported": True, "remote": True, "provider": "openai", "model": "gpt-4o", "reason": None}
    no = vision.capability("groq", "llama-3.3-70b-versatile")
    assert no["supported"] is False and "no admite imágenes" in no["reason"] and "llama-3.3-70b-versatile" in no["reason"]
    assert "CLI de Claude" in vision.capability("claude_cli", "sonnet")["reason"]
    assert vision.capability("ollama", "llava", "http://localhost:11434")["remote"] is False


# ------------------------------------------------------------------ bloques por proveedor ------------------------------------------------
IM = [{"mime": "image/png", "data": base64.b64encode(b"PNGDATA").decode()}]


def test_bloque_openai():
    assert vision.openai_content("mira", IM) == [
        {"type": "text", "text": "mira"},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{IM[0]['data']}"}},
    ]


def test_bloque_anthropic_pone_la_imagen_antes_del_texto():
    blocks = vision.anthropic_content("mira", IM)
    assert blocks[0] == {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": IM[0]["data"]}}
    assert blocks[-1] == {"type": "text", "text": "mira"}


def test_bloque_ollama():
    assert vision.ollama_images(IM) == [IM[0]["data"]]


def test_valid_images_descarta_lo_raro():
    good = {"mime": "image/jpeg", "data": "QUJD"}
    junk = [good, {"mime": "text/plain", "data": "QUJD"}, {"mime": "image/png", "data": "no es base64 !!"}, {"mime": "image/png"}, "x", None, {"data": "QUJD"}]
    assert vision.valid_images(junk) == [good]
    assert vision.valid_images(None) == []
