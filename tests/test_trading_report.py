"""Teste pentru trading/report.py (WP-T Etapa 1.2) — verdict peste ledger."""
import numpy as np
import pytest

from trading.ledger import TradingLedger, Experiment
from trading.report import verdicts_for_ledger, format_report, run


@pytest.fixture
def seeded_ledger(tmp_path):
    lg = TradingLedger(db_path=tmp_path / "trading.db")
    rng = np.random.default_rng(0)

    # exp1: zgomot, 40 trade-uri închise (PnL ~ N(0,1))
    e1 = lg.record_experiment(Experiment(strategy="noise", pair="BTC/USDT", timeframe="5m"))
    for _ in range(40):
        entry = 100.0
        exit_ = entry + float(rng.normal(0, 1))
        tid = lg.record_paper_trade("BTC/USDT", "long", entry, 1.0, experiment_id=e1)
        lg.close_paper_trade(tid, exit_price=exit_)

    # exp2: prea puține trade-uri → INSUFICIENT
    e2 = lg.record_experiment(Experiment(strategy="tiny", pair="ETH/USDT", timeframe="5m"))
    for _ in range(3):
        tid = lg.record_paper_trade("ETH/USDT", "long", 100.0, 1.0, experiment_id=e2)
        lg.close_paper_trade(tid, exit_price=101.0)

    yield lg, e1, e2
    lg.close()


def test_verdicts_per_experiment(seeded_ledger):
    lg, e1, e2 = seeded_ledger
    verdicts = verdicts_for_ledger(lg, min_trades=20)
    by_id = {v.experiment_id: v for v in verdicts}
    assert len(verdicts) == 2
    assert by_id[e1].verdict == "ZGOMOT"          # 40 trade-uri de zgomot, deflatat → nu e semnal
    assert by_id[e2].verdict == "INSUFICIENT"     # 3 trade-uri < prag


def test_format_report_structure(seeded_ledger):
    lg, _, _ = seeded_ledger
    txt = format_report(verdicts_for_ledger(lg), n_trials=2)
    assert "VERDICT VALIDARE" in txt
    assert "ZGOMOT" in txt and "INSUFICIENT" in txt
    assert "verdict" in txt


def test_run_end_to_end(tmp_path):
    # ledger gol → raport valid, fără crash
    out = run(db_path=str(tmp_path / "empty.db"))
    assert "VERDICT VALIDARE" in out
