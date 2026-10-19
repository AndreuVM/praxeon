"""Reparto de los modelos locales (LAYA y KEV) entre GPU y CPU según el hardware del equipo.

Un equipo con poca VRAM no puede tener LAYA y KEV a la vez en la GPU: con una tarjeta de 8 GB, KEV-4B ocupa casi todo.
El plan se calcula con la VRAM total de la GPU y el tamaño estimado de cada modelo:

1. caben los dos            -> los dos en la GPU;
2. cabe KEV (el grande)     -> KEV en la GPU y LAYA (pequeño) en la CPU, ambos residentes, sin cargas ni descargas;
3. solo cabe LAYA           -> LAYA en la GPU y KEV en la CPU (lento; mejor un KEV más pequeño);
4. no hay GPU o no cabe nada -> todo en la CPU.

Se puede forzar con ``LAYA_DEVICE`` y ``KEV_DEVICE`` (``cpu``, ``cuda``…); ``auto`` (por defecto) usa este plan.
Solo consulta ``nvidia-smi``: no importa torch.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional, Tuple

logger = logging.getLogger("praxeon.providers.hardware")

LAYA_VRAM_MB = 2000  # pesos de `typed-decisions` (~1,7 GB) y activaciones
KEV_MB_PER_BILLION = 1850  # bfloat16 medido: KEV-4B ocupa ~7,9 GB con su caché
KEV_BASE_MB = 400
MARGIN_MB = 300
GRAPHS_HEADROOM_MB = 2000  # los CUDA graphs de KEV reservan memoria extra; con poco margen van peor
DEFAULT_KEV_RUN = "jaredpalmer/kev-4b"


@dataclass(frozen=True)
class GpuInfo:
    name: str
    total_mb: int
    free_mb: int


@dataclass(frozen=True)
class PlacementPlan:
    laya_device: str
    kev_device: str
    kev_cuda_graphs: bool
    reason: str
    gpu: Optional[GpuInfo] = None


def detect_gpu() -> Optional[GpuInfo]:
    """Primera GPU NVIDIA visible, o None si no hay controlador o GPU."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip().splitlines()
        name, total, free = [part.strip() for part in out[0].rsplit(",", 2)]
        return GpuInfo(name=name, total_mb=int(float(total)), free_mb=int(float(free)))
    except Exception as exc:  # noqa: BLE001 - sin GPU utilizable cualquier fallo equivale a «no hay»
        logger.debug("nvidia-smi no está disponible: %s", exc)
        return None


def estimate_kev_vram_mb(run: Optional[str]) -> int:
    """VRAM estimada para servir un checkpoint de KEV en bfloat16, a partir del tamaño del nombre (kev-4b, kev-0.8b…)."""
    match = re.search(r"(\d+(?:\.\d+)?)\s*b\b", (run or DEFAULT_KEV_RUN).lower())
    billions = float(match.group(1)) if match else 4.0
    return int(billions * KEV_MB_PER_BILLION + KEV_BASE_MB)


_KEV_IN_USE: Optional[bool] = None  # None: nadie lo ha declarado; False: solo LAYA; True: KEV o cascada en uso (no se revierte)


def declare_kev_in_use(in_use: bool) -> None:
    """Los proveedores declaran si este proceso usa KEV. Solo LAYA (False) deja la GPU para LAYA aunque KEV_AUTOSTART esté activo.

    «En uso» es permanente: una vez creada una cascada o un KEV, el reparto sigue el plan por hardware. «Solo LAYA» solo se
    declara si nadie había declarado antes, para no quitarle la GPU a un KEV ya previsto.
    """
    global _KEV_IN_USE
    if in_use:
        _KEV_IN_USE = True
    elif _KEV_IN_USE is None:
        _KEV_IN_USE = False


def _kev_wanted() -> bool:
    autostart = os.getenv("KEV_AUTOSTART", "").strip().lower() in ("1", "true", "yes")
    return autostart and _KEV_IN_USE is not False


def plan_placement(
    gpu: Optional[GpuInfo] = None, kev_run: Optional[str] = None, kev_wanted: Optional[bool] = None
) -> PlacementPlan:
    """Decide dónde corre cada modelo (ver el docstring del módulo)."""
    gpu = gpu if gpu is not None else detect_gpu()
    if gpu is None:
        return PlacementPlan("cpu", "cpu", False, "sin GPU NVIDIA: todo en la CPU")
    kev_wanted = _kev_wanted() if kev_wanted is None else kev_wanted
    kev_need = estimate_kev_vram_mb(kev_run or os.getenv("KEV_RUN"))
    laya_need = LAYA_VRAM_MB
    budget = gpu.total_mb - MARGIN_MB

    if not kev_wanted:
        if laya_need <= budget:
            return PlacementPlan("cuda", "cpu", False, f"LAYA cabe en {gpu.name} y KEV no se usa", gpu)
        return PlacementPlan("cpu", "cpu", False, f"LAYA no cabe en {gpu.name} ({gpu.total_mb} MB)", gpu)
    if kev_need + laya_need <= budget:
        graphs = kev_need + laya_need + GRAPHS_HEADROOM_MB <= budget
        return PlacementPlan("cuda", "cuda", graphs, f"LAYA y KEV caben juntos en {gpu.name} ({gpu.total_mb} MB)", gpu)
    if kev_need <= budget:
        return PlacementPlan(
            "cpu", "cuda", False,
            f"KEV (~{kev_need} MB) casi llena {gpu.name} ({gpu.total_mb} MB): KEV en la GPU y LAYA en la CPU", gpu,
        )
    if laya_need <= budget:
        return PlacementPlan(
            "cuda", "cpu", False,
            f"KEV (~{kev_need} MB) no cabe en {gpu.name} ({gpu.total_mb} MB): LAYA en la GPU y KEV en la CPU; "
            "con un KEV más pequeño iría más rápido", gpu,
        )
    return PlacementPlan("cpu", "cpu", False, f"nada cabe en {gpu.name} ({gpu.total_mb} MB): todo en la CPU", gpu)


def _forced(env_name: str, requested: Optional[str]) -> Optional[str]:
    value = (requested or "").strip().lower()
    if value and value != "auto":
        return value
    value = os.getenv(env_name, "").strip().lower()
    return value if value and value != "auto" else None


def laya_device(requested: Optional[str] = None) -> Tuple[str, str]:
    """Dispositivo de LAYA y el motivo. Lo pedido o ``LAYA_DEVICE`` manda; con «auto» decide el plan."""
    forced = _forced("LAYA_DEVICE", requested)
    if forced:
        return forced, "forzado por configuración"
    plan = plan_placement()
    device = plan.laya_device
    if device == "cuda" and plan.gpu is not None and plan.gpu.free_mb < LAYA_VRAM_MB + MARGIN_MB:
        # el plan cabía, pero otra aplicación está usando la GPU ahora mismo
        return "cpu", f"solo hay {plan.gpu.free_mb} MB libres de VRAM"
    return device, plan.reason


def kev_device(requested: Optional[str] = None, run: Optional[str] = None) -> Tuple[str, bool, str]:
    """Dispositivo del servidor de KEV, si activar sus CUDA graphs y el motivo."""
    plan = plan_placement(kev_run=run, kev_wanted=True)
    forced = _forced("KEV_DEVICE", requested)
    if forced:
        return forced, plan.kev_cuda_graphs and forced.startswith("cuda"), "forzado por configuración"
    return plan.kev_device, plan.kev_cuda_graphs, plan.reason
