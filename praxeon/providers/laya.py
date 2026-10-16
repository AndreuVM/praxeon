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
from praxeon.providers.context import ProviderContext, ProviderContextBuilder
from praxeon.providers.systemone import QUESTIONS, parse_answers

logger = logging.getLogger("praxeon.providers.laya")

def _laya_sdk_installed() -> bool:
    """Comprueba si el paquete `laya` está instalado sin importarlo (arrastra torch y tarda segundos)."""
    import importlib.util

    return importlib.util.find_spec("laya") is not None


# Checkpoint de LAYA: «typed-decisions» (entrenado para decisiones tipadas) o «auto» (el Router elige por idioma)
LAYA_CHECKPOINT = os.getenv("LAYA_CHECKPOINT", "typed-decisions").strip() or "typed-decisions"
NEURAL_LOOP_CAP = 0.60  # por debajo del umbral de bloqueo por bucle (0.65): el modelo solo no puede bloquear

_ROUTERS: Dict[Optional[str], Any] = {}
_ROUTER_LOCK = threading.Lock()


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

    def __init__(
        self,
        backend: str = "auto",
        model_name: str = "laya-v1-calibrated",
        endpoint_url: Optional[str] = None,
        auth_token: Optional[str] = None,
        context_builder: Optional[ProviderContextBuilder] = None,
        max_context_tokens: int = 2048,
        timeout: float = 5.0,
        device: str = "cpu",
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

    def _get_router(self):
        """Carga (una vez por proceso y dispositivo) el Router del paquete `laya`, que elige el checkpoint por idioma."""
        with _ROUTER_LOCK:
            router = _ROUTERS.get(self.device)
            if router is None:
                if os.name == "nt":
                    # Sin modo desarrollador Windows no permite enlaces simbólicos y la descarga de pesos falla
                    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
                from laya import Router  # type: ignore

                kwargs = {"device": self.device} if self.device and self.device != "auto" else {}
                router = _ROUTERS[self.device] = Router(**kwargs)
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
        primitives = parse_answers(router.predict(ctx.formatted_prompt, QUESTIONS, **kwargs))
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

        # 1. Comprobación de repetición o bucle frente a pasos previos.
        # Un bucle es repetir la MISMA acción sobre el MISMO objetivo (mismo archivo, mismo comando), no usar
        # varias veces una herramienta: crear cinco archivos distintos con edit_file es progreso, no un ciclo.
        write_tools = ("edit_file", "write_file")

        def _target(a: Dict[str, Any]) -> str:
            return str(
                a.get("path") or a.get("file") or a.get("command") or a.get("cmd") or a.get("raw") or a.get("query") or ""
            ).strip().lower()

        def _content(a: Dict[str, Any]) -> str:
            return str(a.get("content") or a.get("diff") or "")

        cmd_str = str(args.get("command") or args.get("cmd") or args.get("raw") or desc).strip().lower()
        target_path = str(args.get("path") or args.get("file") or "").strip().lower()
        my_target = _target(args)

        history = [h for h in ctx.history_window if isinstance(h, dict)]
        same_target = [
            h for h in history
            if (h.get("tool") or "") == tool_name and _target(h.get("arguments") or {}) == my_target
        ]
        if tool_name in write_tools:
            # Reescribir un archivo con contenido distinto es refinar; solo el contenido idéntico es repetición
            same_target = [h for h in same_target if _content(h.get("arguments") or {}) == _content(args)]
        repetition_count = len(same_target) if (tool_name and my_target) else 0

        is_loop = 0.05
        if repetition_count >= 2:
            is_loop = 0.88
        elif repetition_count >= 1:
            is_loop = 0.65 if tool_name in ("run_command", "run_script") else 0.88

        # Detección de repetición del mismo comando exacto
        recent_commands = [
            str(h.get("arguments", {}).get("command") or h.get("operation") or "").strip().lower()
            for h in history
        ]
        if cmd_str and tool_name not in write_tools and recent_commands.count(cmd_str) >= 2:
            is_loop = max(is_loop, 0.85)

        # Detección de estancamiento tras fallos consecutivos
        # Los mensajes de fallo de las herramientas son cortos o empiezan por «error»; el contenido de un archivo largo
        # puede contener «error» o «failed» (código, tests, logs) sin que la lectura haya fallado.
        def _is_failure(obs: str) -> bool:
            obs = obs.strip().lower()
            if not any(err in obs for err in ("no existe", "error", "failed", "fail", "not found")):
                return False
            return len(obs) <= 300 or obs.startswith(("error", "fallo", "traceback"))

        recent_obs = [str(h.get("observation", "")) for h in ctx.history_window[-3:] if isinstance(h, dict)]
        recent_failures = [obs for obs in recent_obs if _is_failure(obs)]
        if len(recent_failures) >= 2:
            is_loop = max(is_loop, 0.85)

        if any(w in desc for w in ("reintentar", "mismo comando", "bucle", "repetir")):
            is_loop = max(is_loop, 0.85)

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
