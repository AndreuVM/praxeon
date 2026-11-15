"""Catálogo de herramientas de agy que el usuario puede activar a mano (FE-015, nota N-020).

``agy`` se usa como generador de texto y todas sus herramientas están bloqueadas por un hook (``agent_llm.AgyCLILLM``). Aquí se
define QUÉ puede desbloquear el usuario, por grupos y con su nivel de riesgo. Lo activado lo ejecuta agy por su cuenta, **sin pasar
por la política, el supervisor ni el sandbox de PRAXEON**; por eso cada grupo exige una confirmación explícita (que el servidor
comprueba, no solo la interfaz) y queda anotado en el log de eventos de la sesión.

Qué ofrece agy en modo ``-p`` se comprobó con el agy real (nota N-021 de MarQ): la sesión principal declara 17 herramientas, y
las imágenes no son una herramienta suelta sino un SUBAGENTE ``image-generator`` cuyo hook ve cada llamada. El navegador no se
puede ofrecer por este canal (no está declarado, no hay subagente de navegador y ``/browser`` no se expande en ``-p``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from praxeon import vision

RISK_LEVELS = ("bajo", "medio", "alto", "critico")


@dataclass(frozen=True)
class ToolGroup:
    id: str
    title: str
    risk: str
    summary: str
    warning: str
    tools: tuple[str, ...] = ()            # herramientas que el hook deja pasar
    subagents: tuple[str, ...] = ()        # tipos de subagente que se pueden invocar
    caps: tuple[tuple[str, int], ...] = ()  # tope de llamadas por turno de cada herramienta
    needs_images: bool = False             # necesita Pillow para recoger y limpiar lo generado

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "title": self.title, "risk": self.risk, "summary": self.summary, "warning": self.warning,
            "tools": list(self.tools) + [f"invoke_subagent:{s}" for s in self.subagents], "caps": dict(self.caps),
            "needs_images": self.needs_images,
        }


GROUPS: dict[str, ToolGroup] = {
    g.id: g
    for g in (
        ToolGroup(
            id="images",
            title="Generación de imágenes",
            risk="medio",
            summary="Permite que agy genere imágenes con su subagente «image-generator». Las imágenes se recogen, se limpian de metadatos y se muestran en el chat.",
            warning=(
                "agy ejecuta esto por su cuenta, sin pasar por la política, el supervisor ni el sandbox de PRAXEON. Cada imagen gasta cuota de tu cuenta de "
                "Google y el texto que escribas en la misión sale hacia el servicio de imágenes. El subagente puede enviar mensajes por su canal interno "
                "(send_message). Máximo 4 imágenes por turno."
            ),
            tools=("generate_image", "send_message"),
            subagents=("image-generator",),
            caps=(("generate_image", 4), ("invoke_subagent", 2), ("send_message", 12)),
            needs_images=True,
        ),
        ToolGroup(
            id="web_search",
            title="Búsqueda web",
            risk="medio",
            summary="Permite que agy busque en la web con su herramienta «search_web».",
            warning=(
                "agy ejecuta esto por su cuenta, sin pasar por la política, el supervisor ni el sandbox de PRAXEON. Cada consulta sale hacia el buscador de "
                "Google y puede contener texto de tu misión; lo que devuelve son datos externos no fiables. Máximo 8 búsquedas por turno."
            ),
            tools=("search_web",),
            caps=(("search_web", 8),),
        ),
    )
}


def catalog() -> list[dict[str, Any]]:
    """Grupos activables, para la interfaz."""
    return [g.as_dict() for g in GROUPS.values()]


def validate(selected: list[str] | None, acked: list[str] | None) -> tuple[str, ...]:
    """Grupos pedidos ya comprobados. Lanza ``ValueError`` (mensaje para el usuario) si hay uno desconocido, repetido, sin confirmar o no usable.

    La confirmación se exige AQUÍ, en el servidor: una petición que active herramientas sin confirmarlas se rechaza.
    """
    chosen = list(selected or [])
    if len(set(chosen)) != len(chosen):
        raise ValueError("Hay grupos de herramientas de agy repetidos.")
    unknown = [g for g in chosen if g not in GROUPS]
    if unknown:
        raise ValueError(f"Herramientas de agy desconocidas: {', '.join(sorted(map(str, unknown)))}.")
    unconfirmed = [g for g in chosen if g not in set(acked or [])]
    if unconfirmed:
        names = ", ".join(GROUPS[g].title for g in unconfirmed)
        raise ValueError(f"Falta la confirmación explícita para activar: {names}. Esas herramientas las ejecuta agy por su cuenta, sin la política de PRAXEON.")
    if any(GROUPS[g].needs_images for g in chosen) and not vision.available():
        raise ValueError(f"La generación de imágenes necesita Pillow para limpiar lo generado. {vision.INSTALL_HINT}")
    return tuple(chosen)


def build_policy(selected: tuple[str, ...] | list[str]) -> dict[str, Any]:
    """Política que lee el hook (``agy_hook.py``)."""
    tools: list[str] = []
    subagents: list[str] = []
    caps: dict[str, int] = {}
    for gid in selected:
        group = GROUPS[gid]
        tools.extend(t for t in group.tools if t not in tools)
        subagents.extend(s for s in group.subagents if s not in subagents)
        for name, cap in group.caps:
            caps[name] = max(cap, caps.get(name, 0))
    return {"tools": tools, "subagents": subagents, "caps": caps}
