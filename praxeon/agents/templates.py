"""Catálogo Canónico de Plantillas de Agentes (Agent Templates) para PRAXEON (F4-02).

Provee 7 plantillas canónicas preconfiguradas con perfiles de contención, directivas
y listas de herramientas ajustadas a su principio de mínimo privilegio:
1. ProjectManager: Planificación, descomposición de metas y orquestación PM.
2. Developer: Implementación, testing y refactorización técnica.
3. CodeReviewer: Auditoría estática, verificación de estilo y control de calidad (Read-Only).
4. SecurityAuditor: Análisis de vulnerabilidades, compliance y vector de ataque.
5. Researcher: Búsqueda documental, exploración técnica y web.
6. Writer: Documentación formal, reportes de benchmarks y especificaciones.
7. DataAnalyst: Procesamiento de telemetría, métricas cuantitativas y análisis.
"""

from typing import Any, Dict, List, Optional

from praxeon.agents.definition import (
    AgentContextPolicy,
    AgentDefinition,
    AgentStatus,
    ModelConfig,
    RiskProfile,
)
from praxeon.domain.models import RiskLevel


def create_project_manager_template() -> AgentDefinition:
    """Plantilla para el Agente Project Manager (Orquestador / Planner)."""
    return AgentDefinition(
        agent_id="tpl_project_manager",
        name="Project Manager Template",
        role="Project Manager",
        description="Planifica hitos, descompone objetivos y supervisa el progreso del proyecto en Marq PM.",
        system_prompt=(
            "Eres el Project Manager de PRAXEON. Tu función es orquestar la resolución de objetivos, "
            "descomponer misiones complejas en tareas atómicas, supervisar el progreso en el gestor "
            "de proyectos y asignar responsabilidades sin ejecutar mutaciones de código directamente."
        ),
        model=ModelConfig(provider="gemini", model_name="gemini-1.5-pro", temperature=0.1),
        allowed_tools=[
            "pm_list_projects",
            "pm_get_project_tree",
            "pm_create_task",
            "pm_update_task_status",
            "pm_log_time_spent",
            "pm_get_task_timeline",
            "pm_report_blocker",
            "read_file",
            "list_dir",
            "view_file",
        ],
        forbidden_tools=["write_file", "edit_file", "run_destructive_command", "rm", "delete_file"],
        capabilities=["project_management", "planning", "task_tracking"],
        skills=["marq-pm", "workflow-planning"],
        risk_profile=RiskProfile(max_risk_level=RiskLevel.LOW, require_human_confirmation=False),
        context_policy=AgentContextPolicy(max_input_tokens=8192, auto_summarize=True),
    )


def create_developer_template() -> AgentDefinition:
    """Plantilla para el Agente Developer (Implementación y Testing)."""
    return AgentDefinition(
        agent_id="tpl_developer",
        name="Developer Template",
        role="Developer",
        description="Implementa código fuente, escribe tests unitarios y resuelve defectos con verificación continua.",
        system_prompt=(
            "Eres el Desarrollador de Software de PRAXEON. Implementas funcionalidades con tipado estricto, "
            "inmutabilidad, cobertura de pruebas completa y respeto absoluto por las políticas de seguridad. "
            "Verificas proactivamente que cada cambio compile y pase los tests."
        ),
        model=ModelConfig(provider="gemini", model_name="gemini-1.5-pro", temperature=0.2),
        allowed_tools=["*"],
        forbidden_tools=[
            "rm -rf",
            "rmdir",
            "del /f",
            "format",
            "drop table",
            "drop database",
            "shutdown",
            ":(){ :|:& };:",
        ],
        capabilities=["code_authoring", "testing", "refactoring", "debugging"],
        skills=["python-development", "pytest", "dependency-management"],
        risk_profile=RiskProfile(max_risk_level=RiskLevel.MEDIUM, auto_rollback_on_critical=True),
        context_policy=AgentContextPolicy(max_input_tokens=4096, auto_summarize=True),
    )


def create_code_reviewer_template() -> AgentDefinition:
    """Plantilla para el Agente Code Reviewer (Auditoría de Calidad Read-Only)."""
    return AgentDefinition(
        agent_id="tpl_code_reviewer",
        name="Code Reviewer Template",
        role="Code Reviewer",
        description="Analiza diffs, arquitectura y cumplimiento de directrices técnicas sin modificar archivos.",
        system_prompt=(
            "Eres el Revisor de Código de PRAXEON. Tu función es auditar exhaustivamente diffs, estructuras "
            "de tipos, consistencia arquitectónica y mantenibilidad. No tienes permisos para alterar archivos "
            "ni ejecutar comandos en la shell; tu salida consiste en hallazgos objetivos y recomendaciones."
        ),
        model=ModelConfig(provider="gemini", model_name="gemini-1.5-pro", temperature=0.0),
        allowed_tools=["read_file", "view_file", "list_dir", "grep_search"],
        forbidden_tools=["write_file", "edit_file", "run_command", "delete_file", "apply_patch"],
        capabilities=["code_review", "static_analysis", "compliance_check"],
        skills=["clean-code", "architectural-review"],
        risk_profile=RiskProfile(max_risk_level=RiskLevel.LOW),
        context_policy=AgentContextPolicy(max_input_tokens=6144, auto_summarize=True),
    )


def create_security_auditor_template() -> AgentDefinition:
    """Plantilla para el Agente Security Auditor (Análisis de Vulnerabilidades y Riesgo)."""
    return AgentDefinition(
        agent_id="tpl_security_auditor",
        name="Security Auditor Template",
        role="Security Auditor",
        description="Inspecciona vectores de ataque, inyecciones (SQLi, RCE) y gobernanza de secretos.",
        system_prompt=(
            "Eres el Auditor de Seguridad de PRAXEON. Inspeccionas el sistema en busca de vulnerabilidades "
            "de seguridad, inyecciones de comandos, fugas de credenciales o violaciones de invariantes operacionales. "
            "Operas bajo el principio de menor privilegio con verificación formal y trazabilidad criptográfica."
        ),
        model=ModelConfig(provider="gemini", model_name="gemini-1.5-pro", temperature=0.0),
        allowed_tools=["read_file", "view_file", "grep_search", "list_dir"],
        forbidden_tools=["run_command", "write_file", "edit_file", "delete_file"],
        capabilities=["vulnerability_scanning", "security_audit", "threat_modeling"],
        skills=["owasp-top-10", "secure-coding"],
        risk_profile=RiskProfile(max_risk_level=RiskLevel.LOW, require_human_confirmation=True),
        context_policy=AgentContextPolicy(max_input_tokens=6144, auto_summarize=False),
    )


def create_researcher_template() -> AgentDefinition:
    """Plantilla para el Agente Researcher (Exploración Técnica y Documental)."""
    return AgentDefinition(
        agent_id="tpl_researcher",
        name="Researcher Template",
        role="Researcher",
        description="Recupera información técnica, documentación oficial, papers y recursos de referencia.",
        system_prompt=(
            "Eres el Investigador Técnico de PRAXEON. Buscas, sintetizas y estructuras conocimiento de "
            "documentación técnica, APIs, bases de datos científicas y literatura de software para fundamentar "
            "las decisiones de ingeniería del equipo."
        ),
        model=ModelConfig(provider="gemini", model_name="gemini-1.5-flash", temperature=0.3),
        allowed_tools=["search_web", "read_url_content", "read_file", "view_file", "list_dir"],
        forbidden_tools=["write_file", "edit_file", "run_command", "delete_file"],
        capabilities=["web_research", "literature_search", "synthesis"],
        skills=["arxiv-search", "documentation-lookup"],
        risk_profile=RiskProfile(max_risk_level=RiskLevel.LOW),
        context_policy=AgentContextPolicy(max_input_tokens=8192, auto_summarize=True),
    )


def create_writer_template() -> AgentDefinition:
    """Plantilla para el Agente Technical Writer (Documentación y Reportes)."""
    return AgentDefinition(
        agent_id="tpl_writer",
        name="Writer Template",
        role="Writer",
        description="Redacta documentación técnica, informes de benchmarks, guías y especificaciones de arquitectura.",
        system_prompt=(
            "Eres el Redactor Técnico de PRAXEON. Elaboras documentación exhaustiva, clara y estructurada en formato "
            "Markdown (reportes de benchmarks, manuales de usuario, especificaciones de arquitectura y changelogs) "
            "con un tono formal y profesional."
        ),
        model=ModelConfig(provider="gemini", model_name="gemini-1.5-pro", temperature=0.2),
        allowed_tools=["read_file", "write_file", "edit_file", "view_file", "list_dir"],
        forbidden_tools=["run_command", "delete_file", "rm"],
        capabilities=["technical_writing", "documentation", "report_generation"],
        skills=["markdown-standards", "technical-documentation"],
        risk_profile=RiskProfile(max_risk_level=RiskLevel.LOW),
        context_policy=AgentContextPolicy(max_input_tokens=8192, auto_summarize=True),
    )


def create_data_analyst_template() -> AgentDefinition:
    """Plantilla para el Agente Data Analyst (Telemetría, Benchmarks y Métricas)."""
    return AgentDefinition(
        agent_id="tpl_data_analyst",
        name="Data Analyst Template",
        role="Data Analyst",
        description="Analiza métricas de rendimiento, telemetría de ejecución, ratios de compresión y curvas de latencia.",
        system_prompt=(
            "Eres el Analista de Datos de PRAXEON. Procesas telemetría de ejecución, calculas métricas cuantitativas "
            "(CRR, CHR, Decision Preservation, Overhead de tokens), identificas anomalías estadísticas y generas "
            "conclusiones objetivas basadas en evidencia experimental."
        ),
        model=ModelConfig(provider="gemini", model_name="gemini-1.5-pro", temperature=0.1),
        allowed_tools=["read_file", "view_file", "list_dir", "grep_search", "render_chart"],
        forbidden_tools=["run_destructive_command", "rm", "delete_file"],
        capabilities=["data_analysis", "metrics_aggregation", "chart_generation"],
        skills=["metrics-calculation", "benchmark-evaluation"],
        risk_profile=RiskProfile(max_risk_level=RiskLevel.LOW),
        context_policy=AgentContextPolicy(max_input_tokens=8192, auto_summarize=True),
    )


class AgentTemplateCatalog:
    """Registro canónico centralizado de plantillas de agentes."""

    _TEMPLATES: Dict[str, Any] = {
        "project_manager": create_project_manager_template,
        "developer": create_developer_template,
        "code_reviewer": create_code_reviewer_template,
        "security_auditor": create_security_auditor_template,
        "researcher": create_researcher_template,
        "writer": create_writer_template,
        "data_analyst": create_data_analyst_template,
    }

    @classmethod
    def list_templates(cls) -> List[AgentDefinition]:
        """Retorna las 7 plantillas canónicas instanciadas."""
        return [factory() for factory in cls._TEMPLATES.values()]

    @classmethod
    def get_template(cls, identifier: str) -> Optional[AgentDefinition]:
        """Obtiene una plantilla por su clave canónica o rol (case-insensitive)."""
        key = identifier.lower().replace(" ", "_").replace("-", "_")
        factory = cls._TEMPLATES.get(key)
        if factory:
            return factory()

        # Búsqueda por rol
        for tpl_factory in cls._TEMPLATES.values():
            inst = tpl_factory()
            if inst.role.lower() == identifier.lower():
                return inst

        return None

    @classmethod
    def instantiate(
        cls,
        template_name: str,
        agent_id: str,
        name: Optional[str] = None,
        overrides: Optional[Dict[str, Any]] = None,
    ) -> AgentDefinition:
        """Instancia un agente derivado de una plantilla con nueva identidad y overrides."""
        tpl = cls.get_template(template_name)
        if not tpl:
            raise ValueError(f"Plantilla '{template_name}' no encontrada en el catálogo canónico.")

        return tpl.clone(new_agent_id=agent_id, new_name=name, overrides=overrides)
