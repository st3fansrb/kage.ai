import pytest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from pathlib import Path

from trading.nocturnal import NocturnalLoop
from trading.ledger import TradingLedger
from trading.runner import FreqtradeRunner

@pytest.fixture
def ledger(tmp_path):
    """Ledger în tmp_path pentru teste izolate."""
    l = TradingLedger(db_path=tmp_path / "trading.db")
    yield l
    l.close()

@pytest.fixture
def runner(tmp_path):
    """Runner mock-uit in tmp_path."""
    venv = tmp_path / ".trading-venv"
    venv.mkdir()
    config = tmp_path / "ft_config_dry.json"
    config.write_text("{}")
    userdata = tmp_path / "ft_userdata"
    userdata.mkdir()
    (userdata / "data").mkdir()
    (userdata / "backtest_results").mkdir()
    (userdata / "strategies").mkdir()
    
    with patch("trading.nocturnal.FreqtradeRunner", return_value=FreqtradeRunner(venv_dir=venv, config_path=config, userdata_dir=userdata)):
        yield FreqtradeRunner(venv_dir=venv, config_path=config, userdata_dir=userdata)

@pytest.fixture
def loop_instance(ledger, runner):
    return NocturnalLoop(iterations=2, mission_id="test-mission", ledger=ledger, runner=runner)

class TestNocturnalLoop:

    @patch.object(NocturnalLoop, "get_mutation_from_llm", new_callable=AsyncMock)
    @patch("trading.nocturnal.subprocess.run")
    @patch.object(FreqtradeRunner, "_find_and_parse_latest")
    def test_run_iteration_flow(self, mock_parse, mock_subprocess, mock_llm, loop_instance):
        # Setup mocks
        mock_llm.return_value = "class MutatedStrategy_1(IStrategy):\n    pass"
        mock_subprocess.return_value = MagicMock(returncode=0)
        
        # Mock rezultatul parsat al backtestului
        mock_parse.return_value = {
            "strategy": "MutatedStrategy_1",
            "total_trades": 10,
            "profit_total": 0.05,
            "profit_total_abs": 50.0,
            "profit_factor": 1.2,
            "max_drawdown": 0.02,
            "max_drawdown_abs": 20.0,
            "wins": 6,
            "losses": 4,
            "draws": 0,
            "avg_profit": 0.005,
            "holding_avg": "1:00:00",
            "trade_count_long": 10,
            "trade_count_short": 0,
            "backtest_start": "2026-01-01 00:00:00",
            "backtest_end": "2026-02-01 00:00:00",
            "pairs": [{"pair": "BTC/USDT"}],
            "trades_detail": []
        }

        # Rulăm iterația 1
        exp_id = loop_instance.run_iteration(1)
        
        assert exp_id is not None
        assert mock_llm.called
        assert mock_subprocess.called
        assert mock_parse.called
        
        # Verificăm fișierul generat
        strategy_path = loop_instance.runner.userdata_dir / "strategies" / "MutatedStrategy_1.py"
        assert strategy_path.exists()
        assert "class MutatedStrategy_1" in strategy_path.read_text()
        
        # Verificăm că a scris în ledger
        experiments = loop_instance.ledger.get_experiments()
        assert len(experiments) == 1
        assert experiments[0]["strategy"] == "MutatedStrategy_1"
        assert experiments[0]["mission_id"] == "test-mission"
        assert experiments[0]["metrics"]["profit_total_abs"] == 50.0

    @patch.object(NocturnalLoop, "get_mutation_from_llm", new_callable=AsyncMock)
    def test_run_iteration_llm_failure(self, mock_llm, loop_instance):
        mock_llm.return_value = None
        exp_id = loop_instance.run_iteration(1)
        assert exp_id is None
        experiments = loop_instance.ledger.get_experiments()
        assert len(experiments) == 0

    @patch.object(NocturnalLoop, "get_mutation_from_llm", new_callable=AsyncMock)
    @patch("trading.nocturnal.subprocess.run")
    def test_run_iteration_subprocess_failure(self, mock_subprocess, mock_llm, loop_instance):
        mock_llm.return_value = "class MutatedStrategy_1(IStrategy):\n    pass"
        # Subprocess returnează cod de eroare (ex: eroare sintaxă freqtrade)
        mock_subprocess.return_value = MagicMock(returncode=1, stderr="Syntax Error")
        
        exp_id = loop_instance.run_iteration(1)
        assert exp_id is None
        experiments = loop_instance.ledger.get_experiments()
        assert len(experiments) == 0

    @patch("trading.nocturnal.httpx.AsyncClient.post")
    def test_telegram_notification(self, mock_post, loop_instance, monkeypatch):
        # Setăm config-ul Kage de test
        def mock_config():
            return {"telegram_bot_token": "fake_token", "telegram_chat_id": "123"}
        monkeypatch.setattr("trading.nocturnal.get_kage_config", mock_config)
        
        mock_post.return_value = MagicMock(status_code=200)
        
        # Rulăm raportul asincron
        asyncio.run(loop_instance.report_to_telegram(best_exp_id=None, total_run=0))
        
        assert mock_post.called
        call_kwargs = mock_post.call_args.kwargs
        assert "fake_token" in mock_post.call_args[0][0]
        assert call_kwargs["json"]["chat_id"] == "123"
        assert "Nu au rezultat" in call_kwargs["json"]["text"]
