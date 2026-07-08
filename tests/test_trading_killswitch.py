"""Teste WP-T Etapa 2: contorul global de trial-uri + kill-switch determinist."""
import numpy as np
import pytest

from trading.ledger import TradingLedger, Experiment
from trading import killswitch as ks
from trading.validation import deflated_sharpe_ratio, sharpe_ratio


@pytest.fixture
def ledger(tmp_path):
    lg = TradingLedger(db_path=tmp_path / "trading.db")
    yield lg
    lg.close()


# ── contorul global de trial-uri ─────────────────────────────────────────────
def test_trials_counter(ledger):
    assert ledger.count_trials() == 0
    ledger.record_trial(strategy="a", outcome="ok")
    ledger.record_trial(strategy="a", outcome="failed")     # eșecurile SE numără (invariant #3)
    ledger.record_trial(strategy="b")
    assert ledger.count_trials() == 3
    assert len(ledger.get_trials()) == 3


def test_dsr_harder_with_more_trials():
    rng = np.random.default_rng(1)
    r = rng.normal(0.15, 1.0, size=600)
    trial_sharpes = [sharpe_ratio(r), 0.05, -0.03, 0.02]
    dsr_few = deflated_sharpe_ratio(r, trial_sharpes, n_trials=4)
    dsr_many = deflated_sharpe_ratio(r, trial_sharpes, n_trials=500)
    assert dsr_few is not None and dsr_many is not None
    assert dsr_many < dsr_few                # mai multe trial-uri ⇒ deflatare mai severă


# ── kill-switch ──────────────────────────────────────────────────────────────
def _add_losers(ledger, exp_id, n, pnl_each):
    """Adaugă n tranzacții cu PnL controlat (long, amount ales să dea pnl_each)."""
    for _ in range(n):
        # long: pnl = (exit-entry)*amount ; entry=100, exit=99 ⇒ pnl = -1*amount
        tid = ledger.record_paper_trade("BTC/USDT", "long", 100.0, abs(pnl_each), experiment_id=exp_id)
        ledger.close_paper_trade(tid, exit_price=99.0 if pnl_each < 0 else 101.0)


def test_no_data_no_trip(ledger):
    calls = []
    out = ks.check(ledger, threshold=-0.15, wallet=1000.0, notify=calls.append)
    assert out["drawdown"] == 0.0 and out["tripped_now"] is False and out["halted"] is False
    assert calls == []


def test_killswitch_trips_on_drawdown(ledger):
    e = ledger.record_experiment(Experiment(strategy="loser", pair="BTC/USDT", timeframe="5m"))
    _add_losers(ledger, e, n=10, pnl_each=-20.0)     # −200 pe wallet 1000 = −20%
    calls = []
    out = ks.check(ledger, threshold=-0.15, wallet=1000.0, notify=calls.append)
    assert out["drawdown"] <= -0.15
    assert out["tripped_now"] is True and out["halted"] is True
    assert len(calls) == 1 and "KILL-SWITCH" in calls[0]


def test_killswitch_idempotent(ledger):
    e = ledger.record_experiment(Experiment(strategy="loser", pair="BTC/USDT", timeframe="5m"))
    _add_losers(ledger, e, n=10, pnl_each=-20.0)
    calls = []
    ks.check(ledger, threshold=-0.15, wallet=1000.0, notify=calls.append)
    ks.check(ledger, threshold=-0.15, wallet=1000.0, notify=calls.append)  # a doua oară
    assert len(calls) == 1                    # nu re-notifică cât timp e deja halted


def test_killswitch_no_trip_small_loss(ledger):
    e = ledger.record_experiment(Experiment(strategy="mild", pair="BTC/USDT", timeframe="5m"))
    _add_losers(ledger, e, n=2, pnl_each=-20.0)      # −40 = −4%
    out = ks.check(ledger, threshold=-0.15, wallet=1000.0, notify=lambda m: None)
    assert out["tripped_now"] is False and out["halted"] is False


def test_clear_killswitch(ledger):
    ledger.trip_killswitch("test", -0.2)
    assert ledger.is_halted() is True
    ledger.clear_killswitch()
    assert ledger.is_halted() is False


def test_decay_candidates(ledger):
    bleeder = ledger.record_experiment(Experiment(strategy="bleeder", pair="BTC/USDT", timeframe="5m"))
    _add_losers(ledger, bleeder, n=12, pnl_each=-5.0)       # 12 trade-uri, profit total negativ
    winner = ledger.record_experiment(Experiment(strategy="winner", pair="ETH/USDT", timeframe="5m"))
    _add_losers(ledger, winner, n=12, pnl_each=+5.0)        # profit pozitiv
    cands = ks.decay_candidates(ledger, min_trades=10)
    assert "bleeder" in cands
    assert "winner" not in cands
