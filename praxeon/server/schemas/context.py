"""Esquemas tipados para métricas de contexto estimadas vs facturadas (actual LLM tokens)."""

from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class EstimatedContextMetricsDTO(BaseModel):
    """Métricas heurísticas de tokens estimadas localmente por el optimizador de contexto."""
    model_config = ConfigDict(frozen=True)

    estimated_context_tokens_before: int = Field(default=0, description="Tokens estimados antes de optimización y caché")
    estimated_context_tokens_after: int = Field(default=0, description="Tokens estimados tras poda y presupuestación")
    estimated_context_tokens_saved: int = Field(default=0, description="Tokens estimados ahorrados por reutilización de fragmentos o snapshots")
    estimated_reduction_ratio: float = Field(default=0.0, description="Ratio heurístico de reducción de contexto")


class ActualLLMTokenMetricsDTO(BaseModel):
    """Métricas reales de tokens consumidos y facturados reportados por la API del LLM."""
    model_config = ConfigDict(frozen=True)

    actual_prompt_tokens: int = Field(default=0, description="Tokens reales enviados en el prompt a la API del LLM")
    actual_cached_tokens: int = Field(default=0, description="Tokens del prompt servidos desde el context caching del LLM (ej. Gemini/Anthropic)")
    actual_output_tokens: int = Field(default=0, description="Tokens reales generados por el modelo")
    actual_billed_tokens: int = Field(default=0, description="Tokens efectivamente facturados (prompt - cached + output)")
    cache_hit_status: str = Field(default="none", description="Estado de caché del LLM: hit, prefix_hit, miss, none")


class ContextMetricsDTO(BaseModel):
    """Esquema unificado que segrega explícitamente métricas estimadas de métricas reales facturadas."""
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    # Métricas Estimadas (Heurísticas locales)
    estimated_context_tokens_before: int = Field(default=0, alias="context_tokens_before")
    estimated_context_tokens_after: int = Field(default=0, alias="context_tokens_after")
    estimated_context_tokens_saved: int = Field(default=0, alias="context_tokens_saved")
    estimated_reduction_ratio: float = Field(default=0.0, alias="context_reduction_ratio")

    # Métricas Reales (Facturadas por proveedor LLM)
    actual_prompt_tokens: int = 0
    actual_cached_tokens: int = 0
    actual_output_tokens: int = 0
    actual_billed_tokens: int = 0
    cache_hit_status: str = "none"

    # Estadísticas operativas de caché de contexto local (L1/L2)
    context_builds_total: int = 0
    context_rebuilds_total: int = 0
    rebuild_count: int = 0
    context_cache_hits_total: int = 0
    context_prefix_hits_total: int = 0
    context_cache_misses_total: int = 0
    context_cache_hit_rate: float = 0.0
    context_invalidations_total: int = 0
    context_cache_evictions_total: int = 0
    eviction_count: int = 0
    snapshot_evictions: int = 0
    fragment_evictions: int = 0
    snapshot_cache_hits: int = 0
    prefix_cache_hits: int = 0
    context_fragments_reused: int = 0
    context_build_latency_ms: float = 0.0
    cache_entries: int = 0
    fragment_cache_entries: int = 0
    fragment_cache_hits: int = 0
    fragment_cache_misses: int = 0
    fragment_cache_evictions: int = 0

    @property
    def context_tokens_before(self) -> int:
        return self.estimated_context_tokens_before

    @property
    def context_tokens_after(self) -> int:
        return self.estimated_context_tokens_after

    @property
    def context_tokens_saved(self) -> int:
        return self.estimated_context_tokens_saved

    @property
    def context_reduction_ratio(self) -> float:
        return self.estimated_reduction_ratio
