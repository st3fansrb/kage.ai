"""Teste WP-T Etapa 4: registru de ipoteze (pre-registration), calibrare, baseline/event study."""
import time

import numpy as np
import pytest

from trading.ledger import TradingLedger
from trading import hypotheses as H
from trading.calibration import brier_score, interval_coverage, calibration_report
from trading.validation import ks_statistic, signal_moves_distribution, event_study


@pytest.fixture
def ledger(tmp_path):
    lg = TradingLedger(db_path=tmp_path / "trading.db")
    yield lg
    lg.close()


# ── pre-registration (invariant #2) ──────────────────────────────────────────
def test_register_and_predict_ok(ledger):
    hid = H.register_hypothesis(
        ledger,
        mechanism="funding extrem forțează long-ii la lichidare în 48h",
        prediction={"kind": "point", "mean": -1.5, "interval_80": [-6.0, 2.0], "horizon": "48h"},
        falsification="dacă randamentul mediu la 48h post-semnal > 0 pe 30 instanțe",
    )
    assert hid > 0
    pid = H.record_prediction(
        ledger, hid, signal_ts="2099-01-01T00:00:00",   # semnal ulterior pre-înregistrării
        predicted={"kind": "point", "mean": -1.5, "interval_80": [-6.0, 2.0]}, horizon="48h")
    assert pid > 0


def test_prediction_without_hypothesis_refused(ledger):
    with pytest.raises(H.PreRegistrationError):
        H.record_prediction(ledger, 999, "2026-07-08T10:00:00",
                            {"kind": "prob", "prob": 0.6, "horizon": "7d"})


def test_prediction_before_preregistration_refused(ledger):
    hid = H.register_hypothesis(
        ledger, mechanism="m", falsification="f",
        prediction={"kind": "prob", "prob": 0.6, "horizon": "7d"})
    # semnal ANTERIOR pre-înregistrării → storytelling post-hoc refuzat
    with pytest.raises(H.PreRegistrationError):
        H.record_prediction(ledger, hid, "2000-01-01T00:00:00",
                            {"kind": "prob", "prob": 0.6, "horizon": "7d"})


def test_malformed_prediction_refused(ledger):
    with pytest.raises(H.HypothesisFormatError):
        H.register_hypothesis(ledger, mechanism="m", falsification="f",
                              prediction={"kind": "point", "horizon": "48h"})  # fără mean/interval


def test_resolve_and_status(ledger):
    hid = H.register_hypothesis(ledger, mechanism="m", falsification="f",
                                prediction={"kind": "prob", "prob": 0.7, "horizon": "7d"})
    pid = H.record_prediction(ledger, hid, time.strftime("%Y-%m-%dT%H:%M:%S"),
                              {"kind": "prob", "prob": 0.7, "horizon": "7d"})
    H.resolve(ledger, pid, {"occurred": 1})
    H.mark_hypothesis(ledger, hid, "confirmed")
    assert ledger.get_predictions(resolved=True)[0]["realized"]["occurred"] == 1
    assert ledger.get_hypothesis(hid)["status"] == "confirmed"


# ── calibrare ─────────────────────────────────────────────────────────────────
def test_brier_and_coverage():
    # predicții perfecte → Brier 0
    assert brier_score([(1.0, 1), (0.0, 0)]) == pytest.approx(0.0)
    assert brier_score([(0.5, 1), (0.5, 0)]) == pytest.approx(0.25)
    # 3 din 4 valori în interval → coverage 0.75
    pairs = [(1.0, [0, 2]), (3.0, [0, 2]), (0.5, [0, 2]), (1.5, [0, 2])]
    assert interval_coverage(pairs) == pytest.approx(0.75)


def test_calibration_report_over_ledger(ledger):
    hid = H.register_hypothesis(ledger, mechanism="m", falsification="f",
                                prediction={"kind": "prob", "prob": 0.8, "horizon": "7d"})
    for occ in (1, 1, 0, 1):
        pid = H.record_prediction(ledger, hid, time.strftime("%Y-%m-%dT%H:%M:%S"),
                                  {"kind": "prob", "prob": 0.8, "horizon": "7d"})
        H.resolve(ledger, pid, {"occurred": occ})
    rep = calibration_report(ledger)
    assert "Brier" in rep and "CALIBRARE" in rep


# ── baseline condiționat & event study (4.3) ──────────────────────────────────
def test_ks_signal_moves_distribution():
    rng = np.random.default_rng(0)
    baseline = rng.normal(0, 1, size=500)
    signal = rng.normal(1.5, 1, size=120)          # distribuție clar mutată
    out = signal_moves_distribution(signal, baseline, n=1000)
    assert out["ks"] > 0.3 and out["p"] < 0.05


def test_ks_no_move_for_same_distribution():
    rng = np.random.default_rng(1)
    a = rng.normal(0, 1, size=300)
    b = rng.normal(0, 1, size=300)
    out = signal_moves_distribution(a, b, n=1000)
    assert out["p"] > 0.05                          # aceeași distribuție → nu se mută

def test_event_study():
    rng = np.random.default_rng(2)
    # 40 evenimente, fereastră de 11 (offsets -5..+5); drift pozitiv după T=0
    windows = rng.normal(0.0, 0.5, size=(40, 11))
    windows[:, 6:] += 1.0
    es = event_study(windows)
    assert es["n_events"] == 40 and len(es["mean_by_offset"]) == 11
    assert es["mean_by_offset"][7] > es["mean_by_offset"][3]
    assert event_study([[1, 2, 3]]) is None         # <2 evenimente
