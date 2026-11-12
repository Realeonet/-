"""Yahoo Finance через yfinance: акции, ETF, индексы, фьючерсы, длинная история крипты.

Неофициальный источник: может меняться без предупреждения. Тикеры Лондона — с суффиксом .L
(валюта может быть GBp — пенсы), Тель-Авива — .TA (валюта ILA — агороты).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

import pandas as pd

from app.adapters.base import LiveQuote, SourceError
from app.domain.money import from_market_float

logging.getLogger("yfinance").setLevel(logging.CRITICAL)


def _ticker(symbol: str):
    import yfinance as yf

    return yf.Ticker(symbol)


def _clean(series: pd.Series) -> pd.Series:
    series = series.dropna()
    if series.empty:
        return pd.Series(dtype=float)
    idx = pd.to_datetime([ts.date() for ts in series.index])
    out = pd.Series(series.astype(float).to_numpy(), index=idx)
    return out.groupby(level=0).last().sort_index()


class YahooSource:
    name = "yfinance"

    def history(self, symbol: str, start: date, end: date, adjusted: bool = False) -> pd.Series:
        try:
            df = _ticker(symbol).history(
                start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(), auto_adjust=adjusted
            )
        except Exception as exc:  # noqa: BLE001 — yfinance бросает разнородные исключения
            raise SourceError(f"Yahoo: не удалось получить историю {symbol}: {exc}") from exc
        if df is None or df.empty or "Close" not in df:
            return pd.Series(dtype=float)
        return _clean(df["Close"])

    def dividends(self, symbol: str) -> pd.Series:
        try:
            divs = _ticker(symbol).dividends
        except Exception as exc:  # noqa: BLE001
            raise SourceError(f"Yahoo: не удалось получить дивиденды {symbol}: {exc}") from exc
        if divs is None or len(divs) == 0:
            return pd.Series(dtype=float)
        return _clean(divs)

    def quote(self, symbol: str) -> LiveQuote:
        """Последняя цена и валюта котировки."""
        try:
            t = _ticker(symbol)
            df = t.history(period="7d", auto_adjust=False)
            closes = _clean(df["Close"]) if df is not None and not df.empty else pd.Series(dtype=float)
            if closes.empty:
                raise SourceError(f"Yahoo: нет котировок для {symbol} — проверьте тикер")
            currency = ""
            try:
                currency = t.fast_info.get("currency") or ""
            except Exception:  # noqa: BLE001
                currency = ""
        except SourceError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SourceError(f"Yahoo: не удалось получить цену {symbol}: {exc}") from exc
        last_day = closes.index[-1].date()
        return LiveQuote(
            price=from_market_float(closes.iloc[-1]),
            currency=currency,
            as_of=datetime.combine(last_day, datetime.min.time()),
            source="Yahoo Finance",
        )

    def profile(self, symbol: str) -> dict:
        """Справочные данные для автозаполнения карточки актива. Любое поле может отсутствовать."""
        try:
            info = _ticker(symbol).info or {}
        except Exception as exc:  # noqa: BLE001
            raise SourceError(f"Yahoo: нет данных по {symbol}: {exc}") from exc
        if not info.get("quoteType"):
            raise SourceError(f"Yahoo: тикер {symbol} не найден")
        name = info.get("longName") or info.get("shortName") or ""
        expense = info.get("netExpenseRatio")
        lowered = name.lower()
        dist = "na"
        if "(acc)" in lowered or " acc" in lowered or "accumulating" in lowered:
            dist = "acc"
        elif "(dist)" in lowered or " dist" in lowered or "distributing" in lowered:
            dist = "dist"
        return {
            "name": name,
            "quote_type": info.get("quoteType"),
            "currency": info.get("currency") or "",
            "expense_ratio_pct": from_market_float(expense) if expense is not None else None,
            "dist_type": dist,
            "exchange": info.get("exchange") or "",
        }
