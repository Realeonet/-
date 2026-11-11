"""Оценка пенсионных счетов: дневные изменения по прокси, оценка баланса, сверка с официальными данными.

Все результаты помечаются как ОЦЕНКА, а не официальная доходность фонда.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

import pandas as pd

from app.config import PensionConfig
from app.domain.pension import (
    CLASSES,
    FOREIGN_CLASSES,
    HUNDRED,
    ONE,
    ZERO,
    class_returns_ils,
    compound,
    daily_change,
    estimate_balance,
    hedge_fraction,
    validate_weights,
    weights_stale,
)

# Классы, для которых отсутствие котировки = доходность 0 (с предупреждением).
ZERO_IF_MISSING = frozenset({"israel_bonds"})
ESTIMATE_LABEL = "ОЦЕНКА, не официальная доходность фонда"


class MissingProxyError(Exception):
    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(f"Нет котировок для «{key}». Проверьте тикер в config/pension.toml")


@dataclass(frozen=True)
class AccountInputs:
    balance: Decimal
    balance_date: date
    weights: Mapping[str, Decimal]
    hedge_pct: Decimal
    weights_as_of: date | None = None
    manual_track: bool = False


@dataclass(frozen=True)
class OfficialPoint:
    period: str  # YYYY-MM
    return_pct: Decimal  # как в файле, в процентах
    composition: Mapping[str, Decimal] | None = None


@dataclass(frozen=True)
class Comparison:
    period: str
    official: Decimal  # доля
    estimate: Decimal  # доля
    diff_pp: Decimal  # процентные пункты: (оценка − официальная) × 100
    used_composition: bool


@dataclass
class AccountView:
    today_change: Decimal | None
    today_change_ils: Decimal | None
    balance_today: Decimal
    month_return: Decimal | None
    ytd_return: Decimal | None
    path: list[tuple[date, Decimal]]
    exposure_ils: dict[str, Decimal]
    comparisons: list[Comparison] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    label: str = ESTIMATE_LABEL


def required_keys(config: PensionConfig, track_key: str, weights: Mapping[str, Decimal]) -> dict[str, str]:
    """Какие тикеры нужны для расчёта: ключ (класс или 'usd_ils') → тикер."""
    proxies = config.proxies_for(track_key)
    keys: dict[str, str] = {}
    for c in CLASSES:
        if weights.get(c, ZERO) > 0 and c in proxies:
            keys[c] = proxies[c]
    if any(weights.get(c, ZERO) > 0 for c in FOREIGN_CLASSES):
        keys["usd_ils"] = config.usd_ils
    return keys


def fetch_closes(
    config: PensionConfig,
    track_key: str,
    weights: Mapping[str, Decimal],
    start: date,
    end: date,
    fetch: Callable[[str, date, date], pd.Series],
) -> dict[str, pd.Series]:
    return {
        key: fetch(symbol, start, end)
        for key, symbol in required_keys(config, track_key, weights).items()
    }


def _to_dec(value: float | int) -> Decimal:
    return Decimal(repr(float(value)))


def daily_changes(
    weights: Mapping[str, Decimal],
    closes: Mapping[str, pd.Series],
    hedge_pct: Decimal,
) -> list[tuple[date, Decimal]]:
    """Дневное изменение портфеля по дням: Σ(w_i × r_i), r_i в шекелях.

    closes: ключи — классы активов (цены в своей валюте: ILS для израильских, USD для зарубежных)
    и 'usd_ils' (шекелей за доллар).
    """
    validate_weights(weights)
    hedge = hedge_fraction(hedge_pct)
    active = [c for c in CLASSES if weights[c] > 0]

    series: dict[str, pd.Series] = {}
    for c in active:
        if c == "cash_other":
            continue
        s = closes.get(c)
        if s is None or len(s) == 0:
            if c in ZERO_IF_MISSING:
                continue
            raise MissingProxyError(c)
        series[c] = s
    if any(c in FOREIGN_CLASSES for c in active):
        fx = closes.get("usd_ils")
        if fx is None or len(fx) == 0:
            raise MissingProxyError("usd_ils")
        series["usd_ils"] = fx
    if not series:
        return []

    frame = pd.DataFrame(series).sort_index().ffill().dropna()
    rets = frame.pct_change().dropna()

    out: list[tuple[date, Decimal]] = []
    for ts, row in rets.iterrows():
        local = {c: _to_dec(row[c]) for c in ("israel_equity", "israel_bonds") if c in row.index}
        usd = {c: _to_dec(row[c]) for c in FOREIGN_CLASSES if c in row.index}
        r_fx = _to_dec(row["usd_ils"]) if "usd_ils" in row.index else ZERO
        returns = class_returns_ils(local, usd, r_fx, hedge)
        out.append((pd.Timestamp(ts).date(), daily_change(weights, returns)))
    return out


def estimate_path(
    balance: Decimal, balance_date: date, changes: Iterable[tuple[date, Decimal]]
) -> list[tuple[date, Decimal]]:
    """Накопленная оценка баланса для дат строго после balance_date."""
    path: list[tuple[date, Decimal]] = []
    current = Decimal(balance)
    for d, change in changes:
        if d <= balance_date:
            continue
        current = current * (ONE + change)
        path.append((d, current))
    return path


def _period_return(changes: Iterable[tuple[date, Decimal]], start_excl: date, end: date) -> Decimal | None:
    rets = [c for d, c in changes if start_excl < d <= end]
    return compound(rets) if rets else None


def _month_estimate(changes: Iterable[tuple[date, Decimal]], period: str) -> Decimal | None:
    rets = [c for d, c in changes if d.isoformat()[:7] == period]
    return compound(rets) if rets else None


def build_view(
    inputs: AccountInputs,
    closes: Mapping[str, pd.Series],
    hedge_pct: Decimal,
    today: date,
    official: Iterable[OfficialPoint] = (),
) -> AccountView:
    validate_weights(inputs.weights)
    warnings: list[str] = []
    if inputs.manual_track and weights_stale(inputs.weights_as_of, today):
        warnings.append("Веса трека старше 45 дней или не обновлялись: обновите состав или импорт")
    if inputs.weights.get("israel_bonds", ZERO) > 0 and "israel_bonds" not in closes:
        warnings.append("Нет индекса израильских облигаций: их доходность учтена как 0 (оценка занижена)")

    changes = [(d, c) for d, c in daily_changes(inputs.weights, closes, hedge_pct) if d <= today]
    path = estimate_path(inputs.balance, inputs.balance_date, changes)
    balance_today = path[-1][1] if path else Decimal(inputs.balance)

    today_change = None
    today_change_ils = None
    if path and changes:
        last_date, last_change = changes[-1]
        if last_date > inputs.balance_date:
            today_change = last_change
            prev_balance = balance_today / (ONE + last_change)
            today_change_ils = prev_balance * last_change

    first_of_month = date(today.year, today.month, 1) - timedelta(days=1)
    first_of_year = date(today.year, 1, 1) - timedelta(days=1)
    month_return = _period_return(changes, max(first_of_month, inputs.balance_date), today)
    ytd_return = _period_return(changes, max(first_of_year, inputs.balance_date), today)

    all_changes = daily_changes(inputs.weights, closes, hedge_pct)
    comparisons: list[Comparison] = []
    for point in official:
        if point.composition is not None:
            validate_weights(point.composition)
            est = _month_estimate(daily_changes(point.composition, closes, hedge_pct), point.period)
        else:
            est = _month_estimate(all_changes, point.period)
        if est is None:
            continue
        off = Decimal(point.return_pct) / HUNDRED
        comparisons.append(
            Comparison(
                period=point.period,
                official=off,
                estimate=est,
                diff_pp=(est - off) * HUNDRED,
                used_composition=point.composition is not None,
            )
        )

    exposure = {c: balance_today * Decimal(inputs.weights[c]) / HUNDRED for c in CLASSES}
    return AccountView(
        today_change=today_change,
        today_change_ils=today_change_ils,
        balance_today=balance_today,
        month_return=month_return,
        ytd_return=ytd_return,
        path=path,
        exposure_ils=exposure,
        comparisons=comparisons,
        warnings=warnings,
    )


def format_money(value: Decimal | None) -> str:
    if value is None:
        return "—"
    return f"{value.quantize(Decimal('0.01')):,.2f}".replace(",", " ")


def format_pct(value: Decimal | None) -> str:
    if value is None:
        return "—"
    return f"{(value * HUNDRED).quantize(Decimal('0.01')):+.2f}%"
