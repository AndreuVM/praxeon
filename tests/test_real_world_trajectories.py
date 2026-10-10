"""Pruebas unitarias de benchmark de trayectorias de agentes del mundo real (SWE-bench / GAIA).

Valida la Fase 5 del Roadmap:
- Integración de datasets de ingeniería de software (SWE-bench Lite) y asistentes (GAIA).
- Presencia de más de 30 pasos cronológicos por trayectoria.
- Detección de bucles cognitivos repetitivos y ejecución de rollbacks de recuperación.
- Cálculo de métricas cuantitativas: completion_rate, recovery_rate, tokens mitigados y resiliencia.
"""

from praxeon.evaluation.real_world_trajectories import (
    RealWorldBenchmarkCatalog,
    RealWorldDatasetType,
    RealWorldTrajectoryReport,
    RealWorldTrajectoryRunner,
    RealWorldTrajectoryScenario,
)
from praxeon.domain.models import DecisionStatus


class TestRealWorldTrajectories:
    """Verifica la generación y catalogación de escenarios SWE-bench y GAIA."""

    def test_swebench_django_scenario_structure(self):
        scenario = RealWorldBenchmarkCatalog.get_swebench_django_scenario()
        assert isinstance(scenario, RealWorldTrajectoryScenario)
        assert scenario.dataset_type == RealWorldDatasetType.SWEBENCH_LITE
        assert scenario.instance_id == "django__django-11099"
        # Debe contener más de 30 pasos cronológicos
        assert len(scenario.steps) >= 30
        assert scenario.steps[0].action.tool_call.arguments["command"] == "pwd"

        # Debe contener paso de detección de bucle cognitivo
        loop_steps = [s for s in scenario.steps if s.expected_status == DecisionStatus.REPLAN]
        assert len(loop_steps) >= 1
        assert loop_steps[0].induces_rollback is True
        assert loop_steps[0].expected_backtrack is True

        # El último paso debe ser finish
        last_step = scenario.steps[-1]
        assert last_step.action.tool_call.tool_name == "finish"
        assert last_step.expected_status == DecisionStatus.ALLOW

    def test_gaia_financial_audit_scenario_structure(self):
        scenario = RealWorldBenchmarkCatalog.get_gaia_financial_audit_scenario()
        assert isinstance(scenario, RealWorldTrajectoryScenario)
        assert scenario.dataset_type == RealWorldDatasetType.GAIA
        assert scenario.instance_id == "gaia-val-level3-audit-088"
        # Debe contener al menos 30 pasos
        assert len(scenario.steps) >= 30

        # Debe tener paso de mitigación de inyección / acción no autorizada con rollback
        veto_steps = [s for s in scenario.steps if s.induces_rollback]
        assert len(veto_steps) >= 1
        assert veto_steps[0].expected_status == DecisionStatus.ABSTAIN

        # El paso final debe ser finish
        assert scenario.steps[-1].action.tool_call.tool_name == "finish"

    def test_catalog_all_scenarios(self):
        scenarios = RealWorldBenchmarkCatalog.get_all_real_world_scenarios()
        assert len(scenarios) >= 2
        for sc in scenarios:
            assert len(sc.steps) >= 30
            assert sc.goal.objective is not None
            assert len(sc.goal.success_criteria) > 0


class TestRealWorldTrajectoryRunner:
    """Verifica la ejecución determinista del benchmark de trayectorias reales."""

    def test_run_real_world_benchmark_execution(self):
        runner = RealWorldTrajectoryRunner(token_cost_per_wasted_step=500)
        report = runner.run_benchmark()

        assert isinstance(report, RealWorldTrajectoryReport)
        assert report.total_trajectories >= 2
        assert report.completed_trajectories == report.total_trajectories
        assert report.completion_rate == 1.0
        assert report.total_steps >= 60  # Al menos 32 + 31 = 63 pasos
        assert report.avg_steps_per_trajectory >= 30.0

        # Resiliencia y recuperación ante bucles
        assert report.loops_intercepted >= 1
        assert report.rollbacks_successful >= 1
        assert report.recovery_rate == 1.0
        assert report.estimated_wasted_tokens_prevented > 0
        assert report.resilience_score == 1.0
