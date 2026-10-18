"""Renderizado del estado para el modelo con el mismo ajuste de longitud que usa la inferencia (`_STATE_FITS`).

Sin esto, los estados reales (mediana ~800 tokens) se recortan por el final y el modelo aprende de texto cortado. Aquí se
prueba cada tamaño con el tokenizador del checkpoint y `build_sequence` hasta que ninguna pregunta pierde tokens del estado.
"""

from __future__ import annotations

import hashlib
from typing import Any, Callable, Optional

from praxeon.providers.context import ProviderContext
from praxeon.providers.laya import _STATE_FITS, build_laya_state
from praxeon.providers.systemone import QUESTIONS

_CACHE: dict = {}


def load_tokenizer(model_dir: str) -> Any:
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(f"{model_dir}/tokenizer")


def tokens_dropped(tok: Any, state: str, max_len: int = 1024, head_max_len: int = 256) -> int:
    """Tokens del estado que se pierden en la pregunta más ajustada."""
    import laya.train as lt

    worst = 0
    for qid, question in QUESTIONS.items():
        q = lt.to_internal(qid, question)
        result = lt.build_sequence(tok, state, q, max_len=max_len, head_max_len=head_max_len, return_truncation_stats=True)
        stats = result[-1]
        worst = max(worst, int(stats["state_tokens_dropped"]))
    return worst


def masked_by_id(case_id: str, probability: float = 0.5) -> bool:
    """Decisión estable (por id) de ocultar las banderas del clasificador en un caso."""
    return int(hashlib.sha1(case_id.encode("utf-8")).hexdigest()[:8], 16) / 0xFFFFFFFF < probability


def make_renderer(tok: Any, record_version: int = 2, mask_probability: float = 0.5,
                  max_len: int = 1024, head_max_len: int = 256) -> Callable[[ProviderContext], str]:
    def render(ctx: ProviderContext) -> str:
        cid = str((ctx.candidate_action or {}).get("id") or "") + ctx.session_id + ctx.goal
        masked = masked_by_id(cid, mask_probability)
        state = ""
        for steps, chars in _STATE_FITS:
            state = build_laya_state(ctx, steps, chars, with_classifier=not masked, record_version=record_version)
            if tokens_dropped(tok, state, max_len, head_max_len) == 0:
                break
        return state

    return render
