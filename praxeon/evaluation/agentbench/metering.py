"""Medición homogénea del LLM: el mismo contador para el agente «desnudo» y para PRAXEON.

`MeteredLLM` envuelve cualquier `BaseAgentLLM` y registra, por llamada: tokens reales (los que informa el proveedor; si no
informa, una estimación marcada como tal), tiempo, coste informado y las acciones que el modelo propone (se analizan con
el mismo parser en los dos brazos, de modo que «acciones propuestas» significa lo mismo en ambos).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from praxeon.agent_llm import BaseAgentLLM
from praxeon.live_agent import parse_llm_steps

CHARS_PER_TOKEN = 4.0  # estimación cuando el proveedor no informa tokens (se cuenta en `estimated_calls`)


@dataclass
class LLMMeter:
    calls: int = 0
    prompt_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    llm_seconds: float = 0.0
    estimated_calls: int = 0
    reported_cost_usd: float = 0.0
    has_reported_cost: bool = False
    failed_calls: int = 0
    proposed: List[Dict[str, Any]] = field(default_factory=list)  # acciones propuestas por el modelo, en orden

    def to_dict(self) -> Dict[str, Any]:
        return {
            "llm_calls": self.calls, "prompt_tokens": self.prompt_tokens, "output_tokens": self.output_tokens,
            "cached_tokens": self.cached_tokens, "total_tokens": self.prompt_tokens + self.output_tokens,
            "llm_seconds": round(self.llm_seconds, 3), "estimated_token_calls": self.estimated_calls,
            "reported_cost_usd": round(self.reported_cost_usd, 6) if self.has_reported_cost else None,
            "failed_llm_calls": self.failed_calls,
        }


class MeteredLLM(BaseAgentLLM):
    """Delegado transparente de un `BaseAgentLLM` que mide cada llamada."""

    def __init__(self, inner: BaseAgentLLM) -> None:
        self.inner = inner
        self.meter = LLMMeter()
        self.last_usage = None

    @property
    def provider_name(self) -> str:
        return self.inner.provider_name

    @property
    def model_name(self) -> str:
        return self.inner.model_name

    @property
    def stateful_session(self) -> bool:  # type: ignore[override]
        return bool(getattr(self.inner, "stateful_session", False))

    def end_session(self) -> None:
        self.inner.end_session()

    def __getattr__(self, name: str) -> Any:  # el runtime puede consultar atributos propios del proveedor
        if name in ("inner", "meter"):
            raise AttributeError(name)
        return getattr(self.inner, name)

    def generate(self, messages: List[Dict[str, str]], system_prompt: Optional[str] = None) -> str:
        m = self.meter
        if hasattr(self.inner, "last_usage"):
            self.inner.last_usage = None
        if hasattr(self.inner, "last_cost_usd"):
            self.inner.last_cost_usd = None
        started = time.perf_counter()
        try:
            output = self.inner.generate(messages, system_prompt=system_prompt)
        except Exception:
            m.failed_calls += 1
            m.llm_seconds += time.perf_counter() - started
            raise
        m.llm_seconds += time.perf_counter() - started
        m.calls += 1
        usage = getattr(self.inner, "last_usage", None)
        if isinstance(usage, dict) and (usage.get("prompt_tokens") or usage.get("output_tokens")):
            m.prompt_tokens += int(usage.get("prompt_tokens") or 0)
            m.output_tokens += int(usage.get("output_tokens") or 0)
            m.cached_tokens += int(usage.get("cached_tokens") or 0)
        else:
            chars = len(system_prompt or "") + sum(len(str(x.get("content", ""))) for x in messages)
            m.prompt_tokens += int(chars / CHARS_PER_TOKEN)
            m.output_tokens += int(len(output) / CHARS_PER_TOKEN)
            m.estimated_calls += 1
        self.last_usage = usage
        cost = getattr(self.inner, "last_cost_usd", None)
        if isinstance(cost, (int, float)):
            m.reported_cost_usd += float(cost)
            m.has_reported_cost = True
        try:
            for step in parse_llm_steps(output)[:1]:
                m.proposed.append({"tool": step.get("tool_name"), "args": step.get("tool_args")})
        except Exception:  # noqa: BLE001 - un formato ilegible simplemente no cuenta como acción propuesta
            pass
        return output


@dataclass
class PriceTable:
    """Precios en USD por millón de tokens. Sin precio conocido el coste queda en None: no se inventa."""

    prices: Dict[str, Dict[str, float]] = field(default_factory=dict)  # clave = subcadena del modelo
    reference: Optional[Dict[str, float]] = None  # precio de referencia común para comparar modelos locales

    @classmethod
    def load(cls, path: Optional[str], reference: Optional[str] = None) -> "PriceTable":
        prices: Dict[str, Dict[str, float]] = {}
        if path:
            prices = json.loads(Path(path).read_text(encoding="utf-8"))
        ref = None
        if reference:
            inp, out = (float(x) for x in reference.split(","))
            ref = {"input": inp, "output": out, "cached": inp}
        return cls(prices=prices, reference=ref)

    def lookup(self, provider: str, model: str) -> Optional[Dict[str, float]]:
        if provider in ("ollama", "lmstudio"):
            return {"input": 0.0, "output": 0.0, "cached": 0.0}  # local: sin coste monetario por token
        for key, price in self.prices.items():
            if key in model or key in provider:
                return price
        return None

    def cost(self, provider: str, model: str, meter: Dict[str, Any]) -> Dict[str, Any]:
        price = self.lookup(provider, model)
        fresh = max(0, meter["prompt_tokens"] - meter["cached_tokens"])
        result: Dict[str, Any] = {"cost_usd": None, "cost_source": "unknown", "reference_cost_usd": None}
        if meter.get("reported_cost_usd") is not None:
            result.update(cost_usd=meter["reported_cost_usd"], cost_source="reported_by_provider")
        elif price is not None:
            usd = (fresh * price["input"] + meter["cached_tokens"] * price.get("cached", price["input"])
                   + meter["output_tokens"] * price["output"]) / 1e6
            result.update(cost_usd=round(usd, 6), cost_source="price_table" if price["input"] or price["output"] else "local_zero")
        if self.reference:
            ref = self.reference
            result["reference_cost_usd"] = round(
                (fresh * ref["input"] + meter["cached_tokens"] * ref["cached"] + meter["output_tokens"] * ref["output"]) / 1e6, 6
            )
        return result
