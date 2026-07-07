"""Teste pentru trading/validation.py (WP-T Etapa 1).

Testul central (`test_best_of_noise_is_not_signal`) demonstrează teza: cea mai bună din N
strategii de ZGOMOT PUR are un Sharpe brut care „pare" semnificativ, dar DSR (deflatat pe
numărul de trial-uri) o respinge corect ca zgomot.
"""
import numpy as np
import pytest

from trading.validation import (
    sharpe_ratio, max_drawdown, probabilistic_sharpe_ratio,
    expected_max_sharpe, deflated_sharpe_ratio, bootstrap_pnl,
    permutation_test, pbo_cscv, classify,
)


# ── statistici de bază ────────────────────────────────────────────────────────
def test_sharpe_basic_and_edges():
    assert sharpe_ratio([1.0, 1.0, 1.0]) is None          # std=0
    assert sharpe_ratio([1.0]) is None                    # <2
    sr = sharpe_ratio([1.0, -1.0, 2.0, -2.0, 3.0])
    assert sr is not None


def test_max_drawdown():
    # equity: 1,3,2,5,1 → peak 5, trough 1 → mdd = -4
    assert max_drawdown([1, 2, -1, 3, -4]) == pytest.approx(-4.0)
    assert max_drawdown([]) == 0.0


# ── PSR / DSR ─────────────────────────────────────────────────────────────────
def test_psr_high_for_strong_signal():
    rng = np.random.default_rng(1)
    r = rng.normal(0.12, 1.0, size=600)          # drift clar pozitiv
    psr = probabilistic_sharpe_ratio(r, 0.0)
    assert psr is not None and psr > 0.95


def test_psr_around_half_for_noise():
    # Mediat pe multe trageri de zgomot, PSR(0) ≈ 0.5 (nedistinct de zero).
    psrs = []
    for seed in range(30):
        r = np.random.default_rng(seed).normal(0.0, 1.0, size=600)
        psrs.append(probabilistic_sharpe_ratio(r, 0.0))
    assert 0.4 < float(np.mean(psrs)) < 0.6


def test_expected_max_sharpe_grows_with_trials():
    rng = np.random.default_rng(3)
    few = rng.normal(0, 0.07, size=5)
    many = rng.normal(0, 0.07, size=200)
    sr0_few = expected_max_sharpe(few)
    sr0_many = expected_max_sharpe(many)
    assert sr0_few is not None and sr0_many is not None
    assert sr0_many > sr0_few                     # mai multe trial-uri ⇒ prag mai sus


def test_best_of_noise_is_not_signal():
    """CRITIC: cel mai bun din 50 de strategii de zgomot pur NU e semnal după deflatare."""
    rng = np.random.default_rng(0)
    T, N = 250, 50
    mat = rng.normal(0.0, 1.0, size=(T, N))       # 50 de strategii = zgomot pur
    sharpes = [sharpe_ratio(mat[:, j]) for j in range(N)]
    best = int(np.argmax(sharpes))
    best_returns = mat[:, best]

    raw_psr = probabilistic_sharpe_ratio(best_returns, 0.0)
    dsr = deflated_sharpe_ratio(best_returns, sharpes)

    assert raw_psr is not None and dsr is not None
    assert raw_psr > 0.90        # Sharpe-ul brut „pare" bun (capcana)
    assert dsr < 0.95            # ...dar deflatarea pe 50 trial-uri îl respinge


def test_genuine_signal_survives_deflation():
    rng = np.random.default_rng(7)
    r = rng.normal(0.3, 1.0, size=1000)                         # semnal puternic, eșantion mare
    trial_sharpes = [sharpe_ratio(r), 0.0, 0.01, -0.02, 0.03]   # puține trial-uri
    dsr = deflated_sharpe_ratio(r, trial_sharpes)
    assert dsr is not None and dsr > 0.95


# ── bootstrap & permutation ───────────────────────────────────────────────────
def test_bootstrap_prob_profit():
    rng = np.random.default_rng(4)
    pos = rng.normal(0.1, 1.0, size=300)
    out = bootstrap_pnl(pos, n=3000)
    assert out["prob_profit"] > 0.9
    assert out["profit_ci90"][0] < out["profit_mean"] < out["profit_ci90"][1]
    assert out["mdd_median"] <= 0.0


def test_permutation_signal_vs_noise():
    rng = np.random.default_rng(5)
    signal = rng.normal(0.15, 1.0, size=400)
    noise = rng.normal(0.0, 1.0, size=400)
    p_signal = permutation_test(signal, n=3000)
    p_noise = permutation_test(noise, n=3000)
    assert p_signal < 0.05        # semnal: improbabil sub nul
    assert p_noise > 0.10         # zgomot: consistent cu nul


# ── PBO/CSCV ──────────────────────────────────────────────────────────────────
def test_pbo_none_on_insufficient_data():
    assert pbo_cscv(np.zeros((4, 1)), n_splits=16) is None      # o singură strategie
    assert pbo_cscv(np.zeros((8, 3)), n_splits=16) is None      # prea puține rânduri


def test_pbo_high_for_pure_noise_matrix():
    rng = np.random.default_rng(6)
    mat = rng.normal(0.0, 1.0, size=(320, 10))     # 10 strategii de zgomot
    pbo = pbo_cscv(mat, n_splits=8)
    assert pbo is not None and pbo > 0.3           # overfitting probabil ridicat pe zgomot


# ── verdict ───────────────────────────────────────────────────────────────────
def test_classify_insufficient_sample():
    v = classify(returns=[0.1, -0.2, 0.3], trial_sharpes=[0.1, 0.0], strategy="x")
    assert v.verdict == "INSUFICIENT"


def test_classify_noise_is_zgomot():
    rng = np.random.default_rng(0)
    T, N = 250, 50
    mat = rng.normal(0.0, 1.0, size=(T, N))
    sharpes = [sharpe_ratio(mat[:, j]) for j in range(N)]
    best = int(np.argmax(sharpes))
    v = classify(returns=mat[:, best], trial_sharpes=sharpes, strategy="best-of-noise")
    assert v.verdict == "ZGOMOT"
    assert v.dsr is not None and v.dsr < 0.95


def test_classify_genuine_signal():
    rng = np.random.default_rng(7)
    r = rng.normal(0.3, 1.0, size=1000)
    trials = [sharpe_ratio(r), 0.0, 0.01, -0.02]
    v = classify(returns=r, trial_sharpes=trials, strategy="real")
    assert v.verdict == "SEMNAL"
    assert v.permutation_p is not None and v.permutation_p < 0.05
