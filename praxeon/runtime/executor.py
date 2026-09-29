"""Ejecutor físico con enforcement formal (SecureExecutor) para v0.2.1.

Aplica los principios fundamentales:
1. Prompt instruction != Execution enforcement.
2. Ninguna herramienta puede ejecutarse físicamente sin presentar un capability / DecisionReceipt
   válido, firmado/emitido por PolicyEngine, no manipulado y no consumido (Replay Prevention).
3. Toda ejecución física de herramientas se aísla mediante SandboxAdapter, protegiendo al host
   contra inyección de subshells, acceso no autorizado a archivos fuera del workspace y fugas de secretos.
"""

import os
import time
from typing import Any, Callable, Dict, Optional, Set
from pydantic import BaseModel, ConfigDict
from praxeon.domain.action import compute_action_hash
from praxeon.domain.decision import (
    compute_state_hash,
    DecisionReceipt,
    DecisionStatus,
    ExecutionMode,
    PolicyDecision,
    verify_receipt_signature,
)
from praxeon.domain.interfaces import Executor
from praxeon.domain.models import ActionCandidate
from praxeon.policy.risk import ToolRegistry
from praxeon.policy.sanitizer import DataSanitizer
from praxeon.runtime.full_access import FullAccessExecutor
from praxeon.runtime.nonce_store import InMemoryNonceStore, NonceStore
from praxeon.runtime.sandbox import (
    ContainerSandboxAdapter,
    ContainerSandboxConfig,
    DryRunSandbox,
    LocalProcessSandbox,
    SandboxAdapter,
    get_default_workspace_root,
)
from praxeon.runtime.state import SessionState


class PolicyViolation(Exception):
    """Excepción lanzada cuando una herramienta intenta ejecutarse en contra de la política o sin autorización."""

    def __init__(
        self,
        message: str,
        action: Optional[ActionCandidate] = None,
        decision: Optional[PolicyDecision] = None,
        receipt: Optional[DecisionReceipt] = None,
    ):
        super().__init__(message)
        self.action = action
        self.decision = decision
        self.receipt = receipt


class ToolObservation(BaseModel):
    """Resultado estructurado de la ejecución física de una herramienta."""
    model_config = ConfigDict(frozen=True)

    output: str
    success: bool = True
    execution_time_ms: float = 0.0
    tool_name: Optional[str] = None
    is_error: bool = False


class SecureExecutor(Executor):
    """Ejecutor físico con validación estricta de capacidades/recibos y aislamiento en sandbox."""

    def __init__(
        self,
        registry: Optional[ToolRegistry] = None,
        sandbox: Optional[SandboxAdapter] = None,
        dry_run: bool = False,
        sanitizer: Optional[DataSanitizer] = None,
        strict_capability: bool = True,
        secret_key: Optional[str] = None,
        nonce_store: Optional[NonceStore] = None,
        full_access_executor: Optional[FullAccessExecutor] = None,
        allow_full_access: bool = True,
    ):
        self.registry = registry or ToolRegistry(register_defaults=True)
        self.dry_run = dry_run
        if dry_run:
            self.sandbox = sandbox if sandbox is not None else DryRunSandbox()
        else:
            self.sandbox = sandbox if sandbox is not None else LocalProcessSandbox()
        self.sanitizer = sanitizer if sanitizer is not None else DataSanitizer()
        self.strict_capability = strict_capability
        self.secret_key = secret_key
        self.nonce_store = nonce_store if nonce_store is not None else InMemoryNonceStore()
        self.full_access_executor = full_access_executor or FullAccessExecutor()
        self.allow_full_access = allow_full_access
        self.container_sandbox: Optional[ContainerSandboxAdapter] = None
        self._consumed_receipts: Set[str] = set()
        self._custom_handlers: Dict[str, Callable[[Dict[str, Any]], str]] = {}

    def register_handler(self, tool_name: str, handler: Callable[[Dict[str, Any]], str]) -> None:
        """Permite inyectar controladores personalizados o mocks para herramientas."""
        self._custom_handlers[tool_name] = handler

    def execute(
        self,
        action: ActionCandidate,
        state: SessionState,
        receipt: Optional[DecisionReceipt] = None,
        decision: Optional[PolicyDecision] = None,
    ) -> ToolObservation:
        """Valida rigurosamente la autorización mediante capability/recibo y ejecuta en sandbox."""
        tool_name = action.tool_call.tool_name if action.tool_call else None
        tool_args = action.tool_call.arguments if action.tool_call else {}

        # 1. BARRERA DE ENFORCEMENT: Exigir Capability / DecisionReceipt válido
        if receipt is None:
            if decision is not None and not self.strict_capability:
                # Modo de compatibilidad relajado explícito
                if decision.status != DecisionStatus.ALLOW:
                    raise PolicyViolation(
                        f"Ejecución física DENEGADA para '{tool_name}': el estatus de la política es {decision.status} "
                        f"(Motivos: {', '.join(decision.reason_codes)})",
                        action=action,
                        decision=decision,
                    )
            else:
                raise PolicyViolation(
                    f"Ejecución física DENEGADA para '{tool_name}': Se requiere un capability/DecisionReceipt "
                    "válido y no reutilizado emitido por PolicyEngine. La ejecución directa sin autorización formal está terminantemente prohibida.",
                    action=action,
                    decision=decision,
                )
        else:
            # Validación rigurosa del capability ligado
            if receipt.decision_status != DecisionStatus.ALLOW:
                raise PolicyViolation(
                    f"Ejecución física DENEGADA para '{tool_name}': el recibo presenta estatus no autorizado: '{receipt.decision_status}' "
                    f"(Motivos: {', '.join(receipt.reason_codes)})",
                    action=action,
                    decision=decision,
                    receipt=receipt,
                )

            # Verificar hash de acción (evita manipulación o cambio de tool/args)
            expected_action_hash = compute_action_hash(action)
            if receipt.action_hash != expected_action_hash:
                raise PolicyViolation(
                    f"Ejecución física DENEGADA para '{tool_name}': El hash de la acción ({expected_action_hash[:12]}...) "
                    f"no coincide con el capability ({receipt.action_hash[:12]}...). Acción manipulada o desvinculada.",
                    action=action,
                    decision=decision,
                    receipt=receipt,
                )

            # Verificar hash de estado (evita replay en estados desfasados)
            snapshot_dict = state.to_snapshot()
            snapshot_hash = compute_state_hash(snapshot_dict)
            canonical_hash = state.compute_hash()

            valid_hashes = {snapshot_hash, canonical_hash}
            if "checkpoint_ids" in snapshot_dict:
                # Comprobar estado con los checkpoints anteriores al auto-checkpoint actual
                if len(state.checkpoint_ids) > 0:
                    snap_prev_chk = dict(snapshot_dict)
                    snap_prev_chk["checkpoint_ids"] = list(state.checkpoint_ids[:-1])
                    valid_hashes.add(compute_state_hash(snap_prev_chk))

            if receipt.state_hash not in valid_hashes:
                raise PolicyViolation(
                    f"Ejecución física DENEGADA para '{tool_name}': El hash de estado ({receipt.state_hash[:12]}...) "
                    f"está desfasado frente al estado actual ({snapshot_hash[:12]}...).",
                    action=action,
                    decision=decision,
                    receipt=receipt,
                )

            # Verificar ligadura de sesión
            if receipt.session_id != state.session_id:
                raise PolicyViolation(
                    f"Ejecución física DENEGADA para '{tool_name}': El ID de sesión del capability ({receipt.session_id}) "
                    f"no coincide con la sesión activa ({state.session_id}).",
                    action=action,
                    decision=decision,
                    receipt=receipt,
                )

            # Verificar ventana de validez temporal (Expiración)
            if receipt.is_expired():
                raise PolicyViolation(
                    f"Ejecución física DENEGADA para '{tool_name}': El capability ha expirado "
                    f"(expiró en: {receipt.expires_at.isoformat() if receipt.expires_at else 'N/A'}).",
                    action=action,
                    decision=decision,
                    receipt=receipt,
                )

            # Verificar autenticidad mediante firma HMAC si se configuró clave secreta
            if self.secret_key:
                if not receipt.signature:
                    raise PolicyViolation(
                        f"Ejecución física DENEGADA para '{tool_name}': El capability carece de firma HMAC auténtica del emisor.",
                        action=action,
                        decision=decision,
                        receipt=receipt,
                    )
                if not verify_receipt_signature(self.secret_key, receipt):
                    raise PolicyViolation(
                        f"Ejecución física DENEGADA para '{tool_name}': La firma HMAC del capability es inválida o ha sido manipulada.",
                        action=action,
                        decision=decision,
                        receipt=receipt,
                    )

            # Verificar no-reutilización (Replay attack prevention mediante NonceStore atómico)
            if receipt.decision_id in self._consumed_receipts or not self.nonce_store.consume(
                receipt.decision_id, receipt.nonce, expires_at=receipt.expires_at
            ):
                raise PolicyViolation(
                    f"Ejecución física DENEGADA para '{tool_name}': El capability '{receipt.decision_id}' "
                    f"con nonce '{receipt.nonce}' ya ha sido consumido previamente (Replay attack prevention).",
                    action=action,
                    decision=decision,
                    receipt=receipt,
                )

            # Verificar modo de ejecución y ligadura contextual (PRAXEON 1.0 Sección 7 y 8)
            receipt_mode = getattr(receipt, "execution_mode", ExecutionMode.LOCAL_RESTRICTED.value)
            session_mode = state.metadata.get("execution_mode", ExecutionMode.LOCAL_RESTRICTED.value)
            if receipt_mode != session_mode:
                raise PolicyViolation(
                    f"Ejecución física DENEGADA para '{tool_name}': Mismatch de ExecutionMode. "
                    f"El capability fue firmado para '{receipt_mode}' pero la sesión requiere '{session_mode}'. "
                    "Cualquier intento de elevación o alteración de entorno anula la autorización.",
                    action=action,
                    decision=decision,
                    receipt=receipt,
                )

            # Gate de política para FULL_ACCESS
            if receipt_mode == ExecutionMode.FULL_ACCESS.value:
                if not self.allow_full_access:
                    raise PolicyViolation(
                        f"Ejecución física DENEGADA para '{tool_name}': El modo FULL_ACCESS está deshabilitado "
                        "en este servidor/entorno por política de seguridad.",
                        action=action,
                        decision=decision,
                        receipt=receipt,
                    )

            # Registrar en conjunto local
            self._consumed_receipts.add(receipt.decision_id)

        # 2. BARRERA DE ENFORCEMENT: Verificar si la herramienta está prohibida en el estado
        if tool_name and tool_name in state.forbidden_tools:
            raise PolicyViolation(
                f"Ejecución física DENEGADA para '{tool_name}': la herramienta está explícitamente PROHIBIDA en este estado.",
                action=action,
                decision=decision,
                receipt=receipt,
            )

        # 3. BARRERA DE ENFORCEMENT: Verificar si la herramienta está registrada
        if tool_name and not self.registry.is_known(tool_name) and tool_name not in self._custom_handlers:
            raise PolicyViolation(
                f"Ejecución física DENEGADA: herramienta desconocida '{tool_name}' no admitida en ToolRegistry.",
                action=action,
                decision=decision,
                receipt=receipt,
            )

        # 4. Modo cognitivo puro (sin herramienta física)
        if not tool_name:
            return ToolObservation(
                output=action.rationale or action.description or "Paso cognitivo registrado.",
                success=True,
                execution_time_ms=0.0,
                tool_name=None,
            )

        # 5. Despacho a controlador personalizado si existe
        if tool_name in self._custom_handlers:
            start_t = time.perf_counter()
            try:
                out = self._custom_handlers[tool_name](tool_args)
                elapsed = (time.perf_counter() - start_t) * 1000.0
                redacted = self.sanitizer.redact_text(out)
                return ToolObservation(
                    output=self.sanitizer.enforce_payload_limit(redacted),
                    success=True,
                    execution_time_ms=round(elapsed, 2),
                    tool_name=tool_name,
                )
            except Exception as e:
                elapsed = (time.perf_counter() - start_t) * 1000.0
                return ToolObservation(
                    output=f"Error en handler de '{tool_name}': {e}",
                    success=False,
                    execution_time_ms=round(elapsed, 2),
                    tool_name=tool_name,
                    is_error=True,
                )

        # 6. Ejecución física según el ExecutionMode resuelto
        start_t = time.perf_counter()
        resolved_mode = (
            getattr(receipt, "execution_mode", state.metadata.get("execution_mode", ExecutionMode.LOCAL_RESTRICTED.value))
            if receipt
            else state.metadata.get("execution_mode", ExecutionMode.LOCAL_RESTRICTED.value)
        )

        if resolved_mode == ExecutionMode.FULL_ACCESS.value:
            effective_dir = (
                state.metadata.get("workspace_root")
                or state.metadata.get("working_directory")
                or get_default_workspace_root()
            )
            fa_res = self.full_access_executor.execute_tool(
                tool_name=tool_name,
                arguments=tool_args,
                context={
                    "working_directory": effective_dir,
                    "session_id": state.session_id,
                },
            )
            raw_output, success, is_error = fa_res.output, fa_res.success, fa_res.is_error
        elif resolved_mode == ExecutionMode.CONTAINER.value:
            if self.container_sandbox is None:
                self.container_sandbox = ContainerSandboxAdapter(
                    config=ContainerSandboxConfig(fallback_to_local=False)
                )
            raw_output, success, is_error = self._execute_builtin_tool_in_container(tool_name, tool_args)
        else:
            raw_output, success, is_error = self._execute_builtin_tool_in_sandbox(tool_name, tool_args, state=state)

        elapsed = (time.perf_counter() - start_t) * 1000.0

        # Aplicar redacción de secretos y límite de payload configurado
        redacted_output = self.sanitizer.redact_text(raw_output)
        final_output = self.sanitizer.enforce_payload_limit(redacted_output)

        return ToolObservation(
            output=final_output,
            success=success,
            execution_time_ms=round(elapsed, 2),
            tool_name=tool_name,
            is_error=is_error,
        )

    def _get_effective_sandbox(self, state: Optional[SessionState] = None) -> SandboxAdapter:
        """Obtiene o instancia el SandboxAdapter adecuado según el workspace_root de la sesión."""
        if state is None:
            return self.sandbox
        ws = (
            state.metadata.get("workspace_root")
            or state.metadata.get("working_directory")
        )
        if ws and isinstance(self.sandbox, LocalProcessSandbox):
            resolved_ws = os.path.realpath(ws)
            if self.sandbox.workspace_root != resolved_ws:
                return LocalProcessSandbox(
                    workspace_root=resolved_ws,
                    allow_network=getattr(self.sandbox, "allow_network", False),
                    egress_policy=getattr(self.sandbox, "egress_policy", None),
                    allow_external_cwd=getattr(self.sandbox, "allow_external_cwd", False),
                    extra_blocked_vars=getattr(self.sandbox, "extra_blocked_vars", None),
                )
        return self.sandbox

    def _execute_builtin_tool_in_sandbox(
        self, tool_name: str, args: Dict[str, Any], state: Optional[SessionState] = None
    ) -> tuple[str, bool, bool]:
        """Ejecuta controladores nativos seguros delegando en el SandboxAdapter efectivo."""
        target_sb = self._get_effective_sandbox(state)

        if tool_name in ("read_file", "view_file"):
            path = str(args.get("path") or args.get("file") or "").strip()
            res = target_sb.read_file(path)
            return res.output, res.success, res.is_error

        elif tool_name == "edit_file":
            path = str(args.get("path") or "").strip()
            content = str(args.get("content") or "").strip()
            res = target_sb.edit_file(path, content)
            return res.output, res.success, res.is_error

        elif tool_name == "run_command":
            cmd = str(args.get("command") or args.get("cmd") or "").strip()
            cwd_arg = args.get("cwd") or (state.metadata.get("working_directory") if state else None)
            res = target_sb.execute_command(cmd, cwd=cwd_arg)
            return res.output, res.success, res.is_error

        elif tool_name in ("finish", "complete_task", "done", "complete", "task_completed"):
            summary = str(args.get("summary") or args.get("final_answer") or args.get("output") or "Tarea completada.").strip()
            return f"Tarea concluida: {summary}", True, False

        # Herramientas o comandos de sistema autorizados (git, pytest, python, npm, etc.)
        elif self.registry.is_known(tool_name):
            cmd_args = str(args.get("command") or args.get("cmd") or args.get("raw") or "").strip()
            full_cmd = f"{tool_name} {cmd_args}".strip() if cmd_args else tool_name
            cwd_arg = args.get("cwd") or (state.metadata.get("working_directory") if state else None)
            res = target_sb.execute_command(full_cmd, cwd=cwd_arg)
            return res.output, res.success, res.is_error

        return f"Herramienta '{tool_name}' sin controlador físico implementado en sandbox.", False, True

    def _execute_builtin_tool_in_container(self, tool_name: str, args: Dict[str, Any]) -> tuple[str, bool, bool]:
        """Ejecuta controladores en ContainerSandboxAdapter sin permitir fallback silencioso a Full Access."""
        if self.container_sandbox is None:
            self.container_sandbox = ContainerSandboxAdapter(
                config=ContainerSandboxConfig(fallback_to_local=False)
            )

        if tool_name in ("read_file", "view_file"):
            path = str(args.get("path") or args.get("file") or "").strip()
            res = self.container_sandbox.read_file(path)
            return res.output, res.success, res.is_error

        elif tool_name == "edit_file":
            path = str(args.get("path") or "").strip()
            content = str(args.get("content") or "").strip()
            res = self.container_sandbox.edit_file(path, content)
            return res.output, res.success, res.is_error

        elif tool_name == "run_command":
            cmd = str(args.get("command") or args.get("cmd") or "").strip()
            res = self.container_sandbox.execute_command(cmd)
            return res.output, res.success, res.is_error

        elif tool_name in ("finish", "complete_task", "done", "complete", "task_completed"):
            summary = str(args.get("summary") or args.get("final_answer") or args.get("output") or "Tarea completada.").strip()
            return f"Tarea concluida: {summary}", True, False

        # Herramientas o comandos de sistema autorizados en container
        elif self.registry.is_known(tool_name):
            cmd_args = str(args.get("command") or args.get("cmd") or args.get("raw") or "").strip()
            full_cmd = f"{tool_name} {cmd_args}".strip() if cmd_args else tool_name
            res = self.container_sandbox.execute_command(full_cmd)
            return res.output, res.success, res.is_error

        return f"Herramienta '{tool_name}' sin controlador en container.", False, True
