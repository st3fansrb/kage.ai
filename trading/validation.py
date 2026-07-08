"""Validare statistică pentru laboratorul de trading (WP-T, Etapa 1 — PRIORITATE #1).

Răspunde la o singură întrebare: *ce a produs bucla până acum e SEMNAL sau ZGOMOT?*

Invariant #5: validarea e matematică, NU LLM. Modul pur numpy + `statistics.NormalDist`
(stdlib) — fără scipy, fără mlfinlab (licență comercială). DSR/PBO scrise din papers:
- Bailey & López de Prado, „The Deflated Sharpe Ratio" (2014).
- Bailey, Borwein, López de Prado, Zhu, „The Probability of Backtest Overfitting" (2015, CSCV).

Toate Sharpe-urile de aici sunt **per-observație** (non-anualizate) — consistent pe tot modulul.
Contra data-snooping: DSR deflatează Sharpe-ul pe **numărul de trial-uri** (invariant #3), fiindcă
cel mai bun din N încercări aleatorii are un Sharpe „bun" doar prin șansă.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import NormalDist
from typing import Optional, Sequence

import numpy as np

_NORM = NormalDist()
_EULER_GAMMA = 0.5772156649015329


# ── statistici de bază ────────────────────────────────────────────────────────
def sharpe_ratio(returns: Sequence[float]) -> Optional[float]:
    """Sharpe per-observație = medie/deviație standard. None dacă <2 puncte sau std=0."""
    r = np.asarray(returns, dtype=float)
    if r.size < 2:
        return None
    sd = r.std(ddof=1)
    if sd == 0 or not np.isfinite(sd):
        return None
    return float(r.mean() / sd)


def _skew(r: np.ndarray) -> float:
    sd = r.std(ddof=0)
    if sd == 0:
        return 0.0
    return float(((r - r.mean()) ** 3).mean() / sd ** 3)


def _kurtosis(r: np.ndarray) -> float:
    """Kurtoză NON-excess (normala = 3.0)."""
    sd = r.std(ddof=0)
    if sd == 0:
        return 3.0
    return float(((r - r.mean()) ** 4).mean() / sd ** 4)


def max_drawdown(pnls: Sequence[float]) -> float:
    """Max drawdown pe curba de equity cumulată din PnL-uri (valoare ≤ 0)."""
    eq = np.cumsum(np.asarray(pnls, dtype=float))
    if eq.size == 0:
        return 0.0
    peak = np.maximum.accumulate(eq)
    return float((eq - peak).min())


# ── Probabilistic / Deflated Sharpe Ratio ─────────────────────────────────────
def probabilistic_sharpe_ratio(returns: Sequence[float], sr_benchmark: float = 0.0) -> Optional[float]:
    """PSR(sr*) — probabilitatea ca Sharpe-ul real > benchmark, corectat pe skew/kurtoză.

    PSR = Φ( (SR − SR*)·√(T−1) / √(1 − skew·SR + (kurt−1)/4·SR²) ).
    """
    r = np.asarray(returns, dtype=float)
    T = r.size
    if T < 2:
        return None
    sr = sharpe_ratio(r)
    if sr is None:
        return None
    sk, ku = _skew(r), _kurtosis(r)
    denom_sq = 1.0 - sk * sr + ((ku - 1.0) / 4.0) * sr ** 2
    if denom_sq <= 0 or not math.isfinite(denom_sq):
        return None
    z = (sr - sr_benchmark) * math.sqrt(T - 1) / math.sqrt(denom_sq)
    return float(_NORM.cdf(z))


def expected_max_sharpe(trial_sharpes: Sequence[float], n_trials: Optional[int] = None) -> Optional[float]:
    """SR0 = Sharpe-ul maxim AȘTEPTAT sub nul, din N trial-uri (López de Prado).

    SR0 = √Var(SR)·[ (1−γ)·Φ⁻¹(1−1/N) + γ·Φ⁻¹(1−1/(N·e)) ], γ = Euler–Mascheroni.
    Varianța se estimează din `trial_sharpes`; `n_trials` (dacă e dat — ex. contorul global,
    invariant #3) fixează N, care poate fi mai mare decât câte Sharpe-uri sunt calculabile.
    """
    s = np.asarray(trial_sharpes, dtype=float)
    s = s[np.isfinite(s)]
    if s.size < 2:
        return None
    N = int(n_trials) if n_trials is not None else s.size
    if N < 2:
        return None
    var_sr = float(s.var(ddof=1))
    if var_sr <= 0:
        return 0.0
    a = _NORM.inv_cdf(1.0 - 1.0 / N)
    b = _NORM.inv_cdf(1.0 - 1.0 / (N * math.e))
    return float(math.sqrt(var_sr) * ((1.0 - _EULER_GAMMA) * a + _EULER_GAMMA * b))


def deflated_sharpe_ratio(
    returns: Sequence[float], trial_sharpes: Sequence[float], n_trials: Optional[int] = None
) -> Optional[float]:
    """DSR = PSR(SR0) — probabilitatea ca Sharpe-ul să fie real DUPĂ deflatarea pe N trial-uri.

    `trial_sharpes` = Sharpe-urile calculabile ale încercărilor (pentru varianță); `n_trials` =
    contorul GLOBAL (invariant #3), inclusiv eșecurile. DSR < ~0.95 ⇒ nu putem respinge că e
    produsul selecției pe multe încercări (zgomot).
    """
    sr0 = expected_max_sharpe(trial_sharpes, n_trials=n_trials)
    if sr0 is None:
        return None
    return probabilistic_sharpe_ratio(returns, sr_benchmark=sr0)


# ── bootstrap & permutation ───────────────────────────────────────────────────
def bootstrap_pnl(pnls: Sequence[float], n: int = 10000, seed: Optional[int] = 42) -> dict:
    """Bootstrap (resampling cu replacement) pe secvența de PnL → CI profit + max drawdown.

    Întoarce distribuția (percentile) profitului total ȘI a max drawdown-ului — un interval,
    nu un număr. `prob_profit` = fracția de resamples cu profit total > 0.
    """
    p = np.asarray(pnls, dtype=float)
    if p.size == 0:
        return {"n": 0}
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, p.size, size=(n, p.size))
    samples = p[idx]
    totals = samples.sum(axis=1)
    mdds = np.array([max_drawdown(samples[i]) for i in range(min(n, 2000))])  # mdd e scump: sub-eșantion
    return {
        "n": int(p.size),
        "profit_mean": float(totals.mean()),
        "profit_ci90": [float(np.percentile(totals, 5)), float(np.percentile(totals, 95))],
        "prob_profit": float((totals > 0).mean()),
        "mdd_median": float(np.median(mdds)),
        "mdd_ci90": [float(np.percentile(mdds, 5)), float(np.percentile(mdds, 95))],
    }


def permutation_test(returns: Sequence[float], n: int = 10000, seed: Optional[int] = 42) -> Optional[float]:
    """Test de permutare prin sign-flip pe Sharpe. p-value = fracția de permutări cu Sharpe
    ≥ cel observat. Sub nul (fără edge direcțional) semnele sunt aleatorii; un p mic ⇒ semnal.
    """
    r = np.asarray(returns, dtype=float)
    if r.size < 2:
        return None
    sr_obs = sharpe_ratio(r)
    if sr_obs is None:
        return None
    rng = np.random.default_rng(seed)
    signs = rng.choice([-1.0, 1.0], size=(n, r.size))
    perm = signs * r
    means = perm.mean(axis=1)
    sds = perm.std(axis=1, ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        srs = np.where(sds > 0, means / sds, 0.0)
    return float((srs >= sr_obs).mean())


# ── PBO via CSCV (pentru N strategii pe ACELEAȘI date; folosit în Etapa 5) ─────
def pbo_cscv(returns_matrix: np.ndarray, n_splits: int = 16, seed: Optional[int] = 42) -> Optional[float]:
    """Probability of Backtest Overfitting via Combinatorially Symmetric Cross-Validation.

    `returns_matrix` = (T observații × N strategii) aliniate pe aceleași date. Împarte T în
    `n_splits` blocuri, formează combinații IS/OOS, ia strategia cea mai bună IS și vede unde
    cade OOS. PBO = fracția în care câștigătoarea IS e sub mediana OOS (rank logit ≤ 0).

    Necesită strategii aliniate temporal — peste ledger-ul actual (perioade eterogene) NU e
    aplicabil; devine util în Etapa 5 (candidați backtestați pe același split). None dacă datele
    nu ajung.
    """
    from itertools import combinations

    M = np.asarray(returns_matrix, dtype=float)
    if M.ndim != 2 or M.shape[1] < 2 or M.shape[0] < n_splits or n_splits % 2 != 0:
        return None
    T, Ns = M.shape
    blocks = np.array_split(np.arange(T), n_splits)
    half = n_splits // 2
    logits = []
    for is_idx in combinations(range(n_splits), half):
        is_set = set(is_idx)
        is_rows = np.concatenate([blocks[b] for b in range(n_splits) if b in is_set])
        oos_rows = np.concatenate([blocks[b] for b in range(n_splits) if b not in is_set])
        is_perf = np.array([sharpe_ratio(M[is_rows, j]) or -np.inf for j in range(Ns)])
        oos_perf = np.array([sharpe_ratio(M[oos_rows, j]) or -np.inf for j in range(Ns)])
        best_is = int(np.argmax(is_perf))
        # rangul relativ OOS al câștigătoarei IS (1 = cea mai bună OOS)
        rank = float((oos_perf <= oos_perf[best_is]).mean())  # ∈ (0,1], mare = bun OOS
        rank = min(max(rank, 1e-6), 1 - 1e-6)
        logits.append(math.log(rank / (1.0 - rank)))
    if not logits:
        return None
    return float((np.asarray(logits) <= 0).mean())


# ── verdict pe un experiment ──────────────────────────────────────────────────
@dataclass
class Verdict:
    experiment_id: Optional[int]
    strategy: str
    n_trades: int
    sharpe: Optional[float]
    psr0: Optional[float]              # PSR(0) — Sharpe distinct de zero?
    dsr: Optional[float]              # deflatat pe trial count
    permutation_p: Optional[float]
    prob_profit: Optional[float]
    verdict: str                      # SEMNAL | ZGOMOT | INSUFICIENT
    reasons: list[str] = field(default_factory=list)


def classify(
    returns: Sequence[float],
    trial_sharpes: Sequence[float],
    pnls: Optional[Sequence[float]] = None,
    experiment_id: Optional[int] = None,
    strategy: str = "?",
    dsr_threshold: float = 0.95,
    perm_threshold: float = 0.05,
    min_trades: int = 20,
    n_trials: Optional[int] = None,
) -> Verdict:
    """Verdict SEMNAL/ZGOMOT/INSUFICIENT pentru un experiment.

    SEMNAL cere: destule trade-uri + DSR ≥ prag + permutation p ≤ prag. Conservator: la orice
    lipsă de date sau eșec de prag → ZGOMOT/INSUFICIENT (nu declarăm semnal din dubiu).
    """
    r = np.asarray(returns, dtype=float)
    n = int(r.size)
    reasons: list[str] = []
    sr = sharpe_ratio(r)
    psr0 = probabilistic_sharpe_ratio(r, 0.0)
    dsr = deflated_sharpe_ratio(r, trial_sharpes, n_trials=n_trials)
    perm = permutation_test(r)
    boot = bootstrap_pnl(pnls if pnls is not None else r)
    prob_profit = boot.get("prob_profit")

    if n < min_trades:
        verdict = "INSUFICIENT"
        reasons.append(f"doar {n} trade-uri (< {min_trades}) — eșantion prea mic pentru DSR/PBO")
    else:
        is_signal = (dsr is not None and dsr >= dsr_threshold) and (perm is not None and perm <= perm_threshold)
        verdict = "SEMNAL" if is_signal else "ZGOMOT"
        if dsr is None:
            reasons.append("DSR nedefinit (varianță trial-uri sau denom invalid)")
        elif dsr < dsr_threshold:
            n_eff = n_trials if n_trials is not None else len(list(trial_sharpes))
            reasons.append(f"DSR={dsr:.3f} < {dsr_threshold} — nu supraviețuiește deflatării pe {n_eff} trial-uri")
        if perm is not None and perm > perm_threshold:
            reasons.append(f"permutation p={perm:.3f} > {perm_threshold} — indistinct de zgomot")
        if verdict == "SEMNAL":
            reasons.append("trece DSR + permutation — candidat de investigat (NU promovare automată)")

    return Verdict(
        experiment_id=experiment_id, strategy=strategy, n_trades=n,
        sharpe=sr, psr0=psr0, dsr=dsr, permutation_p=perm,
        prob_profit=prob_profit, verdict=verdict, reasons=reasons,
    )
