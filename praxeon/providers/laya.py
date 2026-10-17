"""Proveedor de razonamiento semántico basado en LAYA (System One Local & Hosted).

Implementa la arquitectura no-autorregresiva de LAYA con sus tres primitivas de decisión:
1. choice: Selección categórica y distribución de probabilidad (ALLOW, REPLAN, BLOCK, ABSTAIN).
2. score: Evaluación continua y posición ponderada de progreso hacia la meta (0.0 a 1.0).
3. noul: Probabilidades binarias calibradas (is_loop, is_grounded, is_novel).

Soporta backends:
- 'auto': Autodetección entre paquete local, endpoint alojado o simulación calibrada.
- 'local': Motor local (vía biblioteca laya / ONNX / simulación local calibrada).
- 'hosted': Servicio HTTP remoto o microservicio REST de LAYA.
- 'simulated': Motor determinista calibrado para pruebas y benchmarks reproducibles offline.
"""

from datetime import datetime, timezone
import json
import logging
import math

import os
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from praxeon.domain.action import ActionCandidate
from praxeon.domain.assessment import ProviderAssessment
from praxeon.providers.base import BaseReasoningProvider
from praxeon.policy.loop_detector import estimate_loop_probability
from praxeon.providers.action_record import build_action_record
from praxeon.providers.context import ProviderContext, ProviderContextBuilder
from praxeon.providers.hardware import laya_device
from praxeon.providers.supervisor_trace import record_supervisor_step, trace_path
from praxeon.providers.systemone import QUESTIONS, parse_answers

logger = logging.getLogger("praxeon.providers.laya")

def _laya_sdk_installed() -> bool:
    """Comprueba si el paquete `laya` está instalado sin importarlo (arrastra torch y tarda segundos)."""
    import importlib.util

    return importlib.util.find_spec("laya") is not None


# Checkpoint de LAYA: «typed-decisions» (entrenado para decisiones tipadas) o «auto» (el Router elige por idioma)
LAYA_CHECKPOINT = os.getenv("LAYA_CHECKPOINT", "typed-decisions").strip() or "typed-decisions"
NEURAL_LOOP_CAP = 0.60  # por debajo del umbral de bloqueo por bucle (0.65): el modelo solo no puede bloquear


def _env_float(name: str, default: float, low: float, high: float) -> float:
    try:
        return min(high, max(low, float(os.getenv(name, "").strip() or default)))
    except ValueError:
        return default


# Temperatura posterior sobre las probabilidades de la decisión (p' ∝ p^(1/T)). Por defecto 1.0 (sin ajuste): las
# probabilidades de un checkpoint sin ajuste fino al dominio son casi uniformes y una T<1 ajustada sobre un banco sintético
# las convierte en «confianza» que no existe (0,33 -> 0,56 supera el umbral de veto). Solo debe fijarse con trazas reales:
# `scripts/fit_laya_temperature.py` propone un valor (con el banco sintético sale ~0,3). La librería no admite T < 0,5.
LAYA_TEMPERATURE = _env_float("LAYA_TEMPERATURE", 1.0, 0.05, 5.0)

# Tamaños con los que se prueba el estado hasta que el modelo lo lea entero: (pasos recientes, caracteres por observación)
# Qué ve el modelo de la acción propuesta: «record» (registro canónico construido por el arnés, por defecto) o «thought»
# (la descripción libre del agente). Medido sobre el banco con razonamientos reales: el texto libre hace casi uniformes las
# probabilidades, repite el vocabulario de los vetos («considerada destructiva») y lo controla el propio agente.
LAYA_ACTION_VIEW = os.getenv("LAYA_ACTION_VIEW", "record").strip().lower() or "record"

_STATE_FITS = ((6, 300), (4, 160), (3, 90), (2, 50), (1, 30), (0, 0))
_ARGS_CHARS = 400

_ROUTERS: Dict[Optional[str], Any] = {}
_ROUTER_LOCK = threading.Lock()


def resolve_device(requested: Optional[str] = None) -> str:
    """Dispositivo de inferencia: lo pedido o `LAYA_DEVICE`; con «auto» decide el plan según el hardware (hardware.py)."""
    device, reason = laya_device(requested)
    if device.startswith("cuda"):
        try:
            import torch  # type: ignore

            if not torch.cuda.is_available():
                logger.info("LAYA: PyTorch sin CUDA; se usa la CPU.")
                return "cpu"
        except Exception:  # noqa: BLE001
            return "cpu"
    logger.info("LAYA en %s: %s", device, reason)
    return device



def build_laya_state(ctx: ProviderContext, max_steps: int, obs_chars: int) -> str:
    """Estado para el modelo, con el mismo formato que el gestor de contexto pero acotado a `max_steps` y `obs_chars`.

    El checkpoint lee unos 1.024 tokens en total y las preguntas ocupan más de la mitad: si el estado no cabe, recorta por
    el final y pierde la acción propuesta. Se prueba con tamaños decrecientes hasta que el modelo lo lee entero (ver
    `_STATE_FITS`). Poner la acción primero evitaba el recorte, pero bajó el acierto de 0,80 a 0,61 en el banco de escenarios.
    """
    cand = ctx.candidate_action or {}
    args = cand.get("arguments") or {}
    args_text = str(args)
    if len(args_text) > _ARGS_CHARS:
        args_text = args_text[:_ARGS_CHARS] + "...[recortado]"
    lines = [f"OBJETIVO: {ctx.goal}", "Criterios de éxito:"]
    lines += [f"- {c}" for c in (ctx.success_criteria or ["tarea completada"])]
    env = ctx.metadata.get("environment") if isinstance(ctx.metadata, dict) else None
    lines.append(f"ENTORNO: {env or 'cwd=., mode=local_restricted'}")
    history = [h for h in ctx.history_window if isinstance(h, dict)]
    if max_steps and history:
        lines += ["", "PASOS Y OBSERVACIONES RECIENTES:"]
        for h in history[-max_steps:]:
            h_args = json.dumps(h.get("arguments") or {}, ensure_ascii=False)[:120]
            obs = " ".join(str(h.get("observation") or "").split())[:obs_chars]
            lines.append(f" - PASO {h.get('id')} [{h.get('tool')}({h_args})]: {obs}")
    if LAYA_ACTION_VIEW == "record":
        lines += ["", build_action_record(cand, history)]
    else:
        lines += ["", f"ACCIÓN PROPUESTA: {cand.get('description') or ''} -> {cand.get('tool_name')}({args_text})"]
    return "\n".join(lines)


def calibrate_choice(choice: Dict[str, Any], temperature: float) -> Dict[str, Any]:
    """Aplica la temperatura posterior a las probabilidades de la decisión; la etiqueta (argmax) no cambia."""
    probs = {str(k): float(v) for k, v in (choice.get("probabilities") or {}).items()}
    if not probs or abs(temperature - 1.0) < 1e-9:
        return choice
    logits = {k: math.log(max(v, 1e-9)) / temperature for k, v in probs.items()}
    top = max(logits.values())
    expo = {k: math.exp(v - top) for k, v in logits.items()}
    total = sum(expo.values())
    calibrated = {k: v / total for k, v in expo.items()}
    label = str(choice.get("label"))
    return {**choice, "probabilities": calibrated, "confidence": calibrated.get(label, choice.get("confidence")),
            "raw_probabilities": probs, "temperature": temperature}


class LayaDecisionPrimitives(BaseModel):
    """Estructura de las tres primitivas fundamentales retornadas por LAYA."""
    model_config = ConfigDict(frozen=True)

    choice: Dict[str, Any] = Field(
        default_factory=lambda: {"label": "ALLOW", "probabilities": {"ALLOW": 1.0}, "confidence": 1.0}
    )
    score: float = 1.0
    noul: Dict[str, float] = Field(
        default_factory=lambda: {"is_loop": 0.0, "is_grounded": 1.0, "is_novel": 1.0}
    )


class LayaProvider(BaseReasoningProvider):
    """Adaptador de razonamiento semántico para LAYA con soporte para choice, score y noul.

    La construcción del `ProviderAssessment` a partir de las primitivas es común a los supervisores System-1
    (p. ej. KEV): basta con cambiar `PROVIDER_ID` y `CODE_PREFIX` y sustituir `_infer_primitives`.
    """

    PROVIDER_ID = "laya"
    CODE_PREFIX = "LAYA"
    # `advisory`: su etiqueta es una señal de aviso (un BLOCK probable pide revisión humana, un REPLAN se ignora) y nunca
    # veta por sí sola; `authoritative`: la política obedece su BLOCK/REPLAN. LAYA sin ajuste fino a trazas de agente
    # decide casi al azar (ver informe de auditoría), por eso es consultivo por defecto.
    VETO_MODE_ENV = "LAYA_VETO_MODE"
    DEFAULT_VETO_MODE = "advisory"

    def __init__(
        self,
        backend: str = "auto",
        model_name: str = "laya-v1-calibrated",
        endpoint_url: Optional[str] = None,
        auth_token: Optional[str] = None,
        context_builder: Optional[ProviderContextBuilder] = None,
        max_context_tokens: int = 2048,
        timeout: float = 5.0,
        device: str = "auto",
    ):
        self.name = "laya"
        self.requested_backend = backend.lower()
        self.model_name = model_name

        trusted_endpoint = os.getenv("LAYA_ENDPOINT_URL")
        # Finding 8: Prevenir filtración de LAYA_AUTH_TOKEN del entorno a endpoints personalizados del llamador
        if endpoint_url:
            self.endpoint_url = endpoint_url
            if auth_token:
                self.auth_token = auth_token
            elif trusted_endpoint and endpoint_url == trusted_endpoint:
                self.auth_token = os.getenv("LAYA_AUTH_TOKEN")
            else:
                # Endpoint personalizado sin token explícito: no filtrar credenciales del servidor
                self.auth_token = None
        else:
            self.endpoint_url = trusted_endpoint
            self.auth_token = auth_token or os.getenv("LAYA_AUTH_TOKEN")
        self.context_builder = context_builder or ProviderContextBuilder(default_max_tokens=max_context_tokens)
        self.max_context_tokens = max_context_tokens
        self.timeout = timeout
        self.device = device
        self._is_warmed_up = False

        self.backend = self._resolve_backend()

    def _resolve_backend(self) -> str:
        """Determina el backend efectivo a utilizar."""
        if self.requested_backend in ("hosted", "remote"):
            return "hosted"
        if self.requested_backend == "simulated":
            return "simulated"
        if self.requested_backend == "local":
            if _laya_sdk_installed():
                return "local"
            logger.info(
                "Backend 'local' solicitado pero 'laya' no está instalado. "
                "Ejecute 'pip install praxeon[laya]' o 'pip install laya' para inferencia neuronal con pesos locales. "
                "Utilizando motor local calibrado sin pesos externos."
            )
            return "simulated"

        # Modo 'auto'
        if self.endpoint_url:
            return "hosted"
        return "local" if _laya_sdk_installed() else "simulated"

    def is_available(self) -> bool:
        """Comprueba si el proveedor LAYA está disponible para realizar inferencias."""
        if self.backend == "hosted":
            return bool(self.endpoint_url)
        return True

    def warmup(self) -> bool:
        """Realiza un pase de calentamiento para verificar latencia y disponibilidad."""
        start_t = time.perf_counter()
        try:
            # Consulta mínima de prueba
            dummy_ctx = ProviderContext(
                session_id="warmup_session",
                goal="Warmup check",
                history_window=[],
                active_evidence=[],
                candidate_action={"id": "warmup", "description": "ping"},
                formatted_prompt="GOAL: Warmup check\nCANDIDATE ACTION: ping",
            )
            _ = self._infer_primitives(dummy_ctx)
            self._is_warmed_up = True
            logger.info(f"LAYA warmup completado en {(time.perf_counter() - start_t) * 1000.0:.2f} ms ({self.backend})")
            return True
        except Exception as e:
            logger.warning(f"Fallo en warmup de LAYA ({self.backend}): {e}")
            return False

    def _infer_primitives_hosted(self, ctx: ProviderContext) -> LayaDecisionPrimitives:
        """Invoca el servicio HTTP remoto de LAYA."""
        if not self.endpoint_url:
            raise ValueError("endpoint_url no configurado para backend hosted de LAYA.")

        payload = ctx.to_laya_payload()
        payload["model"] = self.model_name
        req_data = json.dumps(payload).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Praxeon/1.0.0",
        }
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"

        req = urllib.request.Request(self.endpoint_url, data=req_data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # nosec B310
            resp_code = resp.getcode()
            if resp_code != 200:
                raise RuntimeError(f"LAYA endpoint retornó status {resp_code}")
            raw_body = resp.read().decode("utf-8")
            data = json.loads(raw_body)

        choice_data = data.get("choice", {"label": "ALLOW", "probabilities": {"ALLOW": 1.0}, "confidence": 1.0})
        score_val = float(data.get("score", 1.0))
        noul_data = data.get("noul", {"is_loop": 0.0, "is_grounded": 1.0, "is_novel": 1.0})

        return LayaDecisionPrimitives(
            choice=choice_data,
            score=score_val,
            noul=noul_data,
        )

    @property
    def veto_mode(self) -> str:
        explicit = (os.getenv(self.VETO_MODE_ENV, "") or "").strip().lower()
        if explicit in ("advisory", "authoritative"):
            return explicit
        # El motor de reglas calibrado (backend simulated) es determinista y se obedece; el consultivo es para el modelo neuronal
        return "authoritative" if self.backend == "simulated" else self.DEFAULT_VETO_MODE

    def _get_router(self):
        """Carga (una vez por proceso y dispositivo) el Router del paquete `laya`, que elige el checkpoint por idioma."""
        with _ROUTER_LOCK:
            device = resolve_device(self.device)
            router = _ROUTERS.get(device)
            if router is None:
                if os.name == "nt":
                    # Sin modo desarrollador Windows no permite enlaces simbólicos y la descarga de pesos falla
                    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
                from laya import Router  # type: ignore

                router = _ROUTERS[device] = Router(device=device)
            return router

    def _infer_primitives_local_sdk(self, ctx: ProviderContext) -> LayaDecisionPrimitives:
        """Inferencia neuronal con el paquete `laya` (Router.predict) en este equipo."""
        try:
            router = self._get_router()
        except Exception as exc:
            # No se pudo cargar el modelo (p. ej. sin red en el primer uso): se degrada al motor calibrado, a la vista
            logger.warning("LAYA local no pudo cargar el modelo (%s); se usa el motor calibrado.", exc)
            self.backend = "simulated"
            return self._infer_primitives_calibrated(ctx)
        kwargs = {} if LAYA_CHECKPOINT == "auto" else {"model": LAYA_CHECKPOINT}
        result: Dict[str, Any] = {}
        fit = (0, 0)
        for fit in _STATE_FITS:
            result = router.predict(build_laya_state(ctx, *fit), QUESTIONS, **kwargs)
            if not (result.get("usage") or {}).get("state_tokens_dropped"):
                break   # el modelo leyó el estado entero (la acción propuesta va al principio y nunca se pierde)
        primitives = parse_answers(result)
        choice = calibrate_choice(primitives.choice, LAYA_TEMPERATURE)
        primitives = primitives.model_copy(update={"choice": {**choice, "state_fit": {"steps": fit[0], "obs_chars": fit[1]}}})
        return self._combine_loop_evidence(primitives, ctx)

    def _combine_loop_evidence(self, primitives: LayaDecisionPrimitives, ctx: ProviderContext) -> LayaDecisionPrimitives:
        """La detección de bucles la decide la repetición comprobable; el modelo neuronal solo puede sumar hasta un tope.

        Medido sobre escenarios etiquetados, el modelo separa mal «bucle» de «paso nuevo» (sus probabilidades se solapan) y
        llegó a bloquear lecturas seguras. Las repeticiones exactas, en cambio, las detecta con precisión el motor de
        reglas, así que el valor final es el mayor de los dos, con el aporte neuronal acotado por debajo del umbral de bloqueo.
        """
        neural_loop = float(primitives.noul.get("is_loop", 0.0))
        rule_loop = float(self._infer_primitives_calibrated(ctx).noul.get("is_loop", 0.0))
        loop = max(rule_loop, min(neural_loop, NEURAL_LOOP_CAP))
        noul = {**primitives.noul, "is_loop": loop, "is_novel": 1.0 - loop, "neural_is_loop": neural_loop, "rule_is_loop": rule_loop}
        return primitives.model_copy(update={"noul": noul})

    def _infer_primitives_calibrated(self, ctx: ProviderContext) -> LayaDecisionPrimitives:
        """Inferencia local simulada de alta fidelidad y calibración matemática."""
        cand = ctx.candidate_action
        desc = (cand.get("description") or "").lower()
        tool_name = (cand.get("tool_name") or "").lower()
        args = cand.get("arguments") or {}
        req_evidence = cand.get("requires_evidence") or []
        evidence_lower = [e.lower() for e in ctx.active_evidence]

        cmd_str = str(args.get("command") or args.get("cmd") or args.get("raw") or desc).strip().lower()
        target_path = str(args.get("path") or args.get("file") or "").strip().lower()

        # 1. Bucle: la detección es del arnés (policy/loop_detector.py), común a todos los supervisores
        history = [h for h in ctx.history_window if isinstance(h, dict)]
        is_loop = estimate_loop_probability(history, tool_name, args)

        # 2. Comprobación de grounding (fundamentación fáctica y verificación de recursos en disco)
        is_grounded = 0.95
        workspace_dir = (
            (getattr(ctx, "metadata", None) or {}).get("workspace_root")
            or (getattr(ctx, "metadata", None) or {}).get("working_directory")
            or os.getcwd()
        )
        has_file_evidence = any(
            target_path and (target_path in ev or os.path.basename(target_path) in ev)
            for ev in evidence_lower
        )
        if tool_name in ("read_file", "view_file") and target_path:
            norm_target = os.path.expanduser(os.path.expandvars(target_path))
            full_path = norm_target if os.path.isabs(norm_target) else os.path.join(workspace_dir, norm_target)
            alt_path = norm_target if os.path.isabs(norm_target) else os.path.join(os.getcwd(), norm_target)
            if not (os.path.exists(full_path) or os.path.exists(alt_path)) and not has_file_evidence:
                is_grounded = 0.05
        elif tool_name in ("run_command", "run_script"):
            for runner in ("python ", "python3 ", "node ", "bash ", "sh "):
                if cmd_str.startswith(runner):
                    script_part = cmd_str[len(runner):].strip().split()[0].strip('"\'')
                    if script_part.endswith((".py", ".js", ".sh", ".ts")):
                        full_script = script_part if os.path.isabs(script_part) else os.path.join(workspace_dir, script_part)
                        alt_script = script_part if os.path.isabs(script_part) else os.path.join(os.getcwd(), script_part)
                        has_script_evidence = any(
                            script_part in ev or os.path.basename(script_part) in ev
                            for ev in evidence_lower
                        )
                        if not (os.path.exists(full_script) or os.path.exists(alt_script)) and not has_script_evidence:
                            is_grounded = 0.05
                    break

        if req_evidence:
            missing = [r for r in req_evidence if r.lower().strip() not in evidence_lower]
            if missing:
                is_grounded = min(is_grounded, 0.12)

        # 3. Comprobación de peligrosidad / acciones destructivas
        is_destructive = any(
            dest in str(args).lower() or dest in desc or dest in cmd_str
            for dest in ("rm -rf", "drop table", "truncate", "delete from", "format c:", "mkfs", "kill -9")
        )

        # 3.5. Detección de comandos innecesarios, ociosos o distractores
        idle_or_deviant_patterns = [
            "sleep ", "timeout ", "ping -t", "nmap", "wireshark",
            "cat /dev/urandom", "cowsay", "sl ", "fortune", "banner"
        ]
        is_unnecessary = any(p in cmd_str for p in idle_or_deviant_patterns)

        # 4. Cálculo de Score de progreso (0.0 a 1.0)
        if is_destructive:
            score = 0.05
        elif is_loop > 0.70 or is_unnecessary:
            score = 0.15
        elif is_grounded < 0.30:
            score = 0.20
        else:
            score = 0.88

        # 5. Determinación de Choice y distribución de probabilidades calibrada
        if is_destructive:
            label = "BLOCK"
            probs = {"BLOCK": 0.94, "ALLOW": 0.01, "REPLAN": 0.03, "ABSTAIN": 0.02}
            confidence = 0.94
        elif is_loop >= 0.70 or is_unnecessary or is_grounded < 0.30:
            label = "REPLAN"
            probs = {"REPLAN": 0.88, "ALLOW": 0.05, "BLOCK": 0.02, "ABSTAIN": 0.05}
            confidence = 0.88
        elif any(w in desc for w in ("incierto", "desconocido", "duda", "ambiguo")):
            label = "ABSTAIN"
            probs = {"ABSTAIN": 0.45, "ALLOW": 0.25, "REPLAN": 0.20, "BLOCK": 0.10}
            confidence = 0.40  # Baja confianza para disparar escalado
        else:
            label = "ALLOW"
            probs = {"ALLOW": 0.92, "REPLAN": 0.04, "BLOCK": 0.01, "ABSTAIN": 0.03}
            confidence = 0.92

        return LayaDecisionPrimitives(
            choice={"label": label, "probabilities": probs, "confidence": confidence},
            score=score,
            noul={
                "is_loop": is_loop,
                "is_grounded": is_grounded,
                "is_novel": round(1.0 - is_loop, 2),
            },
        )

    def _infer_primitives(self, ctx: ProviderContext) -> LayaDecisionPrimitives:
        """Enruta la inferencia al backend seleccionado."""
        if self.backend == "hosted":
            return self._infer_primitives_hosted(ctx)
        elif self.backend == "local":
            return self._infer_primitives_local_sdk(ctx)
        else:
            return self._infer_primitives_calibrated(ctx)

    def _get_resident_memory_mb(self) -> float:
        """Estima la memoria residente utilizada en megabytes."""
        try:
            import psutil  # type: ignore
            process = psutil.Process(os.getpid())
            return round(process.memory_info().rss / (1024 * 1024), 2)
        except Exception:
            return 0.0

    def evaluate_action(self, state: Any, action: ActionCandidate) -> ProviderAssessment:
        """Evalúa una acción candidata mediante LAYA emitiendo un ProviderAssessment estructurado."""
        start_t = time.perf_counter()

        # Construir contexto normalizado con ProviderContextBuilder
        ctx = self.context_builder.build(state, action, max_tokens=self.max_context_tokens)

        try:
            primitives = self._infer_primitives(ctx)
            elapsed_ms = (time.perf_counter() - start_t) * 1000.0
            if trace_path() is not None:
                fit = (primitives.choice or {}).get("state_fit") or {}
                record_supervisor_step(
                    state=build_laya_state(ctx, int(fit.get("steps", _STATE_FITS[0][0])), int(fit.get("obs_chars", _STATE_FITS[0][1]))),
                    questions=QUESTIONS, primitives=primitives, ctx=ctx, provider=self.name, backend=self.backend,
                    extra={"state_fit": fit})

            choice_data = primitives.choice
            score_val = primitives.score
            noul_data = primitives.noul

            loop_prob = noul_data.get("is_loop", 0.0)
            grounded_prob = noul_data.get("is_grounded", 1.0)
            progress_prob = score_val
            novelty_prob = noul_data.get("is_novel", 1.0 - loop_prob)

            # JEV analítico: producto de progreso, no-bucle y fundamentación
            analytical_jev = progress_prob * (1.0 - loop_prob) * grounded_prob

            px = self.CODE_PREFIX
            reason_codes: List[str] = [f"{px}_CHOICE_{choice_data.get('label', 'ALLOW')}"]
            if choice_data.get("label") == "BLOCK":
                reason_codes.append(f"{px}_DESTRUCTIVE_BLOCK")
            elif choice_data.get("label") == "REPLAN":
                reason_codes.append(f"{px}_UNNECESSARY_ACTION_REPLAN")
            elif choice_data.get("label") == "ALLOW":
                reason_codes.append(f"{px}_PROGRESS_APPROVED")
            if loop_prob >= 0.70:
                reason_codes.append(f"{px}_LOOP_PREVENTED")
            if grounded_prob < 0.30:
                reason_codes.append(f"{px}_UNGROUNDED_EVIDENCE")
            if score_val >= 0.75:
                reason_codes.append(f"{px}_HIGH_PROGRESS")

            metadata = {
                "provider": self.name,
                "backend": self.backend,
                "model_name": self.model_name,
                "device": self.device,
                "latency_ms": round(elapsed_ms, 2),
                "context_tokens": ctx.token_estimate,
                "truncated": ctx.truncated,
                "choice": choice_data,
                "score": score_val,
                "noul": noul_data,
                "veto_mode": self.veto_mode,
                "memory_mb": self._get_resident_memory_mb(),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }


            return ProviderAssessment(
                provider=self.name,
                model=self.model_name,
                available=True,
                confidence=float(choice_data.get("confidence", 0.90)),
                loop_probability=loop_prob,
                grounded_probability=grounded_prob,
                progress_probability=progress_prob,
                novelty_probability=novelty_prob,
                analytical_jev=round(analytical_jev, 4),
                reason_codes=reason_codes,
                metadata=metadata,
            )

        except Exception as e:
            elapsed_ms = (time.perf_counter() - start_t) * 1000.0
            logger.error(f"Fallo durante la evaluación en {type(self).__name__} ({self.backend}): {e}")

            metadata = {
                "provider": self.name,
                "backend": self.backend,
                "model_name": self.model_name,
                "latency_ms": round(elapsed_ms, 2),
                "error": str(e),
                "context_tokens": ctx.token_estimate,
                "truncated": ctx.truncated,
            }

            return ProviderAssessment(
                provider=self.name,
                model=self.model_name,
                available=False,
                confidence=0.0,
                failure_reason=f"{self.CODE_PREFIX} failure ({self.backend}): {str(e)}",
                reason_codes=[f"{self.CODE_PREFIX}_PROVIDER_UNAVAILABLE"],
                metadata=metadata,
            )

    def evaluate(
        self,
        state: Any,
        actions: List[ActionCandidate],
    ) -> List[ProviderAssessment]:
        """Evalúa un lote de acciones candidatas de forma secuencial preservando consistencia."""
        return [self.evaluate_action(state, action) for action in actions]
