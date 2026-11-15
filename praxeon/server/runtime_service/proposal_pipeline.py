"""Pipeline formal de una propuesta de acción como secuencia de pasos (auditoría A-01).

Proposal -> Evidence -> Risk -> Supervisor -> Policy -> Capability. Cada paso implementa el contrato común de
``praxeon.core.pipeline`` (``Step.run(ctx) -> Outcome``) y emite su evento al bus; ``PROPOSAL_STEPS`` fija el orden, que
es también el orden de los eventos que ve la interfaz. Ningún paso corta la pasada: todos devuelven CONTINUE, y la
respuesta se construye al final con ``ProposalContext.response()``.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, UTC
from typing import Any
import uuid

from praxeon.core.pipeline import Outcome, Step, run_steps
from praxeon.domain.decision import DecisionStatus, ExecutionMode, PolicyDecision, sign_receipt
from praxeon.domain.events import EventType
from praxeon.domain.models import ActionCandidate, ProviderAssessment, RiskLevel, ToolCall
from praxeon.policy.loop_detector import estimate_loop_probability
from praxeon.server.schemas.action import ProposeActionRequest
from praxeon.server.schemas.decision import DecisionResponse, PolicyDTO, ProviderEvaluationDTO, RiskDTO

logger = logging.getLogger("praxeon.server.dependencies")

FINISH_TOOLS = ("finish", "complete_task", "done")
SCRIPT_RUNNERS = ("python ", "python3 ", "node ", "bash ", "sh ")
SCRIPT_EXTENSIONS = (".py", ".js", ".sh", ".ts")
RISK_LEVEL_SCORES = {"LOW": 0.15, "MEDIUM": 0.45, "HIGH": 0.80, "CRITICAL": 0.95}
HARNESS_LOOP_THRESHOLD = 0.65
CAPABILITY_TTL = timedelta(minutes=5)
UNGROUNDED_SCORE = 0.05

# Tareas generativas, creativas o de especificación (historias, requisitos, diseño, nuevo proyecto, etc.)
_GENERATIVE_GOAL_HINTS = (
    "user stories", "historias de usuario", "requisito", "requirements", "diseño",
    "propuesta", "nuevo proyecto", "nueva app", "arquitectura", "especific", "cuento",
    "historia", "poema", "estructura de carpetas", "opin", "calific", "informe formal",
)
_INVESTIGATION_GOAL_HINTS = (
    "informe del proyecto", "informe", "report", "audita", "inspecciona", "analiza",
    "cómo funciona", "investiga", "describe el proyecto", "código",
)
_EVASION_PHRASES = ("pendiente de lectura", "sin analizar", "no se ha realizado", "no he podido revisar")


def _has_content(observation: Any) -> bool:
    return bool(observation and len(str(observation).strip()) > 20)


@dataclass
class ProposalContext:
    """La propuesta, el estado de la sesión y lo que cada paso va averiguando sobre ella."""

    runtime: Any
    session_id: str
    proposal: ProposeActionRequest
    state: Any = None
    action_id: str = ""
    decision_id: str = ""
    now: datetime = field(default_factory=lambda: datetime.now(UTC))

    # procedencia y acción
    provenance: dict[str, Any] = field(default_factory=dict)
    is_synthetic: bool = False
    model_source: str = ""
    action: ActionCandidate | None = None
    session_mode: str = "local_restricted"
    session_metadata: dict[str, Any] = field(default_factory=dict)   # metadata de la sesión al clasificar
    base_dir: str = ""

    # análisis
    op_assessment: Any = None
    target_path: str = ""
    is_missing_resource: bool = False
    missing_resource_name: str = ""
    is_premature_finish: bool = False
    eval_state: Any = None
    evidences: list[Any] = field(default_factory=list)
    grounding_score: float = 0.0
    risk_assessment: Any = None
    risk_dto: RiskDTO | None = None
    assessment: Any = None
    provider_dtos: list[ProviderEvaluationDTO] = field(default_factory=list)

    # decisión
    decision: Any = None
    receipt: Any = None
    status: str = ""
    policy_decision: str = ""
    requires_confirmation: bool = False
    preauthorized_reason: str | None = None
    policy_dto: PolicyDTO | None = None
    capability_data: dict[str, Any] | None = None
    expires_at: datetime | None = None

    @property
    def tool(self) -> str:
        return self.proposal.tool

    @property
    def arguments(self) -> dict[str, Any]:
        return self.proposal.arguments

    def emit(self, event_type: EventType, payload: dict[str, Any], node_id: str | None = None, **kw: Any) -> None:
        self.runtime.event_bus.emit(
            session_id=self.session_id, event_type=event_type, node_id=node_id or self.action_id, payload=payload, **kw
        )

    def response(self) -> DecisionResponse:
        return DecisionResponse(
            decision_id=self.decision_id,
            session_id=self.session_id,
            status=self.status,
            action_hash=self.receipt.action_hash,
            risk=self.risk_dto,
            providers=self.provider_dtos,
            policy=self.policy_dto,
            execution_mode=self.session_mode,
            capability=self.capability_data,
            expires_at=self.expires_at,
            operation_assessment=self.op_assessment.model_dump(),
        )


# ------------------------------------------------------------------------------------------------ 1. propuesta


class LoadSessionStep(Step[ProposalContext]):
    """Carga el estado de la sesión (la crea sobre la marcha si no existe) e identifica la acción y la decisión."""

    name = "load_session"

    def run(self, ctx: ProposalContext) -> Outcome:
        runtime, proposal = ctx.runtime, ctx.proposal
        ctx.state = runtime.state_store.load_state(ctx.session_id)
        if not ctx.state:
            runtime.create_session(goal=proposal.context.get("goal") or "Supervised Agent Task", session_id=ctx.session_id)
            ctx.state = runtime.state_store.load_state(ctx.session_id)
        ctx.action_id = proposal.action_id or f"act_{len(ctx.state.steps) + 1}"
        ctx.decision_id = f"d_{uuid.uuid4().hex[:6]}"
        ctx.now = datetime.now(UTC)
        return Outcome.CONTINUE


class BuildActionStep(Step[ProposalContext]):
    """Construye la acción candidata con su procedencia y resuelve el modo de ejecución de la sesión."""

    name = "build_action"

    def run(self, ctx: ProposalContext) -> Outcome:
        proposal, runtime = ctx.proposal, ctx.runtime
        ctx.provenance = proposal.provenance if isinstance(proposal.provenance, dict) else {}
        ctx.is_synthetic = bool(proposal.synthetic_fallback or ctx.provenance.get("synthetic_fallback", False))
        ctx.model_source = proposal.model_source or ctx.provenance.get("model_source") or ctx.provenance.get("source", "ExternalAgent")
        ctx.action = ActionCandidate(
            id=ctx.action_id,
            description=proposal.thought_rationale or f"{proposal.tool} {proposal.operation or ''}".strip(),
            tool_call=ToolCall(tool_name=proposal.tool, arguments=proposal.arguments),
            synthetic_fallback=ctx.is_synthetic,
            model_source=ctx.model_source,
        )

        session_mode = "local_restricted"
        with runtime._lock:
            if ctx.session_id in runtime._sessions_meta:
                session_mode = runtime._sessions_meta[ctx.session_id].get("execution_mode") or session_mode
        if not session_mode or session_mode == "local_restricted":
            if ctx.state and ctx.state.metadata:
                session_mode = ctx.state.metadata.get("execution_mode", session_mode)
        ctx.session_mode = session_mode
        return Outcome.CONTINUE


class EmitProposedStep(Step[ProposalContext]):
    """Evento action.proposed, con el parent_id respetado para bifurcaciones y retrocesos."""

    name = "emit_proposed"

    def run(self, ctx: ProposalContext) -> Outcome:
        proposal, state, prov = ctx.proposal, ctx.state, ctx.provenance
        parent_id = proposal.parent_id or (f"act_{len(state.steps)}" if state.steps else f"root_{ctx.session_id}")
        ctx.emit(
            EventType.ACTION_PROPOSED,
            {
                "action_id": ctx.action_id,
                "parent_id": parent_id,
                "tool": proposal.tool,
                "operation": proposal.operation or "",
                "arguments": proposal.arguments,
                "source": prov.get("source", "ExternalAgent"),
                "step": prov.get("step", 1),
                "workflow_node": prov.get("workflow"),
                "thought_rationale": proposal.thought_rationale,
                "synthetic_fallback": ctx.is_synthetic,
                "model_source": ctx.model_source,
            },
            parent_id=parent_id,
            decision_id=ctx.decision_id,
        )
        return Outcome.CONTINUE


class ClassifyOperationStep(Step[ProposalContext]):
    """Clasificación contextual de la operación concreta (Sección 4).

    SEGURIDAD (auditoría, Finding 13): el comando real de ``arguments`` prevalece sobre el campo ``operation``, que es
    una etiqueta decorativa del llamante y podría suplantarse para evadir la política.
    """

    name = "classify_operation"

    @staticmethod
    def operation_text(proposal: ProposeActionRequest) -> str:
        cmd = ""
        if proposal.arguments:
            cmd = str(proposal.arguments.get("command") or proposal.arguments.get("cmd") or proposal.arguments.get("raw") or "").strip()
        if proposal.tool in ("run_command", "run_script") and cmd:
            return cmd
        if cmd:
            return f"{proposal.tool} {cmd}".strip() if proposal.tool else cmd
        op = (proposal.operation or "").strip()
        if not op:
            return proposal.tool or ""
        if proposal.tool and proposal.tool != "run_command" and not op.startswith(proposal.tool):
            return f"{proposal.tool} {op}".strip()
        return op

    def run(self, ctx: ProposalContext) -> Outcome:
        runtime, proposal = ctx.runtime, ctx.proposal
        with runtime._lock:
            ctx.session_metadata = (runtime._sessions_meta.get(ctx.session_id) or {}).get("metadata") or {}
        # Las rutas relativas se resuelven contra el workspace de la sesión, no contra el cwd del servidor
        ctx.base_dir = ctx.session_metadata.get("workspace_root") or ctx.session_metadata.get("working_directory") or os.getcwd()
        assessment = runtime.command_classifier.classify(
            self.operation_text(proposal),
            tool=proposal.tool,
            arguments=proposal.arguments,
            # workspace_root: el clasificador comprueba si una redirección `>` sobrescribiría un fichero existente
            context={"goal": ctx.state.goal.objective, "session_id": ctx.session_id, "workspace_root": ctx.base_dir},
        )
        ctx.op_assessment = runtime._adopt_external_tool_profile(proposal.tool, assessment)
        ctx.emit(EventType.OPERATION_CLASSIFIED, ctx.op_assessment.model_dump(), decision_id=ctx.decision_id)
        return Outcome.CONTINUE


# ------------------------------------------------------------------------------------------------ 2. evidencia


class MissingResourceStep(Step[ProposalContext]):
    """Detecta la insistencia en un archivo o script que no existe.

    Pedir por primera vez un recurso inexistente es inofensivo y el error de la herramienta («no existe…») orienta al
    agente mejor que un veto (en el benchmark, sin veto rectificaba; con veto repetía la ruta hasta el corte). Solo se
    marca la insistencia: la misma herramienta con la misma ruta ya intentada antes.
    """

    name = "missing_resource"

    def run(self, ctx: ProposalContext) -> Outcome:
        args, tool = ctx.arguments, ctx.tool
        ctx.target_path = str(args.get("path") or args.get("file") or "").strip()
        missing = ""
        if tool in ("read_file", "view_file") and ctx.target_path:
            norm_target = os.path.expanduser(os.path.expandvars(ctx.target_path))
            full_target = norm_target if os.path.isabs(norm_target) else os.path.join(ctx.base_dir, norm_target)
            if not os.path.exists(full_target):
                missing = ctx.target_path
        elif tool in ("run_command", "run_script"):
            missing = self._missing_script(str(args.get("command") or args.get("cmd") or "").strip(), ctx.base_dir)
        if missing:
            ctx.missing_resource_name = missing
            ctx.is_missing_resource = ctx.state is None or self._already_tried(ctx)
        return Outcome.CONTINUE

    @staticmethod
    def _missing_script(command: str, base_dir: str) -> str:
        for runner in SCRIPT_RUNNERS:
            if command.startswith(runner):
                script = command[len(runner):].strip().split()[0].strip('"\'')
                if script.endswith(SCRIPT_EXTENSIONS):
                    full_script = script if os.path.isabs(script) else os.path.join(base_dir, script)
                    if not os.path.exists(full_script):
                        return script
                break
        return ""

    @staticmethod
    def _already_tried(ctx: ProposalContext) -> bool:
        def target_of(step: Any) -> str:
            call = getattr(getattr(step, "action", None), "tool_call", None)
            a = getattr(call, "arguments", None) or {}
            return str(a.get("path") or a.get("file") or a.get("command") or a.get("cmd") or "").strip()

        wanted = ctx.target_path or str(ctx.arguments.get("command") or ctx.arguments.get("cmd") or "").strip()
        return any(
            getattr(getattr(getattr(s, "action", None), "tool_call", None), "tool_name", None) == ctx.tool
            and target_of(s) == wanted
            for s in getattr(ctx.state, "steps", [])
        )


class PrematureFinishStep(Step[ProposalContext]):
    """Detecta una conclusión prematura o evasiva (finish con texto evasivo, vacío o sin evidencia que lo respalde)."""

    name = "premature_finish"

    def run(self, ctx: ProposalContext) -> Outcome:
        state = ctx.state
        if ctx.tool not in FINISH_TOOLS or not state:
            return Outcome.CONTINUE
        goal_lower = state.goal.objective.lower()
        summary = str(ctx.arguments.get("summary") or ctx.arguments.get("final_answer") or "").strip()
        is_generative_or_spec = any(w in goal_lower for w in _GENERATIVE_GOAL_HINTS)
        # Es investigación de repositorio si no es una tarea generativa y pide informe o análisis del proyecto o del código
        is_codebase_investigation = not is_generative_or_spec and any(w in goal_lower for w in _INVESTIGATION_GOAL_HINTS)
        explicit_evasion = any(m in summary.lower() for m in _EVASION_PHRASES)
        if ctx.session_metadata.get("workflow_id"):
            # Nodo de un workflow: un veredicto breve (p. ej. {"approved": true}) es un resultado legítimo del nodo
            is_evasive = explicit_evasion or not summary
            is_codebase_investigation = False
        else:
            # Una respuesta corta ("8123") es legítima si el agente ya reunió observaciones; solo es evasiva sin evidencia
            has_observations = any(_has_content(s.observation) for s in state.steps)
            is_evasive = explicit_evasion or not summary or (len(summary) < 30 and not has_observations)
        observed = [s for s in state.steps if _has_content(s.observation)]
        if is_evasive or (is_codebase_investigation and not observed and len(summary) < 250):
            ctx.is_premature_finish = True
        return Outcome.CONTINUE


class AssessEvidenceStep(Step[ProposalContext]):
    """Evidencia empírica de la acción y su grado de fundamentación.

    En una misión con workflow cada nodo se supervisa con su propia trayectoria: repetir acciones parecidas entre nodos
    (p. ej. dos iteraciones de un bucle desarrollador/revisor) no es un bucle del agente.
    """

    name = "assess_evidence"

    def run(self, ctx: ProposalContext) -> Outcome:
        runtime = ctx.runtime
        ctx.eval_state = runtime._node_scoped_state(ctx.state, ctx.session_metadata, ctx.action_id)
        ctx.evidences = runtime.evidence_engine.assess(ctx.eval_state, ctx.action)
        if ctx.is_missing_resource or ctx.is_premature_finish:
            ctx.grounding_score = UNGROUNDED_SCORE
        else:
            ctx.grounding_score = 0.85 if ctx.evidences else 0.40
        ctx.emit(
            EventType.EVIDENCE_EVALUATED,
            {
                "evidence_count": len(ctx.evidences),
                "grounding_score": ctx.grounding_score,
                "claims_evaluated": [e.claim.statement for e in ctx.evidences],
                "resource_missing": ctx.is_missing_resource,
                "missing_resource": ctx.missing_resource_name if ctx.is_missing_resource else None,
                "premature_finish": ctx.is_premature_finish,
            },
            decision_id=ctx.decision_id,
        )
        return Outcome.CONTINUE


# ------------------------------------------------------------------------------------------------ 3. riesgo y supervisor


class AssessRiskStep(Step[ProposalContext]):
    """Riesgo operacional, sensible al modo de ejecución y reconciliado con la clasificación de la operación."""

    name = "assess_risk"

    def run(self, ctx: ProposalContext) -> Outcome:
        runtime, state = ctx.runtime, ctx.state
        raw_risk = runtime.risk_engine.assess_action_risk(ctx.action, execution_mode=ctx.session_mode)
        ctx.risk_assessment = risk = runtime.policy_engine.reconciler.reconcile(
            base_risk=raw_risk,
            operation_assessment=ctx.op_assessment,
            tool_name=ctx.tool,
            tool_spec=runtime.registry.get_tool(ctx.tool),
            action=ctx.action,
            available_evidence=state.evidence,
            execution_mode=ctx.session_mode,
            forbidden_tools=state.forbidden_tools,
        )
        level = (risk.level.value if hasattr(risk.level, "value") else str(risk.level)).upper()
        ctx.risk_dto = RiskDTO(level=level, score=RISK_LEVEL_SCORES.get(level, 0.20), reasons=risk.reasons)
        ctx.emit(EventType.RISK_ASSESSED, ctx.risk_dto.model_dump(), decision_id=ctx.decision_id)
        return Outcome.CONTINUE


class EvaluateSupervisorStep(Step[ProposalContext]):
    """Juicio del supervisor semántico de la sesión (LAYA, TypeSafe, Mock...), completado con las señales del arnés.

    El bucle lo calcula también el arnés con reglas, igual para cualquier supervisor; cuenta el mayor de los dos. Un
    recurso inexistente o un finish prematuro bajan la fundamentación a 0.05 con su motivo.
    """

    name = "evaluate_supervisor"

    def run(self, ctx: ProposalContext) -> Outcome:
        runtime = ctx.runtime
        provider = runtime.get_session_provider(ctx.session_id)
        assessments = provider.evaluate(ctx.eval_state, [ctx.action])
        assessment = assessments[0] if assessments else ProviderAssessment(
            provider=getattr(provider, "name", runtime.config.provider.name),
            available=False,
            confidence=0.0,
            loop_probability=None,
            grounded_probability=None,
            progress_probability=None,
            reason_codes=["PROVIDER_NO_EVALUATION"],
        )
        assessment = self._with_harness_loop(ctx, assessment)
        ctx.assessment = assessment = self._with_ungrounded_signals(ctx, assessment)

        available = bool(getattr(assessment, "available", True))
        loop_prob = getattr(assessment, "loop_probability", None)
        if not available:
            verdict = "UNAVAILABLE"
        elif loop_prob is not None and loop_prob >= 0.5:
            verdict = "REVIEW"
        else:
            verdict = "ALLOW"
        dto = ProviderEvaluationDTO(name=self.display_name(provider, runtime), score=self._score(assessment, available),
                                    verdict=verdict, available=available)
        ctx.provider_dtos = [dto]
        ctx.emit(
            EventType.PROVIDER_EVALUATED,
            {"provider_name": dto.name, "score": dto.score, "verdict": dto.verdict, "available": dto.available},
            decision_id=ctx.decision_id,
        )
        return Outcome.CONTINUE

    @staticmethod
    def _with_harness_loop(ctx: ProposalContext, assessment: Any) -> Any:
        if not ctx.tool or ctx.tool in FINISH_TOOLS:
            return assessment
        history = []
        for step in getattr(ctx.eval_state, "steps", []) or []:
            call = getattr(getattr(step, "action", None), "tool_call", None)
            history.append({
                "tool": getattr(call, "tool_name", None), "arguments": getattr(call, "arguments", None) or {},
                "observation": getattr(step, "observation", "") or "",
            })
        harness_loop = estimate_loop_probability(history, ctx.tool, ctx.arguments or {})
        if harness_loop <= (assessment.loop_probability or 0.0):
            return assessment
        codes = list(assessment.reason_codes or [])
        if harness_loop >= HARNESS_LOOP_THRESHOLD and "HARNESS_LOOP_DETECTED" not in codes:
            codes.append("HARNESS_LOOP_DETECTED")
        return assessment.model_copy(update={"loop_probability": harness_loop, "reason_codes": codes})

    @staticmethod
    def _with_ungrounded_signals(ctx: ProposalContext, assessment: Any) -> Any:
        if not (ctx.is_missing_resource or ctx.is_premature_finish):
            return assessment
        codes = list(assessment.reason_codes or [])
        missing_code = f"UNGROUNDED_FILE_NOT_FOUND ({ctx.missing_resource_name})"
        if ctx.is_missing_resource and missing_code not in codes:
            codes.append(missing_code)
        if ctx.is_premature_finish and "PREMATURE_COMPLETION_WITHOUT_EVIDENCE" not in codes:
            codes.append("PREMATURE_COMPLETION_WITHOUT_EVIDENCE")
        return assessment.model_copy(update={
            "grounded_probability": min(assessment.grounded_probability or 1.0, UNGROUNDED_SCORE),
            "reason_codes": codes,
        })

    @staticmethod
    def _score(assessment: Any, available: bool) -> float | None:
        if not available:
            return None
        raw = getattr(assessment, "progress_probability", None)
        if raw is None:
            raw = getattr(assessment, "confidence", None)
        return round(float(raw), 2) if (raw is not None and isinstance(raw, (int, float))) else None

    @staticmethod
    def display_name(provider: Any, runtime: Any) -> str:
        raw = getattr(provider, "name", None) or getattr(provider, "provider_name", None) or provider.__class__.__name__
        name = str(raw).lower().replace("provider", "").replace("adapter", "").strip()
        if name in ("mock", "replay"):
            return name
        if "laya" in name:
            return "LAYA"
        if "typesafe" in name:
            return "TypeSafe"
        return "LAYA" if runtime.config.provider.name.lower() == "laya" else "TypeSafe"


# ------------------------------------------------------------------------------------------------ 4. política y capability


class DecidePolicyStep(Step[ProposalContext]):
    """Decisión operacional del PolicyEngine (cadena de reglas) con su recibo firmado."""

    name = "decide_policy"

    def run(self, ctx: ProposalContext) -> Outcome:
        state = ctx.state
        ctx.decision, ctx.receipt = ctx.runtime.policy_engine.evaluate_action(
            action=ctx.action,
            state=state.to_snapshot(),
            provider_assessment=ctx.assessment,
            available_evidence=state.evidence,
            forbidden_tools=state.forbidden_tools,
            risk_assessment=ctx.risk_assessment,
            operation_assessment=ctx.op_assessment,
            session_id=ctx.session_id,
            execution_mode=ExecutionMode(ctx.session_mode),
            decision_id=ctx.decision_id,
        )
        return Outcome.CONTINUE


class ResolveStatusStep(Step[ProposalContext]):
    """Estado final con precedencia determinista BLOCK > REPLAN > REVIEW > ALLOW.

    BLOCK (o riesgo CRÍTICO) nunca ejecuta, ni en Full Access ni en modo autónomo. Lo que pide confirmación se aprueba
    solo en Full Access autorizado por el operador, sin confirmación por paso y con una herramienta que lo admita.
    """

    name = "resolve_status"

    def run(self, ctx: ProposalContext) -> Outcome:
        runtime, decision = ctx.runtime, ctx.decision
        session_meta: dict[str, Any] = {}
        with runtime._lock:
            if ctx.session_id in runtime._sessions_meta:
                session_meta = runtime._sessions_meta[ctx.session_id].get("metadata") or {}
        if not session_meta and ctx.state and ctx.state.metadata:
            session_meta = ctx.state.metadata

        ctx.requires_confirmation = decision.requires_confirmation
        if decision.status == DecisionStatus.BLOCK or decision.risk.level == RiskLevel.CRITICAL or ctx.risk_assessment.level == RiskLevel.CRITICAL:
            ctx.status = ctx.policy_decision = "BLOCK"
            ctx.requires_confirmation = False
        elif decision.status == DecisionStatus.REPLAN:
            ctx.status = ctx.policy_decision = "REPLAN"
            ctx.requires_confirmation = False
        elif ctx.requires_confirmation:
            self._confirmation(ctx, session_meta)
        else:
            ctx.status = ctx.policy_decision = decision.status.value.upper()

        ctx.policy_dto = PolicyDTO(
            decision=ctx.policy_decision,
            reason_codes=list(decision.reason_codes) + ([ctx.preauthorized_reason] if ctx.preauthorized_reason else []),
            requires_confirmation=ctx.requires_confirmation,
        )
        ctx.emit(
            EventType.POLICY_DECIDED,
            {
                "status": ctx.status,
                "reason_code": decision.reason_codes[0] if decision.reason_codes else "POLICY_EVALUATED",
                "requires_confirmation": ctx.requires_confirmation,
                "execution_mode": ctx.session_mode,
            },
            node_id=f"{ctx.action_id}_policy",
            parent_id=ctx.action_id,
            decision_id=ctx.decision_id,
        )
        return Outcome.CONTINUE

    @staticmethod
    def _confirmation(ctx: ProposalContext, session_meta: dict[str, Any]) -> None:
        mode = ctx.session_mode
        mode_val = str(mode.value if hasattr(mode, "value") else mode or "").lower()
        full_access_authorized = bool(session_meta.get("full_access_authorized_by_operator") or session_meta.get("operator_authorized"))
        step_confirmation = bool(session_meta.get("require_step_confirmation"))
        tool = ctx.action.tool_call.tool_name if ctx.action.tool_call else None
        if mode_val == "full_access" and full_access_authorized and not step_confirmation and ctx.runtime._may_auto_confirm(tool):
            # FULL_ACCESS autorizado por el operador (doble confirmación al activarlo): no se pide confirmación por paso.
            # BLOCK, REPLAN y riesgo CRÍTICO se resolvieron antes y siguen siendo innegociables.
            ctx.requires_confirmation = False
            ctx.status = ctx.policy_decision = "ALLOW"
            ctx.preauthorized_reason = "FULL_ACCESS_OPERATOR_PREAUTHORIZED"
            logger.warning(
                "[SECURITY AUDIT] Sesión '%s' en FULL_ACCESS autorizado por el operador: confirmación por paso "
                "omitida para la acción '%s' (motivos de revisión: %s).",
                ctx.session_id,
                ctx.action.tool_call.tool_name if ctx.action.tool_call else "cognitive",
                ", ".join(ctx.decision.reason_codes),
            )
        else:
            # Otros modos, Full Access sin autorización o con confirmación por paso: confirmación interactiva
            ctx.status = "REVIEW"
            ctx.policy_decision = "REQUIRE_HUMAN_CONFIRMATION"


class IssueCapabilityStep(Step[ProposalContext]):
    """ALLOW firma el recibo y prepara la capability (5 min). Los eventos los emite PublishDecisionStep, tras guardarla."""

    name = "issue_capability"

    def run(self, ctx: ProposalContext) -> Outcome:
        if ctx.status == "ALLOW":
            ctx.expires_at = ctx.now + CAPABILITY_TTL
            receipt = ctx.receipt.model_copy(update={
                "decision_id": ctx.decision_id,
                "decision_status": DecisionStatus.ALLOW,
                "expires_at": ctx.expires_at,
                "execution_mode": ExecutionMode(ctx.session_mode),
            })
            signed = sign_receipt(receipt, ctx.runtime.policy_engine.secret_key)
            cap = signed.to_capability_payload(allowed_tools=[ctx.tool])
            ctx.capability_data = cap.model_dump(mode="json") if cap else None
            ctx.receipt = signed
        return Outcome.CONTINUE


class RecordDecisionStep(Step[ProposalContext]):
    """Guarda la decisión en el registro durable (SQLite WAL + memoria) y añade al estado las decisiones terminales.

    Las ALLOW se añaden al estado en execute_decision, tras verificar el hash criptográfico del estado.
    """

    name = "record_decision"

    def run(self, ctx: ProposalContext) -> Outcome:
        runtime, op = ctx.runtime, ctx.op_assessment
        record = {
            "decision_id": ctx.decision_id,
            "session_id": ctx.session_id,
            "action_id": ctx.action_id,
            "action": ctx.action,
            "status": ctx.status,
            "execution_mode": ctx.session_mode,
            "action_hash": ctx.receipt.action_hash,
            "risk": ctx.risk_dto,
            "providers": ctx.provider_dtos,
            "policy": ctx.policy_dto,
            "capability": ctx.capability_data,
            "expires_at": ctx.expires_at,
            "receipt": ctx.receipt,
            "evidence": ctx.evidences,
            "grounding_score": ctx.grounding_score,
            "operation_assessment": op.model_dump(),
            "operation_category": op.category.value if hasattr(op.category, "value") else str(op.category),
            "created_at": ctx.now,
        }
        runtime.decision_repository.save(record)
        with runtime._lock:
            runtime._decisions[ctx.decision_id] = record

        if ctx.status in ("REPLAN", "BLOCK"):
            ctx.state.add_step(
                action=ctx.action,
                decision=PolicyDecision(
                    status=DecisionStatus.BLOCK if ctx.status == "BLOCK" else DecisionStatus.REPLAN,
                    reason_codes=ctx.decision.reason_codes,
                ),
                observation=None,
            )
            runtime.state_store.save_state(ctx.state)
        return Outcome.CONTINUE


class PublishDecisionStep(Step[ProposalContext]):
    """Anuncia la decisión ya guardada: ALLOW emite la capability, REVIEW pide aprobación y BLOCK poda la rama.

    Va después de RecordDecisionStep: quien reacciona al evento (el operador de la terminal, un cliente de la API) puede
    confirmar la decisión en ese mismo instante, y tiene que encontrarla (R-080).
    """

    name = "publish_decision"

    def run(self, ctx: ProposalContext) -> Outcome:
        if ctx.status == "ALLOW":
            ctx.emit(EventType.CAPABILITY_ISSUED, ctx.capability_data or {}, decision_id=ctx.decision_id)
        elif ctx.status == "REVIEW":
            ctx.emit(
                EventType.APPROVAL_REQUESTED,
                {
                    "risk_level": ctx.risk_dto.level,
                    "reasons": ctx.risk_dto.reasons,
                    "reason_code": ctx.policy_dto.reason_codes,
                    "execution_mode": ctx.session_mode,
                },
                decision_id=ctx.decision_id,
            )
        elif ctx.status == "BLOCK":
            ctx.emit(EventType.DECISION_PRUNED, {"reason": ", ".join(ctx.decision.reason_codes)}, decision_id=ctx.decision_id)
        return Outcome.CONTINUE


# Orden explícito: es también el orden de los eventos (proposed, classified, evidence, risk, provider, policy, capability).
# La decisión se guarda antes de anunciarla: record_decision va antes de publish_decision.
PROPOSAL_STEPS: tuple[Step[ProposalContext], ...] = (
    LoadSessionStep(),
    BuildActionStep(),
    EmitProposedStep(),
    ClassifyOperationStep(),
    MissingResourceStep(),
    PrematureFinishStep(),
    AssessEvidenceStep(),
    AssessRiskStep(),
    EvaluateSupervisorStep(),
    DecidePolicyStep(),
    ResolveStatusStep(),
    IssueCapabilityStep(),
    RecordDecisionStep(),
    PublishDecisionStep(),
)


def run_proposal(runtime: Any, session_id: str, proposal: ProposeActionRequest) -> DecisionResponse:
    """Pasa una propuesta por todo el pipeline formal y devuelve la decisión."""
    ctx = ProposalContext(runtime=runtime, session_id=session_id, proposal=proposal)
    run_steps(PROPOSAL_STEPS, ctx)
    return ctx.response()
