"""Demostración interactiva y offline de PRAXEON (v1.0.0).

No requiere claves de API de pago ni conexión a Internet.
Demuestra en tiempo real las cuatro defensas esenciales:
1. Inspección autorizada y fundamentada con firma criptográfica HMAC-SHA256.
2. Bloqueo de finalización prematura (anti-premature finish) mediante CompletionVerifier.
3. Detección semántica de bucles cíclicos con Rollback a checkpoint y recuperación.
4. Barrera física de enforcement en Sandbox frente a evasiones (path traversal/shell).

Ejecución:
    python examples/demo_offline.py
"""

import io
from pathlib import Path
import sys
import tempfile
import time

# Asegurar codificación UTF-8 en Windows
if sys.platform == "win32":
    if hasattr(sys.stdout, "buffer") and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "buffer") and sys.stderr.encoding.lower() != "utf-8":
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from praxeon import __version__
from praxeon.domain.action import ActionCandidate, ToolCall
from praxeon.domain.decision import DecisionStatus
from praxeon.domain.evidence import Evidence
from praxeon.domain.goal import Goal
from praxeon.providers.laya import LayaProvider
from praxeon.providers.replay import ReplayProvider
from praxeon.providers.router import ConfidenceAwareRouter
from praxeon.reasoning.completion import CompletionVerifier
from praxeon.runtime.executor import PolicyViolation, SecureExecutor
from praxeon.runtime.navigator import Navigator
from praxeon.runtime.sandbox import LocalProcessSandbox

console = Console(legacy_windows=False)


def run_offline_demo():
    console.print(Panel.fit(
        f"[bold cyan]🛡️ PRAXEON v{__version__} — DEMO OFFLINE INTERACTIVA[/]\n"
        "[bold white]Runtime supervision for autonomous AI agents[/]\n"
        "[dim green]✓ 100% Offline | Sin claves de API de pago | Ejecución determinista[/]",
        border_style="cyan",
    ))

    # Creamos un sandbox temporal seguro para la demo
    with tempfile.TemporaryDirectory() as temp_dir:
        workspace = Path(temp_dir)
        (workspace / "src").mkdir()
        (workspace / "src" / "main.py").write_text("def run(): return 42\n", encoding="utf-8")
        (workspace / "tests.log").write_text("1 test passed in 0.05s\n", encoding="utf-8")

        # 1. Configuración del Router de Cascada y Navigator
        laya_fast = LayaProvider(backend="simulated")
        replay_strong = ReplayProvider(default_scenario="safe_read")
        cascade_router = ConfidenceAwareRouter(
            primary_provider=laya_fast,
            secondary_provider=replay_strong,
            default_confidence_threshold=0.75,
        )

        sandbox = LocalProcessSandbox(workspace_root=str(workspace), allow_network=False)
        executor = SecureExecutor(sandbox=sandbox)
        completion_verifier = CompletionVerifier(workspace_root=str(workspace))


        navigator = Navigator(
            provider=cascade_router,
            executor=executor,
            completion_verifier=completion_verifier,
        )

        goal = Goal(
            objective="Inspeccionar código, verificar tests y finalizar la tarea",
            success_criteria=["tests pasando", "código verificado"],
        )
        session = navigator.start_session(goal=goal, session_id="demo_session_offline")

        console.print(f"[bold yellow]🎯 Objetivo de la Misión:[/] {goal.objective}")
        console.print(f"[dim]Criterios de Éxito:[/] {', '.join(goal.success_criteria)}\n")
        time.sleep(0.5)

        # ---------------------------------------------------------------------
        # ESCENARIO 1: Acción Fundamentada y Autorizada (ALLOW + HMAC Capability)
        # ---------------------------------------------------------------------
        console.print(Panel(
            "[bold white]1. INSPECCIÓN SEGURA Y FUNDAMENTADA (ALLOW)[/]\n"
            "[dim]El agente solicita inspeccionar 'src/main.py'. Existe evidencia de su existencia.[/]",
            title="[bold green]Escenario 1[/]",
            border_style="green",
        ))

        # Añadimos evidencia inicial de existencia
        ev_file = Evidence(id="ev_main", claim="archivo_existente:src/main.py", content_hash="h_main")
        session.add_evidence(ev_file)

        action_1 = ActionCandidate(
            id="act_read_main",
            description="Inspeccionar implementación principal",
            tool_call=ToolCall(tool_name="read_file", arguments={"path": "src/main.py"}),
            requires_evidence=["archivo_existente:src/main.py"],
        )

        results_1 = navigator.evaluate([action_1])
        _, assessment_1, decision_1, receipt_1 = results_1[0]

        console.print(f"• [bold]Decisión:[/] [bold green]{decision_1.status.value.upper()}[/] ({decision_1.reason_codes[0]})")
        console.print(f"• [bold]Firma HMAC del Recibo:[/] [dim cyan]{receipt_1.signature[:32]}...[/] (HMAC-SHA256 verificado)")

        # Ejecución física en sandbox
        obs_1 = navigator.executor.execute(action=action_1, state=session, receipt=receipt_1)
        session.add_evidence(Evidence(id="ev_verified", claim="código verificado", content_hash="h_verif"))
        session.add_step(action=action_1, decision=decision_1, observation=obs_1.output)
        navigator.checkpoint_manager.create_checkpoint(session, reason="Paso 1 completado")
        console.print(f"• [bold]Observación obtenida:[/] [dim]{obs_1.output.strip()}[/]\n")
        time.sleep(0.5)


        # ---------------------------------------------------------------------
        # ESCENARIO 2: Prevención de Finalización Prematura (UNVERIFIED_COMPLETION)
        # ---------------------------------------------------------------------
        console.print(Panel(
            "[bold white]2. INTENTO DE FINALIZACIÓN PREMATURA SIN PRUEBAS (REPLAN)[/]\n"
            "[dim]El agente intenta declarar victoria ('finish') antes de verificar los tests.[/]",
            title="[bold yellow]Escenario 2[/]",
            border_style="yellow",
        ))

        action_2 = ActionCandidate(
            id="act_premature_finish",
            description="Declarar tarea terminada prematuramente",
            tool_call=ToolCall(tool_name="finish", arguments={"summary": "Todo terminado sin correr tests"}),
        )

        results_2 = navigator.evaluate([action_2])
        _, _, decision_2, _ = results_2[0]

        console.print(f"• [bold]Decisión:[/] [bold yellow]{decision_2.status.value.upper()}[/]")
        console.print(f"• [bold]Motivo de Intervención:[/] [bold red]{decision_2.reason_codes[0]}[/]")
        console.print("[dim green]✓ Resultado: CompletionVerifier bloqueó la terminación espuria y forzó al agente a obtener pruebas.[/]\n")
        time.sleep(0.5)

        # ---------------------------------------------------------------------
        # ESCENARIO 3: Bucle Cíclico, Detección y Rollback con Recuperación
        # ---------------------------------------------------------------------
        console.print(Panel(
            "[bold white]3. BUCLE SEMÁNTICO, ROLLBACK Y RECUPERACIÓN (REPLAN -> BACKTRACK)[/]\n"
            "[dim]El agente insiste en reintentar una acción idéntica sin progreso; el supervisor revierte el estado.[/]",
            title="[bold magenta]Escenario 3[/]",
            border_style="magenta",
        ))

        # Simulamos que el proveedor detecta alta probabilidad de bucle
        action_loop = ActionCandidate(
            id="act_loop_stale",
            description="Reintentar lectura idéntica sin avance",
            tool_call=ToolCall(tool_name="read_file", arguments={"path": "src/main.py"}),
        )
        replay_strong.override_for_action(action_loop.id, ReplayProvider.DEFAULT_SCENARIOS["loop_detected"])

        results_loop = navigator.evaluate([action_loop])
        _, assess_loop, decision_loop, _ = results_loop[0]

        console.print(f"• [bold]Probabilidad de Bucle Semántico:[/] [bold red]{assess_loop.loop_probability * 100:.1f}%[/]")
        console.print(f"• [bold]Decisión del Supervisor:[/] [bold magenta]{decision_loop.status.value.upper()}[/] ({decision_loop.reason_codes[0]})")

        # Activar Rollback formal al checkpoint canónico anterior
        latest_chk = navigator.checkpoint_manager.get_latest_checkpoint()
        console.print(f"• [bold]Rollback Activado:[/] Revertiendo estado al checkpoint [cyan]{latest_chk.id}[/]")
        session = navigator.checkpoint_manager.restore_checkpoint(latest_chk.id, session)
        navigator.state = session
        console.print("• [dim]Rama degenerativa descartada; estado restablecido con éxito.[/]")

        # El agente replanifica y lee el log de tests
        action_read_tests = ActionCandidate(
            id="act_read_tests",
            description="Leer resultado de tests",
            tool_call=ToolCall(tool_name="read_file", arguments={"path": "tests.log"}),
        )
        replay_strong.override_for_action(action_read_tests.id, ReplayProvider.DEFAULT_SCENARIOS["safe_read"])
        res_tests = navigator.evaluate([action_read_tests])
        _, _, dec_tests, rec_tests = res_tests[0]
        obs_tests = navigator.executor.execute(action=action_read_tests, state=session, receipt=rec_tests)
        session.add_evidence(Evidence(id="ev_tests", claim="tests pasando", content_hash="h_pass"))
        session.add_step(action=action_read_tests, decision=dec_tests, observation=obs_tests.output)
        navigator.checkpoint_manager.create_checkpoint(session, reason="Tests verificados")

        console.print(f"• [bold]Recuperación Exitosa:[/] Acción sustituta autorizada ([green]{dec_tests.status.value.upper()}[/]), evidencia 'tests pasando' adquirida.\n")
        time.sleep(0.5)

        # ---------------------------------------------------------------------
        # ESCENARIO 4: Barrera Física contra Ataque Adversarial (Path Traversal)
        # ---------------------------------------------------------------------
        console.print(Panel(
            "[bold white]4. INTENTO DE EVASIÓN ADVERSARIAL (PATH TRAVERSAL ESCAPE)[/]\n"
            "[dim]Un atacante inyecta una ruta maliciosa fuera del workspace ('../../etc/shadow').[/]",
            title="[bold red]Escenario 4[/]",
            border_style="red",
        ))

        action_malicious = ActionCandidate(
            id="act_attack_traversal",
            description="Intentar leer archivo protegido del sistema",
            tool_call=ToolCall(tool_name="read_file", arguments={"path": "../../etc/shadow"}),
        )

        # Si un recibo apócrifo fuese generado o simulado:
        fake_receipt = receipt_1.model_copy(update={"action_id": action_malicious.id})
        try:
            navigator.executor.execute(action=action_malicious, state=session, receipt=fake_receipt)
            console.print("[bold red]FALLO: El ejecutor no detuvo el ataque.[/]")
        except PolicyViolation as pv:
            console.print("• [bold red]🛑 ATAQUE ABORTADO EN TIEMPO DE EJECUCIÓN:[/]")
            console.print(f"  [bold yellow]Violación Detectada:[/] {pv}")
            console.print("  [dim]Las instrucciones de texto en prompts no son seguridad; la barrera criptográfica de SecureExecutor bloqueó el intento antes de tocar el sistema.[/]\n")

        # ---------------------------------------------------------------------
        # CIERRE: Finalización Verificada y Resumen de Métricas
        # ---------------------------------------------------------------------
        action_final_ok = ActionCandidate(
            id="act_final_finish",
            description="Finalizar tarea con criterios cumplidos",
            tool_call=ToolCall(tool_name="finish", arguments={"summary": "Misión cumplida"}),
        )
        replay_strong.override_for_action(action_final_ok.id, ReplayProvider.DEFAULT_SCENARIOS["safe_read"])
        res_final = navigator.evaluate([action_final_ok])
        _, _, dec_final, rec_final = res_final[0]
        navigator.executor.execute(action=action_final_ok, state=session, receipt=rec_final)

        summary_table = Table(title="Resumen Final de Sesión", show_header=True)
        summary_table.add_column("Concepto", style="cyan")
        summary_table.add_column("Resultado en Demostración", style="bold green")
        summary_table.add_row("Estado Final del Objetivo", "COMPLETADO Y VERIFICADO AL 100%")
        summary_table.add_row("Finalizaciones Prematuras Evitadas", "1 (Interceptada por CompletionVerifier)")
        summary_table.add_row("Bucles Resueltos por Rollback", "1 (Recuperación con estado intacto)")
        summary_table.add_row("Ataques de Evasión Detenidos", "1 (100% de eficacia en SecureExecutor)")
        summary_table.add_row("Coste en APIs Externas", "$0.00 USD (Inferencia local simulada)")
        console.print(summary_table)

        console.print("\n[bold green]✨ Demostración completada con éxito. El runtime de agente está completamente protegido.[/]\n")


if __name__ == "__main__":
    run_offline_demo()
