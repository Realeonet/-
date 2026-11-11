"""Слой-адаптер котировок. Источник меняется заменой класса, без правок расчётов.

В запрос уходят только тикеры и даты — никаких сумм, позиций или счетов.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Protocol

import pandas as pd


class PriceSource(Protocol):
    name: str

    def history(self, symbol: str, start: date, end: date) -> pd.Series:
        """Дневные цены закрытия: индекс — дата (datetime64, без времени), значения — float."""
        ...


class YFinancePriceSource:
    name = "yfinance"

    def history(self, symbol: str, start: date, end: date) -> pd.Series:
        import yfinance as yf

        df = yf.Ticker(symbol).history(
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),
            auto_adjust=True,
        )
        if df is None or df.empty:
            return pd.Series(dtype=float, name=symbol)
        idx = pd.to_datetime([ts.date() for ts in df.index])
        series = pd.Series(df["Close"].astype(float).to_numpy(), index=idx, name=symbol)
        return series.groupby(level=0).last().sort_index()
