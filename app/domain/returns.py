"""Доходность и сценарии: total return, CAGR, yield, DCA (бэктест и прогноз), «если цена станет X».

Ряды цен — pandas.Series (float, индекс — даты). Денежные итоги — Decimal.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

import pandas as pd

from app.domain.money import HUNDRED, ONE, ZERO, D


def _on_or_before(series: pd.Series, day: date) -> float | None:
    part = series[series.index <= pd.Timestamp(day)].dropna()
    return float(part.iloc[-1]) if len(part) else None


def total_return(series: pd.Series, years: int, as_of: date | None = None) -> Decimal | None:
    """Полная доходность за N лет по ряду скорректированных цен (дивиденды реинвестированы).
    None — если истории не хватает (допуск 10 дней)."""
    clean = series.dropna()
    if clean.empty:
        return None
    end_day = as_of or clean.index[-1].date()
    start_day = date(end_day.year - years, end_day.month, min(end_day.day, 28))
    if clean.index[0].date() > start_day + timedelta(days=10):
        return None
    start = _on_or_before(clean, start_day)
    if start is None:
        start = float(clean.iloc[0])
    end = _on_or_before(clean, end_day)
    if end is None or start <= 0:
        return None
    return D(end) / D(start) - ONE


def cagr(total: Decimal | None, years: int) -> Decimal | None:
    if total is None or years <= 0 or total <= -1:
        return None
    return D(float(ONE + total) ** (1 / years)) - ONE


def trailing_yield(dividends: pd.Series, price: Decimal, as_of: date) -> Decimal | None:
    """Доходность по выплатам за последние 365 дней к текущей цене."""
    if price <= 0:
        return None
    if dividends is None or dividends.empty:
        return ZERO
    start = pd.Timestamp(as_of - timedelta(days=365))
    window = dividends[(dividends.index > start) & (dividends.index <= pd.Timestamp(as_of))]
    return D(float(window.sum())) / price


def annual_dividend_per_unit(dividends: pd.Series, as_of: date) -> Decimal:
    if dividends is None or dividends.empty:
        return ZERO
    start = pd.Timestamp(as_of - timedelta(days=365))
    window = dividends[(dividends.index > start) & (dividends.index <= pd.Timestamp(as_of))]
    return D(float(window.sum()))


def project_ex_dates(ex_dates: list[date], today: date, horizon_days: int = 365) -> list[date]:
    """Прогноз будущих ex-дат по истории: последняя дата + типичный интервал (медиана)."""
    dates = sorted(set(ex_dates))
    if len(dates) < 2:
        return []
    tail = dates[-7:]
    gaps = sorted((b - a).days for a, b in zip(tail, tail[1:]))
    step = gaps[len(gaps) // 2]
    if step <= 0:
        return []
    out = []
    nxt = dates[-1] + timedelta(days=step)
    while nxt <= today + timedelta(days=horizon_days):
        if nxt >= today:
            out.append(nxt)
        nxt += timedelta(days=step)
    return out


# ---------- DCA ----------


@dataclass(frozen=True)
class DcaPoint:
    day: date
    price: Decimal
    amount: Decimal
    invested: Decimal
    units: Decimal
    value: Decimal


@dataclass(frozen=True)
class DcaResult:
    points: list[DcaPoint]
    invested: Decimal
    units: Decimal
    value: Decimal
    gain: Decimal

    @property
    def gain_pct(self) -> Decimal | None:
        return self.gain / self.invested if self.invested > 0 else None


def monthly_first_days(series: pd.Series, start: date, end: date) -> list[tuple[date, Decimal]]:
    """Первый торговый день каждого месяца в диапазоне."""
    clean = series.dropna()
    clean = clean[(clean.index >= pd.Timestamp(start)) & (clean.index <= pd.Timestamp(end))]
    out: list[tuple[date, Decimal]] = []
    seen: set[tuple[int, int]] = set()
    for ts, price in clean.items():
        key = (ts.year, ts.month)
        if key not in seen:
            seen.add(key)
            out.append((ts.date(), D(float(price))))
    return out


def dca_backtest(
    series: pd.Series,
    start: date,
    end: date,
    amount: Decimal | Callable[[date, Decimal], Decimal],
    rule_series: pd.Series | None = None,
) -> DcaResult:
    """Ежемесячная покупка в первый торговый день месяца на исторических ценах.

    amount — фиксированная сумма или функция (дата, цена-ориентир) → сумма.
    rule_series — ряд цены-ориентира для правила (например BTC), если он отличается от покупаемого актива.
    Итог оценивается по последней цене диапазона.
    """
    buys = monthly_first_days(series, start, end)
    points: list[DcaPoint] = []
    invested = units = ZERO
    for day, price in buys:
        if price <= 0:
            continue
        if callable(amount):
            ref = price
            if rule_series is not None:
                ref_val = _on_or_before(rule_series, day)
                ref = D(ref_val) if ref_val is not None else price
            amt = amount(day, ref)
        else:
            amt = amount
        invested += amt
        units += amt / price
        points.append(DcaPoint(day, price, amt, invested, units, units * price))
    last = _on_or_before(series, end)
    value = units * D(last) if last is not None else ZERO
    return DcaResult(points=points, invested=invested, units=units, value=value, gain=value - invested)


def dca_projection(
    monthly_amount: Decimal, months: int, annual_growth_pct: Decimal, start_value: Decimal = ZERO
) -> list[tuple[int, Decimal, Decimal]]:
    """Прогноз: (месяц, вложено, стоимость) при постоянном росте в % годовых.
    Взнос делается в начале месяца, затем начисляется рост за месяц."""
    if months < 0:
        raise ValueError("Период не может быть отрицательным")
    monthly_rate = D((1 + float(annual_growth_pct) / 100) ** (1 / 12)) - ONE
    value = start_value
    invested = start_value
    out = []
    for m in range(1, months + 1):
        value = (value + monthly_amount) * (ONE + monthly_rate)
        invested += monthly_amount
        out.append((m, invested, value))
    return out


# ---------- «если цена станет X» ----------


@dataclass(frozen=True)
class WhatIf:
    value: Decimal
    cost: Decimal
    gain: Decimal
    tax: Decimal
    net: Decimal


def what_if(qty: Decimal, cost: Decimal, target_price: Decimal, tax_rate: Decimal) -> WhatIf:
    """Упрощённая оценка в одной валюте (без курсовой разницы): стоимость, прибыль, налог, на руки."""
    value = qty * target_price
    gain = value - cost
    tax = gain * tax_rate if gain > 0 else ZERO
    return WhatIf(value=value, cost=cost, gain=gain, tax=tax, net=value - tax)


def pct(value: Decimal) -> Decimal:
    return value * HUNDRED
