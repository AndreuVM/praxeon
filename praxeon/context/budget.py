"""Asignador y gestor de presupuesto de tokens (TokenBudget) para Context Management.

Implementa la jerarquía de prioridades de 8 niveles estipulada en la especificación:
1) Objetivo / requisitos (GOAL)
2) Estado actual / entorno (ENVIRONMENT)
3) Restricciones operacionales (CONSTRAINT)
4) Dependencias directas / veredictos de rama (DECISION)
5) Evidencia contrastada requerida (EVIDENCE)
6) Observaciones recientes (OBSERVATION más recientes)
7) Resúmenes relevantes / memoria episódica (SUMMARY, TASK)
8) Historial secundario y archivos (FILE, observaciones antiguas)
"""

from typing import Any, Callable, Dict, List, Optional, Tuple
from praxeon.context.fragments import ContextFragment, FragmentType, estimate_tokens


# Mapeo de categorías a nivel de prioridad (1 es máxima prioridad, 8 es menor)
DEFAULT_PRIORITY_MAP: Dict[FragmentType, int] = {
    FragmentType.GOAL: 1,
    FragmentType.ENVIRONMENT: 2,
    FragmentType.CONSTRAINT: 3,
    FragmentType.DECISION: 4,
    FragmentType.EVIDENCE: 5,
    FragmentType.OBSERVATION: 6,
    FragmentType.SUMMARY: 7,
    FragmentType.TASK: 7,
    FragmentType.FILE: 8,
}


class TokenBudget:
    """Aplica presupuesto estricto a colecciones de fragmentos respetando la jerarquía de señal."""

    def __init__(
        self,
        default_max_tokens: int = 2048,
        priority_map: Optional[Dict[FragmentType, int]] = None,
        tokenizer: Optional[Callable[[str], int]] = None,
    ):
        self.default_max_tokens = default_max_tokens
        self.priority_map = priority_map or dict(DEFAULT_PRIORITY_MAP)
        self.tokenizer = tokenizer or estimate_tokens

    def get_fragment_priority(self, fragment: ContextFragment, index_in_source: int = 0) -> Tuple[int, int]:
        """Calcula una tupla de ordenación (prioridad_categoria, orden_secundario).
        
        Para observaciones (prioridad 6), se premia la recencia (mayor index = menor orden_secundario).
        """
        cat_prio = self.priority_map.get(fragment.fragment_type, 8)
        # Invertir el índice para que elementos más recientes en el log tengan mayor prioridad
        recency_penalty = -index_in_source if fragment.fragment_type == FragmentType.OBSERVATION else index_in_source
        return (cat_prio, recency_penalty)

    def allocate(
        self,
        fragments: List[ContextFragment],
        max_tokens: Optional[int] = None,
    ) -> Tuple[List[ContextFragment], bool, int]:
        """Selecciona los fragmentos de mayor valor informativo dentro del presupuesto de tokens.
        
        Retorna:
            (fragmentos_seleccionados_en_orden_logico, fue_truncado, tokens_totales)
        """
        budget = max_tokens if max_tokens is not None else self.default_max_tokens
        if not fragments:
            return ([], False, 0)

        # 1. Asociar cada fragmento con su posición original y su peso en tokens
        indexed_items = []
        for idx, frag in enumerate(fragments):
            t_cost = frag.token_estimate if frag.token_estimate > 0 else self.tokenizer(frag.content)
            prio = self.get_fragment_priority(frag, idx)
            indexed_items.append({
                "original_idx": idx,
                "fragment": frag,
                "token_cost": t_cost,
                "priority": prio,
            })

        # 2. Ordenar por prioridad (menor número de prioridad primero)
        indexed_items.sort(key=lambda item: item["priority"])

        # 3. Asignar presupuesto acumulativo
        used_tokens = 0
        selected_indices = set()
        truncated = False

        for item in indexed_items:
            cost = item["token_cost"]
            # Nivel 1 (GOAL) siempre se incluye aunque supere por poco el presupuesto para no romper el objetivo
            is_critical = (item["priority"][0] == 1)
            
            if used_tokens + cost <= budget or (is_critical and used_tokens == 0):
                selected_indices.add(item["original_idx"])
                used_tokens += cost
            else:
                truncated = True

        # 4. Reconstruir la lista seleccionada preservando el orden secuencial/lógico original
        final_selected = [
            fragments[idx] for idx in range(len(fragments)) if idx in selected_indices
        ]

        return (final_selected, truncated, used_tokens)
