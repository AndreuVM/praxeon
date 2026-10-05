"""Clasificador Multidimensional de Tareas (TaskClassifier) (F7-02).

Analiza directivas en lenguaje natural y contexto operacional para inferir:
1. Dificultad y Complejidad (TaskComplexity: TRIVIAL, LOW, MEDIUM, HIGH, CRITICAL).
2. Nivel de Riesgo Operacional (RiskLevel: LOW, MEDIUM, HIGH, CRITICAL).
3. Capacidades requeridas (Capabilities canónicas de agentes).
4. Herramientas indispensables inferidas (run_command, write_file, search_web, etc.).
5. Estimación de volumen de tokens y coste proyectado.
6. Emisión de especificación formal inmutable TaskRequirement.
"""

from datetime import datetime, timezone
import re
from typing import Any, Dict, List, Optional, Set, Tuple
import uuid

from praxeon.agents.protocol import MessagePriority
from praxeon.domain.assessment import RiskLevel
from praxeon.routing.models import TaskComplexity, TaskRequirement


class TaskClassifier:
    """Clasificador multidimensional heurístico y semántico de tareas."""

    # Palabras clave y patrones para inferir nivel de riesgo
    CRITICAL_RISK_PATTERNS = [
        r"\b(drop\s+database|drop\s+table|truncate\s+table|delete\s+from|rm\s+-rf|format\s+disk)\b",
        r"\b(destroy\s+infrastructure|production\s+secret|kill\s+process\s+root|privilege\s+escalation)\b",
        r"\b(hard\s+reset|wipe\s+data|nuclear\s+option|irreversible)\b",
    ]

    HIGH_RISK_PATTERNS = [
        r"\b(auth|authentication|authorization|credential|token|secret|password|private\s+key)\b",
        r"\b(vulnerability|cve|exploit|sql\s+injection|xss|csrf|security\s+audit|pen\s*test)\b",
        r"\b(production\s+deployment|deploy\s+prod|sensitive\s+data|firewall|pki|encryption)\b",
    ]

    MEDIUM_RISK_PATTERNS = [
        r"\b(run_command|exec|bash|shell|terminal|powershell|cmd|cli|system\s+call)\b",
        r"\b(migration|database\s+schema|alter\s+table|refactor\s+core|concurrency|asyncio)\b",
        r"\b(file\s+overwrite|delete\s+file|rmdir|remove\s+file)\b",
    ]

    # Mapeo canónico de patrones hacia capabilities de agentes
    CAPABILITY_PATTERNS: Dict[str, List[str]] = {
        "code_authoring": [
            r"\b(implement|code|write\s+code|develop|create\s+function|create\s+class|feature|script|author)\b",
            r"\b(build\s+component|add\s+endpoint|new\s+endpoint|implement\s+(?:handler|service|interface|endpoint))\b",
        ],
        "testing": [
            r"\b(test|unit\s+test|pytest|mock|assert|fixture|integration\s+test|coverage|tdd)\b",
        ],
        "refactoring": [
            r"\b(refactor|clean\s+code|restructure|optimize|simplify|decouple|modernize)\b",
        ],
        "debugging": [
            r"\b(debug|fix\s+bug|traceback|error|exception|failure|crash|troubleshoot|fix\s+issue)\b",
        ],
        "security_audit": [
            r"\b(security\s+audit|audit|vulnerability|exploit|threat|cve|penetration|bypass)\b",
            r"\b(taint\s+analysis|static\s+analysis|sast|codeql|insecure)\b",
        ],
        "vulnerability_scanning": [
            r"\b(scan\s+vulnerabilities|dependency\s+check|cve\s+scan|sast\s+scan|trivy|bandit)\b",
        ],
        "threat_modeling": [
            r"\b(threat\s+model|stride|dread|attack\s+vector|attack\s+surface|threat\s+assessment)\b",
        ],
        "literature_search": [
            r"\b(literature|academic\s+paper|arxiv|journal|peer\s+review|state\s+of\s+the\s+art|sota)\b",
        ],
        "web_research": [
            r"\b(web\s+search|search\s+web|google|find\s+online|documentation\s+online|latest\s+version)\b",
        ],
        "documentation_analysis": [
            r"\b(analyze\s+doc|read\s+rfc|spec\s+analysis|architecture\s+document|review\s+spec)\b",
        ],
        "technical_writing": [
            r"\b(write\s+docs|readme|documentation|user\s+guide|manual|docstring|architecture\s+notes)\b",
        ],
        "benchmark_reporting": [
            r"\b(benchmark|latency\s+profile|performance\s+report|throughput|load\s+test)\b",
        ],
        "api_documentation": [
            r"\b(api\s+docs|openapi|swagger|endpoints\s+documentation|schema\s+docs)\b",
        ],
    }

    # Herramientas asociadas a capacidades o patrones
    TOOL_INFERENCE_RULES: Dict[str, List[str]] = {
        "run_command": [
            r"\b(command|exec|terminal|bash|shell|run\s+tests|pytest|compile|build\s+project)\b",
        ],
        "write_file": [
            r"\b(create\s+file|write\s+file|implement|new\s+file|scaffold|generate\s+code)\b",
        ],
        "replace_file_content": [
            r"\b(edit\s+file|update\s+file|modify|refactor|replace|patch|fix\s+bug)\b",
        ],
        "grep_search": [
            r"\b(search\s+code|find\s+references|grep|search\s+symbol|locate\s+usages)\b",
        ],
        "view_file": [
            r"\b(read\s+file|inspect\s+file|examine|view|show\s+code|audit\s+file)\b",
        ],
        "search_web": [
            r"\b(search\s+web|web\s+search|find\s+online|google|browse\s+internet)\b",
        ],
    }

    def infer_risk_level(self, prompt: str, target_files: Optional[List[str]] = None) -> Tuple[RiskLevel, List[str]]:
        """Determina el nivel de riesgo inferido y los factores de riesgo detectados."""
        prompt_lower = prompt.lower()
        reasons: List[str] = []

        # 1. Comprobar riesgo CRITICAL
        for pattern in self.CRITICAL_RISK_PATTERNS:
            matches = re.findall(pattern, prompt_lower)
            if matches:
                reasons.append(f"Patrón crítico detectado: {matches[0]}")
                return RiskLevel.CRITICAL, reasons

        # 2. Comprobar riesgo HIGH
        high_detected = False
        for pattern in self.HIGH_RISK_PATTERNS:
            matches = re.findall(pattern, prompt_lower)
            if matches:
                high_detected = True
                reasons.append(f"Factor de alta seguridad: {matches[0]}")

        # Comprobar si los archivos destino incluyen configs de seguridad sensibles
        if target_files:
            for tf in target_files:
                tf_lower = tf.lower()
                if any(sec in tf_lower for sec in ["secret", ".env", "credential", "auth", "security", "token"]):
                    high_detected = True
                    reasons.append(f"Archivo sensible afectado: '{tf}'")

        if high_detected:
            return RiskLevel.HIGH, reasons

        # 3. Comprobar riesgo MEDIUM
        for pattern in self.MEDIUM_RISK_PATTERNS:
            matches = re.findall(pattern, prompt_lower)
            if matches:
                reasons.append(f"Operación potencialmente mutativa o de sistema: {matches[0]}")
                return RiskLevel.MEDIUM, reasons

        # 4. LOW por defecto
        reasons.append("Operación de bajo impacto sin factores de riesgo detectados")
        return RiskLevel.LOW, reasons

    def infer_capabilities(self, prompt: str) -> List[str]:
        """Extrae el conjunto de capacidades canónicas requeridas para la tarea."""
        prompt_lower = prompt.lower()
        matched: List[str] = []

        for cap_name, patterns in self.CAPABILITY_PATTERNS.items():
            for pat in patterns:
                if re.search(pat, prompt_lower):
                    matched.append(cap_name)
                    break

        return sorted(list(set(matched)))

    def infer_required_tools(self, prompt: str, explicit_tools: Optional[List[str]] = None) -> List[str]:
        """Infiere herramientas indispensables combinando explícitas y patrones del prompt."""
        tools: Set[str] = set(explicit_tools or [])
        prompt_lower = prompt.lower()

        for tool_name, patterns in self.TOOL_INFERENCE_RULES.items():
            for pat in patterns:
                if re.search(pat, prompt_lower):
                    tools.add(tool_name)
                    break

        return sorted(list(tools))

    def infer_complexity(
        self,
        prompt: str,
        target_files: Optional[List[str]] = None,
        capabilities: Optional[List[str]] = None,
        risk: RiskLevel = RiskLevel.LOW,
    ) -> Tuple[TaskComplexity, Dict[str, Any]]:
        """Calcula la complejidad multidimensional de la tarea."""
        words = prompt.strip().split()
        word_count = len(words)
        file_count = len(target_files or [])
        caps_count = len(capabilities or [])

        # Puntuación base
        score = 0.0

        # Ponderación por extensión
        if word_count < 15:
            score += 1.0
        elif word_count < 50:
            score += 2.0
        elif word_count < 120:
            score += 3.5
        else:
            score += 5.0

        # Ponderación por archivos
        if file_count == 1:
            score += 1.5
        elif file_count > 1:
            score += 2.5 + min(file_count, 5) * 0.5

        # Ponderación por capacidades involucradas
        score += caps_count * 1.0

        # Ponderación por riesgo
        risk_bonus = {
            RiskLevel.LOW: 0.0,
            RiskLevel.MEDIUM: 1.5,
            RiskLevel.HIGH: 3.5,
            RiskLevel.CRITICAL: 6.0,
        }
        score += risk_bonus.get(risk, 0.0)

        # Palabras de acción de alta complejidad
        high_verbs = ["architect", "redesign", "rewrite", "fuzzing", "consensus", "distributed", "formal verification"]
        if any(v in prompt.lower() for v in high_verbs):
            score += 3.0

        # Mapeo a TaskComplexity
        if score <= 2.5:
            complexity = TaskComplexity.TRIVIAL
        elif score <= 5.5:
            complexity = TaskComplexity.LOW
        elif score <= 9.0:
            complexity = TaskComplexity.MEDIUM
        elif score <= 13.0:
            complexity = TaskComplexity.HIGH
        else:
            complexity = TaskComplexity.CRITICAL

        # Suelo de complejidad según riesgo operacional
        if risk == RiskLevel.CRITICAL:
            complexity = TaskComplexity.CRITICAL if score >= 10.0 else TaskComplexity.HIGH
        elif risk == RiskLevel.HIGH:
            if complexity in [TaskComplexity.TRIVIAL, TaskComplexity.LOW, TaskComplexity.MEDIUM]:
                complexity = TaskComplexity.HIGH

        details = {
            "score": round(score, 2),
            "word_count": word_count,
            "file_count": file_count,
            "caps_count": caps_count,
        }
        return complexity, details

    def estimate_cost_and_tokens(
        self,
        prompt: str,
        target_files: Optional[List[str]] = None,
        complexity: TaskComplexity = TaskComplexity.MEDIUM,
    ) -> Tuple[int, float]:
        """Estima los tokens totales (entrada + salida) y el coste proyectado en USD."""
        words = len(prompt.split())
        prompt_tokens = int(words * 1.33)
        file_overhead = len(target_files or []) * 400
        input_tokens = prompt_tokens + file_overhead

        expected_completion_by_complexity = {
            TaskComplexity.TRIVIAL: 250,
            TaskComplexity.LOW: 600,
            TaskComplexity.MEDIUM: 1500,
            TaskComplexity.HIGH: 3500,
            TaskComplexity.CRITICAL: 6000,
        }
        output_tokens = expected_completion_by_complexity.get(complexity, 1500)
        total_tokens = input_tokens + output_tokens

        # Precios de referencia estándar de inferencia (modelo balanceado):
        # Input: $0.50 / 1M tokens ($0.0000005 por token)
        # Output: $1.50 / 1M tokens ($0.0000015 por token)
        cost = (input_tokens * 0.0000005) + (output_tokens * 0.0000015)
        return total_tokens, round(cost, 6)

    def classify(
        self,
        prompt: str,
        task_id: Optional[str] = None,
        target_files: Optional[List[str]] = None,
        explicit_tools: Optional[List[str]] = None,
        priority: MessagePriority = MessagePriority.NORMAL,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TaskRequirement:
        """Realiza el análisis multidimensional integral y construye el TaskRequirement."""
        tid = task_id or f"task_{uuid.uuid4().hex[:8]}"
        t_files = list(target_files or [])

        # 1. Inferencia de riesgo
        risk, risk_factors = self.infer_risk_level(prompt, t_files)

        # 2. Inferencia de capacidades canónicas
        caps = self.infer_capabilities(prompt)

        # 3. Inferencia de herramientas requeridas
        tools = self.infer_required_tools(prompt, explicit_tools)

        # 4. Inferencia de complejidad
        complexity, comp_details = self.infer_complexity(
            prompt=prompt,
            target_files=t_files,
            capabilities=caps,
            risk=risk,
        )

        # 5. Estimación de volumen de tokens y coste
        est_tokens, est_cost = self.estimate_cost_and_tokens(
            prompt=prompt,
            target_files=t_files,
            complexity=complexity,
        )

        # Consolidar metadatos de auditoría
        meta = dict(metadata or {})
        meta["classification"] = {
            "risk_factors": risk_factors,
            "complexity_details": comp_details,
            "estimated_tokens": est_tokens,
            "estimated_cost": est_cost,
            "classified_at": datetime.now(timezone.utc).isoformat(),
        }

        return TaskRequirement(
            task_id=tid,
            prompt=prompt.strip(),
            required_capabilities=caps,
            required_tools=tools,
            target_files=t_files,
            priority=priority,
            complexity=complexity,
            inferred_risk=risk,
            max_acceptable_cost=round(est_cost * 2.0, 4),  # Margen prudencial 2x
            metadata=meta,
        )
