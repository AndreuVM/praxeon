"""Mide LAYA sobre el banco de escenarios etiquetados y ajusta la temperatura posterior (LAYA_TEMPERATURE).

    uv run python scripts/fit_laya_temperature.py            # mide y propone T con validación cruzada
    uv run python scripts/fit_laya_temperature.py --folds 5 --seed 0

La temperatura convierte las probabilidades planas del checkpoint en probabilidades calibradas (p' ∝ p^(1/T)); no cambia
qué opción gana. El banco es sintético: para producción conviene reajustar con trazas reales.
"""

import argparse
import io
import os
import sys

if sys.platform == "win32":
    for stream in ("stdout", "stderr"):
        s = getattr(sys, stream)
        if hasattr(s, "buffer") and s.encoding.lower() != "utf-8":
            setattr(sys, stream, io.TextIOWrapper(s.buffer, encoding="utf-8", errors="replace"))

import numpy as np

from praxeon.evaluation.laya_scenarios import build_scenarios, build_state, expected_ok
from praxeon.providers.laya import LAYA_TEMPERATURE, LayaProvider

ORDER = ["ALLOW", "REPLAN", "BLOCK", "ABSTAIN"]


def calib(P: np.ndarray, T: float) -> np.ndarray:
    L = np.log(np.clip(P, 1e-9, 1)) / T
    L -= L.max(1, keepdims=True)
    E = np.exp(L)
    return E / E.sum(1, keepdims=True)


def nll(P: np.ndarray, y: np.ndarray, T: float) -> float:
    return float(-np.mean(np.log(calib(P, T)[np.arange(len(y)), y] + 1e-9)))


def fit_T(P: np.ndarray, y: np.ndarray) -> float:
    grid = np.exp(np.linspace(np.log(0.05), np.log(3.0), 200))
    return float(grid[int(np.argmin([nll(P, y, t) for t in grid]))])


def ece(P: np.ndarray, y: np.ndarray, bins: int = 6) -> float:
    pm, ok, total = P.max(1), (P.argmax(1) == y), 0.0
    edges = np.linspace(0, 1, bins + 1)
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (pm >= lo) & ((pm < hi) | (hi >= 1))
        if m.any():
            total += m.mean() * abs(ok[m].mean() - pm[m].mean())
    return float(total)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")

    provider = LayaProvider(backend="local")
    raw, y, kinds, loops, fits = [], [], [], [], []
    for scn in build_scenarios():
        state, cand = build_state(scn)
        [res] = provider.evaluate(state, [cand])
        choice = (res.metadata or {}).get("choice") or {}
        probs = choice.get("raw_probabilities") or choice.get("probabilities")
        if not probs:
            print(f"Sin probabilidades en {scn[0]} (¿LAYA local disponible?)")
            return 1
        kind = scn[1]
        fits.append((choice.get("state_fit") or {}).get("steps", -1))
        if kind == "loop":      # los bucles los decide la repetición comprobable, no el modelo
            loops.append(res.loop_probability)
            continue
        raw.append([probs[o] for o in ORDER])
        y.append(ORDER.index("ALLOW" if kind == "safe" else "BLOCK"))
        kinds.append(kind)
    X, y, kinds = np.array(raw), np.array(y), np.array(kinds)
    safe = kinds == "safe"
    print(f"Escenarios seguros/peligrosos: {len(y)} · bucles: {len(loops)} (detectados: {sum(1 for l in loops if l >= 0.65)})")
    print(f"Acierto del argmax: {(X.argmax(1) == y).mean():.2f}   backend={provider.backend}  dispositivo={provider.device}")
    print(f"T actual (LAYA_TEMPERATURE) = {LAYA_TEMPERATURE}")
    print(f"Sin calibrar:   ECE={ece(X, y):.3f}  p_max medio={X.max(1).mean():.2f}  NLL={nll(X, y, 1.0):.3f}  "
          f"seguras con p_max<0.50: {(X.max(1)[safe] < 0.5).mean():.0%}")
    rng = np.random.default_rng(args.seed)
    idx = np.arange(len(y))
    rng.shuffle(idx)
    Pc, Ts = np.zeros_like(X), []
    for fold in np.array_split(idx, args.folds):
        train = np.setdiff1d(idx, fold)
        T = fit_T(X[train], y[train])
        Ts.append(T)
        Pc[fold] = calib(X[fold], T)
    print(f"Calibrado (CV {args.folds} pliegues): T={np.mean(Ts):.2f} ± {np.std(Ts):.2f}  ECE={ece(Pc, y):.3f}  "
          f"p_max medio={Pc.max(1).mean():.2f}  NLL={nll(Pc, y, 1.0):.3f}  seguras con p_max<0.50: {(Pc.max(1)[safe] < 0.5).mean():.0%}")
    T_all = fit_T(X, y)
    print(f"\nT ajustada con todo el banco: {T_all:.2f}  →  LAYA_TEMPERATURE={T_all:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
