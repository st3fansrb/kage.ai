"""Strategie minimă Freqtrade pentru dry-run-ul Kage.

Se copiază în `trading/ft_userdata/strategies/SampleStrategy.py` de scriptul de
pornire. Fișierul activ rămâne gitignored: poate fi ajustat local, fără a pune în
git o strategie promovată accidental. Această versiune este intenționat banală;
scopul T1-exec este să pornească ceasul de paper trading, nu să pretindă un edge.
"""

from freqtrade.strategy import IStrategy
from pandas import DataFrame
import talib.abstract as ta

from trading.daily_context import bias_allows


class SampleStrategy(IStrategy):
    """Cross EMA conservator, permis numai când bias-ul determinist acceptă long."""

    INTERFACE_VERSION = 3
    can_short = False
    timeframe = "5m"
    minimal_roi = {"0": 0.02}
    stoploss = -0.03
    startup_candle_count = 210

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema_fast"] = ta.EMA(dataframe, timeperiod=20)
        dataframe["ema_slow"] = ta.EMA(dataframe, timeperiod=200)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Gardă de regim: nu deschidem nimic dacă daily_context spune flat/short_only.
        if not bias_allows("long"):
            dataframe["enter_long"] = 0
            return dataframe
        dataframe.loc[
            (dataframe["ema_fast"] > dataframe["ema_slow"])
            & (dataframe["rsi"] > 50)
            & (dataframe["volume"] > 0),
            "enter_long",
        ] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (dataframe["ema_fast"] < dataframe["ema_slow"]) & (dataframe["volume"] > 0),
            "exit_long",
        ] = 1
        return dataframe
