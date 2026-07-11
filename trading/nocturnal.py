"""WP-T Slice 3 — bucla nocturnă NAÏVĂ. ⚠️ DEPRECATED (07.07.2026).

Neutralizată la reorientarea Quant Lab (vezi `docs/QUANT_LAB_DESIGN.md`): această buclă
încalcă doi invarianți — LLM-ul rescrie CODUL strategiei (invariant #1: LLM-ul nu decide
execuția) și selectează pe **profit in-sample maxim**, fără validare statistică / contor de
trial-uri (invariant #3/#5) → overfitting garantat.

Rămâne în repo doar ca referință. Rularea e blocată (guard în `run()` + `__main__`); a fost
ÎNLOCUITĂ de `trading/pipeline.py` (`NightlyPipeline` — Actor→Critic→Validare, Etapa 5). Pentru
a o rula totuși (nerecomandat), pasează `--force-legacy`.
"""

import argparse
import asyncio
import httpx
import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

# Setup path ca să poată importa modulele locale
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from trading.ledger import TradingLedger, PAPER_ONLY
from trading.runner import FreqtradeRunner
from trading.safety import assert_paper_only

logger = logging.getLogger("nocturnal")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Citim config-ul Kage pentru a lua Telegram gateway url
KAGE_CONFIG_PATH = _PROJECT_ROOT / "kage_config.json"

def get_kage_config():
    if KAGE_CONFIG_PATH.exists():
        with open(KAGE_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

class NocturnalLoop:
    def __init__(self, iterations: int = 3, mission_id: Optional[str] = None, 
                 ledger: Optional[TradingLedger] = None, 
                 runner: Optional[FreqtradeRunner] = None):
        self.iterations = iterations
        self.mission_id = mission_id or "nocturnal-auto"
        self.ledger = ledger or TradingLedger()
        self.runner = runner or FreqtradeRunner()
        self.base_strategy_path = self.runner.userdata_dir / "strategies" / "SampleStrategy.py"
        
        # Asigurăm-ne că SampleStrategy există
        if not self.base_strategy_path.exists():
            self._create_base_strategy()

        # Configurăm httpx client pentru LiteLLM
        self.llm_url = "http://localhost:4000/v1/chat/completions"
        self.model = "tier-2-worker" # model din litellm_config.yaml

    def _create_base_strategy(self):
        """Creează strategia de bază dacă lipsește."""
        self.base_strategy_path.parent.mkdir(parents=True, exist_ok=True)
        content = '''
from freqtrade.strategy import IStrategy
from pandas import DataFrame
import talib.abstract as ta

class SampleStrategy(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = '5m'
    can_short = True

    minimal_roi = {"0": 0.05}
    stoploss = -0.02
    trailing_stop = False

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe['ema20'] = ta.EMA(dataframe, timeperiod=20)
        dataframe['ema50'] = ta.EMA(dataframe, timeperiod=50)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (dataframe['ema20'] > dataframe['ema50']) &
            (dataframe['volume'] > 0),
            ['enter_long', 'enter_tag']] = (1, 'ema_cross')
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (dataframe['ema20'] < dataframe['ema50']) &
            (dataframe['volume'] > 0),
            ['exit_long', 'exit_tag']] = (1, 'ema_cross_exit')
        return dataframe
'''
        self.base_strategy_path.write_text(content.strip(), encoding="utf-8")

    async def get_mutation_from_llm(self, iteration: int) -> Optional[str]:
        """Cere lui Qwen o mutație."""
        base_code = self.base_strategy_path.read_text(encoding="utf-8")
        
        prompt = f"""
Ești un asistent de tranzacționare algoritmică.
Mai jos este codul unei strategii Freqtrade de bază. Vreau să generezi o variantă a acestei strategii 
pentru a încerca să-i îmbunătățim performanța.

Reguli:
1. Schimbă numele clasei în `MutatedStrategy_{iteration}`.
2. Poți modifica perioadele indicatoarelor (ex. EMA20 -> EMA21), ROI, stoploss, sau poți adăuga indicatori simpli precum RSI.
3. Răspunde DOAR cu codul Python complet și corect. Fără explicații.
4. Fii conservator în modificări pentru a evita erorile de sintaxă.

Codul de bază:
```python
{base_code}
```
"""
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    self.llm_url,
                    headers={"Authorization": "Bearer sk-orchestrator-local"},
                    json={
                        "model": self.model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.3
                    }
                )
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                
                # Extragem doar blocul de cod python dacă e marcat cu markdown
                match = re.search(r"```python\n(.*?)```", content, re.DOTALL)
                if match:
                    return match.group(1).strip()
                return content.strip()
        except Exception as e:
            logger.error(f"Eroare la apelul LLM: {e}")
            return None

    def run_iteration(self, iteration: int) -> Optional[int]:
        """Rulează o iterație completă și returnează ID-ul experimentului."""
        logger.info(f"--- Iterația {iteration} / {self.iterations} ---")
        
        # 1. Obținem mutația asincron (blocăm aici pt simplitate de execuție)
        new_code = asyncio.run(self.get_mutation_from_llm(iteration))
        if not new_code:
            logger.error("Nu s-a putut genera mutația.")
            return None

        strategy_name = f"MutatedStrategy_{iteration}"
        # Ne asigurăm că numele clasei e corect în cod (fallback fallback)
        if f"class {strategy_name}" not in new_code:
            new_code = re.sub(r"class \w+\(IStrategy\):", f"class {strategy_name}(IStrategy):", new_code)
        
        strategy_file = self.runner.userdata_dir / "strategies" / f"{strategy_name}.py"
        strategy_file.write_text(new_code, encoding="utf-8")
        logger.info(f"Strategie salvată: {strategy_name}.py")

        # 2. Rulăm backtest-ul
        # Comanda backtest va rula pe datele din cache (downloadate de runner anterior, deci BTC/USDT ETH/USDT)
        # Atenție: trebuie ca datele să fie deja prezente. Dacă rulezi E2E, le ai.
        cmd = self.runner.build_backtest_command(
            strategy=strategy_name,
            timeframe="5m"
        )
        
        logger.info(f"Rulăm freqtrade backtesting pentru {strategy_name}...")
        try:
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode != 0:
                logger.error(f"Backtest eșuat: {res.stderr}")
                return None
        except Exception as e:
            logger.error(f"Subprocess error: {e}")
            return None

        # 3. Parsăm rezultatul și scriem în ledger
        results_dir = self.runner.userdata_dir / "backtest_results"
        bt_result = self.runner._find_and_parse_latest(results_dir)
        if not bt_result:
            logger.error("Nu s-au găsit rezultate parșabile.")
            return None

        exp_id = self.runner.backtest_to_ledger(
            bt_result, 
            self.ledger, 
            mission_id=self.mission_id, 
            notes=f"Nocturnal loop iteration {iteration}"
        )
        
        logger.info(f"Iterație finalizată cu succes. Experiment_id={exp_id}. Profit: {bt_result.get('profit_total_abs', 0):.2f}")
        return exp_id

    async def report_to_telegram(self, best_exp_id: Optional[int], total_run: int):
        """Trimite raport către Telegram via gateway-ul intern."""
        config = get_kage_config()
        tg_token = config.get("telegram_bot_token")
        tg_chat_id = config.get("telegram_chat_id")
        
        if not tg_token or not tg_chat_id:
            logger.info("Telegram nu e configurat, sărim peste notificare.")
            return

        msg = f"🌙 <b>Misiune Nocturnă Finalizată</b>\n\nIterații rulate: {total_run}/{self.iterations}\n"
        if best_exp_id:
            exp = next((e for e in self.ledger.get_experiments() if e["id"] == best_exp_id), None)
            if exp:
                p = exp.get('metrics', {}).get('profit_total_abs', 0)
                pf = exp.get('metrics', {}).get('profit_factor', 0)
                msg += f"\n🏆 <b>Cel mai bun rezultat:</b> {exp['strategy']}\n"
                msg += f"Profit: {p:.2f} | PF: {pf:.2f}"
        else:
            msg += "\nNu au rezultat strategii profitabile/valide."

        try:
            # Folosim API-ul Telegram direct pentru notificare
            url = f"https://api.telegram.org/bot{tg_token}/sendMessage"
            async with httpx.AsyncClient() as client:
                await client.post(url, json={
                    "chat_id": tg_chat_id,
                    "text": msg,
                    "parse_mode": "HTML"
                })
            logger.info("Notificare trimisă pe Telegram.")
        except Exception as e:
            logger.error(f"Eroare trimitere Telegram: {e}")

    def run(self, force_legacy: bool = False):
        if not force_legacy:
            raise RuntimeError(
                "nocturnal.py NAÏV e dezactivat (overfitting prin selecție pe profit in-sample). "
                "Vezi docs/QUANT_LAB_DESIGN.md; folosește pipeline-ul Actor→Critic→Validare. "
                "Pentru rulare forțată: run(force_legacy=True) / --force-legacy."
            )
        logger.info(f"Începem bucla nocturnă: {self.iterations} iterații.")
        results = []
        for i in range(1, self.iterations + 1):
            exp_id = self.run_iteration(i)
            if exp_id:
                results.append(exp_id)
        
        best_id = None
        if results:
            exps = [e for e in self.ledger.get_experiments() if e["id"] in results]
            if exps:
                # Găsim experimentul cu profit_total cel mai mare
                best_exp = max(exps, key=lambda x: x.get("metrics", {}).get("profit_total", -999))
                best_id = best_exp["id"]

        asyncio.run(self.report_to_telegram(best_id, len(results)))
        self.ledger.close()

if __name__ == "__main__":
    assert PAPER_ONLY is True, "Modulul safety.PAPER_ONLY a fost compromis!"
    parser = argparse.ArgumentParser(description="Trading Nocturnal Loop")
    parser.add_argument("--iterations", type=int, default=3, help="Număr de iterații")
    parser.add_argument("--mission-id", type=str, default="nocturnal-auto")
    parser.add_argument("--force-legacy", action="store_true",
                        help="Rulează bucla NAÏVĂ deprecated (nerecomandat — overfitting)")
    args = parser.parse_args()

    loop = NocturnalLoop(iterations=args.iterations, mission_id=args.mission_id)
    loop.run(force_legacy=args.force_legacy)
