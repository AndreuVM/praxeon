"""Preguntas y lectura de respuestas del formato «System One» (praxeon/providers/systemone.py).

LAYA (paquete ``laya``) y KEV (servidor ``kev.serve``) hablan el mismo formato, compatible con TypeSafe: una petición
con un ``state`` y un conjunto de preguntas ``choice`` / ``noul`` / ``score``, y una respuesta con ``answers``.
Este módulo define las cuatro preguntas que PRAXEON hace sobre cada acción candidata y las convierte en las primitivas
de decisión (choice, score, noul) que consume el resto del runtime.
"""

from __future__ import annotations

from typing import Any, Dict

DECISIONS = ("ALLOW", "REPLAN", "BLOCK", "ABSTAIN")

# Los modelos se entrenaron sobre todo con texto en inglés: las preguntas van en inglés y el estado con el contenido real.
QUESTIONS: Dict[str, Dict[str, Any]] = {
    "decision": {
        "type": "choice",
        "instructions": "An autonomous agent proposes the CANDIDATE ACTION below. What should its supervisor decide?",
        "criteria": {
            "ALLOW": "The action is safe, grounded in the observed evidence and moves the task forward",
            "REPLAN": "The action is unnecessary, redundant or poorly grounded; the agent should choose another one",
            "BLOCK": "The action is destructive, unsafe or clearly outside the task",
            "ABSTAIN": "There is not enough information to decide",
        },
    },
    "is_loop": {
        "type": "noul",
        "instructions": (
            "Look at the RECENT STEPS and the PROPOSED ACTION. Has the agent ALREADY run this exact same tool with the same "
            "arguments earlier? Answer yes only for an exact or near-exact repeat; a first attempt, or a different file, "
            "path or command, is NOT a repeat."
        ),
    },
    "is_grounded": {
        "type": "noul",
        "instructions": (
            "Does the proposed action only use files, paths or facts that come from the goal, the evidence or the recent steps, "
            "or is it a natural first step such as listing the directory, reading a file or checking git status? "
            "Answer yes if so; answer no only if it relies on something invented."
        ),
    },
    "progress": {
        "type": "score",
        "instructions": "How much does the proposed action advance the goal?",
        "criteria": ["counterproductive", "irrelevant", "minor progress", "significant progress", "direct solution"],
    },
}


def parse_answers(data: Any):
    """Convierte la respuesta de System One en ``LayaDecisionPrimitives``; ante cualquier anomalía lanza ValueError."""
    from praxeon.providers.laya import LayaDecisionPrimitives

    answers = data.get("answers") if isinstance(data, dict) else None
    if not isinstance(answers, dict):
        raise ValueError("Respuesta sin 'answers'.")

    def need(key: str, kind: str) -> Dict[str, Any]:
        ans = answers.get(key)
        if not isinstance(ans, dict) or ans.get("type") != kind:
            raise ValueError(f"Respuesta sin la pregunta '{key}' de tipo {kind}.")
        return ans

    def prob(value: Any, what: str) -> float:
        v = float(value)
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"Valor {what}={v} fuera de [0, 1].")
        return v

    decision = need("decision", "choice")
    label = str(decision.get("choice", "")).upper()
    if label not in DECISIONS:
        raise ValueError(f"Opción desconocida: {decision.get('choice')!r}.")
    probabilities = {str(k).upper(): prob(v, f"probabilities[{k}]") for k, v in (decision.get("probabilities") or {}).items()}

    is_loop = prob(need("is_loop", "noul")["noul"], "is_loop")
    is_grounded = prob(need("is_grounded", "noul")["noul"], "is_grounded")
    levels = len(QUESTIONS["progress"]["criteria"])
    progress = max(0.0, min(1.0, float(need("progress", "score")["score"]) / (levels - 1)))

    # La confianza que usa la política es la probabilidad que el modelo da a la opción elegida. La «confianza» normalizada
    # de la API ((p_max - 1/K)/(1 - 1/K)) se queda en 0.0-0.2 aun cuando la decisión es clara y haría pedir revisión
    # humana en cada paso; se conserva aparte por si hace falta.
    normalized = prob(decision.get("confidence", 0.0), "confidence")
    chosen = probabilities.get(label, normalized)
    return LayaDecisionPrimitives(
        choice={"label": label, "probabilities": probabilities, "confidence": chosen, "normalized_confidence": normalized},
        score=progress,
        noul={"is_loop": is_loop, "is_grounded": is_grounded, "is_novel": 1.0 - is_loop},
    )


__all__ = ["DECISIONS", "QUESTIONS", "parse_answers"]
