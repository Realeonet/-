"""Пенсионные накопления: оценка баланса по дням через прокси-индексы и сверка с официальными данными.

Все результаты — ОЦЕНКА, а не официальная доходность фонда.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

import pandas as pd

from app.adapters.gov_il import OfficialRecord
from app.config import PensionConfig
from app.db import OfficialMonthly, PensionAccount
from app.domain.money import D
from app.domain.pension import (
    CLASSES,
    FOREIGN_CLASSES,
    HUNDRED,
    ONE,
    ZERO,
    class_returns_ils,
    compound,
    daily_change,
    hedge_fraction,
    hedge_from_exposures,
    validate_weights,
    weights_from_exposures,
    weights_stale,
)

# Классы, для которых отсутствие прокси = доходность 0 (с предупреждением).
ZERO_IF_MISSING = frozenset({"israel_bonds"})
ESTIMATE_LABEL = "ОЦЕНКА, не официальная доходность фонда"
FX_KEY = "usd_ils"

Fetch = Callable[[str, date, date], pd.Series]


class MissingProxyError(Exception):
    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(
            f"Нет котировок для «{key}». Обновите данные или проверьте тикер в config/pension.toml"
        )


@dataclass(frozen=True)
class OfficialPoint:
    period: str  # YYYY-MM
    return_pct: Decimal
    composition: Mapping[str, Decimal] | None = None
    hedge_pct: Decimal | None = None


@dataclass(frozen=True)
class Comparison:
    period: str
    official: Decimal  # доля
    estimate: Decimal  # доля
    diff_pp: Decimal  # (оценка − официальная), процентные пункты
    used_composition: bool


@dataclass
class AccountView:
    balance_today: Decimal
    last_day: date | None
    day_change: Decimal | None
    day_change_ils: Decimal | None
    month_return: Decimal | None
    ytd_return: Decimal | None
    path: list[tuple[date, Decimal]]
    exposure_ils: dict[str, Decimal]
    comparisons: list[Comparison] = field(default_factory=list)
    tracking_error_pp: Decimal | None = None
    warnings: list[str] = field(default_factory=list)
    label: str = ESTIMATE_LABEL


def required_symbols(config: PensionConfig, track_key: str, weights: Mapping[str, Decimal]) -> dict[str, str]:
    """Какие ряды нужны: ключ (класс или 'usd_ils') → тикер/код из конфига."""
    proxies = config.proxies_for(track_key)
    keys: dict[str, str] = {}
    for c in CLASSES:
        if weights.get(c, ZERO) > 0 and c in proxies:
            keys[c] = proxies[c]
    if any(weights.get(c, ZERO) > 0 for c in FOREIGN_CLASSES):
        keys[FX_KEY] = config.usd_ils
    return keys


def load_closes(
    config: PensionConfig, track_key: str, weights_list: Iterable[Mapping[str, Decimal]], start: date, end: date,
    fetch: Fetch,
) -> dict[str, pd.Series]:
    symbols: dict[str, str] = {}
    for weights in weights_list:
        symbols.update(required_symbols(config, track_key, weights))
    return {key: fetch(symbol, start, end) for key, symbol in symbols.items()}


def daily_changes(
    weights: Mapping[str, Decimal], closes: Mapping[str, pd.Series], hedge_pct: Decimal
) -> list[tuple[date, Decimal]]:
    """Дневное изменение портфеля: Σ(w_i × r_i), r_i в шекелях.

    closes: цены прокси в своей валюте (ILS для израильских классов, USD для зарубежных)
    и 'usd_ils' (шекелей за доллар).
    """
    validate_weights(weights)
    hedge = hedge_fraction(hedge_pct)
    active = [c for c in CLASSES if weights[c] > 0 and c != "cash_other"]

    series: dict[str, pd.Series] = {}
    for c in active:
        s = closes.get(c)
        if s is None or len(s) == 0:
            if c in ZERO_IF_MISSING:
                continue
            raise MissingProxyError(c)
        series[c] = s
    if any(c in FOREIGN_CLASSES for c in active):
        fx = closes.get(FX_KEY)
        if fx is None or len(fx) == 0:
            raise MissingProxyError("USD/ILS")
        series[FX_KEY] = fx
    if not series:
        return []

    frame = pd.DataFrame(series).sort_index().ffill().dropna()
    rets = frame.pct_change().dropna()
    out: list[tuple[date, Decimal]] = []
    for ts, row in rets.iterrows():
        local = {c: D(float(row[c])) for c in ("israel_equity", "israel_bonds") if c in row.index}
        usd = {c: D(float(row[c])) for c in FOREIGN_CLASSES if c in row.index}
        r_fx = D(float(row[FX_KEY])) if FX_KEY in row.index else ZERO
        out.append((pd.Timestamp(ts).date(), daily_change(weights, class_returns_ils(local, usd, r_fx, hedge))))
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


def _period_return(changes: list[tuple[date, Decimal]], after: date, until: date) -> Decimal | None:
    rets = [c for d, c in changes if after < d <= until]
    return compound(rets) if rets else None


def _month_estimate(changes: list[tuple[date, Decimal]], period: str) -> Decimal | None:
    rets = [c for d, c in changes if d.isoformat()[:7] == period]
    return compound(rets) if rets else None


def build_view(
    balance: Decimal,
    balance_date: date,
    weights: Mapping[str, Decimal],
    hedge_pct: Decimal,
    closes: Mapping[str, pd.Series],
    today: date,
    official: Iterable[OfficialPoint] = (),
    weights_as_of: date | None = None,
    manual_track: bool = False,
) -> AccountView:
    validate_weights(weights)
    warnings: list[str] = []
    if manual_track and weights_stale(weights_as_of, today):
        warnings.append("Веса трека старше 45 дней или не обновлялись — обновите состав (вручную или импортом)")
    bonds_series = closes.get("israel_bonds")
    if weights.get("israel_bonds", ZERO) > 0 and (bonds_series is None or len(bonds_series) == 0):
        warnings.append(
            "Нет индекса израильских облигаций: их доходность принята за 0, оценка по этой части неточна"
        )

    all_changes = [(d, c) for d, c in daily_changes(weights, closes, hedge_pct) if d <= today]
    path = estimate_path(balance, balance_date, all_changes)
    balance_today = path[-1][1] if path else Decimal(balance)

    day_change = day_change_ils = None
    if path:
        day_change = next(c for d, c in reversed(all_changes) if d == path[-1][0])
        prev = path[-2][1] if len(path) > 1 else Decimal(balance)
        day_change_ils = balance_today - prev

    month_start = date(today.year, today.month, 1) - timedelta(days=1)
    year_start = date(today.year, 1, 1) - timedelta(days=1)
    month_return = _period_return(all_changes, month_start, today)
    ytd_return = _period_return(all_changes, year_start, today)

    comparisons: list[Comparison] = []
    for point in official:
        if point.composition is not None:
            hedge = point.hedge_pct if point.hedge_pct is not None else hedge_pct
            est = _month_estimate(daily_changes(point.composition, closes, hedge), point.period)
        else:
            est = _month_estimate(all_changes, point.period)
        if est is None:
            continue
        off = Decimal(point.return_pct) / HUNDRED
        comparisons.append(Comparison(point.period, off, est, (est - off) * HUNDRED, point.composition is not None))

    tracking = None
    if len(comparisons) >= 2:
        diffs = [c.diff_pp for c in comparisons]
        mean = sum(diffs, ZERO) / len(diffs)
        variance = sum(((x - mean) ** 2 for x in diffs), ZERO) / (len(diffs) - 1)
        tracking = variance.sqrt()

    exposure = {c: balance_today * Decimal(weights[c]) / HUNDRED for c in CLASSES}
    return AccountView(
        balance_today=balance_today,
        last_day=path[-1][0] if path else None,
        day_change=day_change,
        day_change_ils=day_change_ils,
        month_return=month_return,
        ytd_return=ytd_return,
        path=path,
        exposure_ils=exposure,
        comparisons=comparisons,
        tracking_error_pp=tracking,
        warnings=warnings,
    )


def official_points(account: PensionAccount) -> list[OfficialPoint]:
    points = []
    for o in account.official:
        hedge = None
        if o.foreign_exposure_pct is not None and o.fx_exposure_pct is not None:
            hedge = hedge_from_exposures(o.foreign_exposure_pct, o.fx_exposure_pct)
        points.append(OfficialPoint(o.period, o.return_pct, o.composition(), hedge))
    return points


def apply_official_records(
    account: PensionAccount, records: Iterable[OfficialRecord], source: str, update_weights: bool
) -> tuple[int, bool]:
    """Сохраняет официальные месячные записи; при update_weights обновляет веса и хедж счёта
    по последнему месяцу, где есть экспозиции. Возвращает (сколько месяцев, обновлены ли веса)."""
    existing = {o.period: o for o in account.official}
    count = 0
    latest: OfficialRecord | None = None
    for rec in records:
        row = existing.get(rec.period)
        if row is None:
            row = OfficialMonthly(period=rec.period)
            account.official.append(row)
            existing[rec.period] = row
        row.return_pct = rec.return_pct
        row.source = source
        row.stock_exposure_pct = rec.stock_pct
        row.foreign_exposure_pct = rec.foreign_pct
        row.fx_exposure_pct = rec.fx_pct
        if rec.stock_pct is not None and rec.foreign_pct is not None:
            for c, v in weights_from_exposures(rec.stock_pct, rec.foreign_pct).items():
                setattr(row, f"w_{c}", v)
            if latest is None or rec.period > latest.period:
                latest = rec
        count += 1
    updated = False
    if update_weights and latest is not None:
        account.set_weights(weights_from_exposures(latest.stock_pct, latest.foreign_pct))
        if latest.fx_pct is not None:
            account.fx_hedge_pct = hedge_from_exposures(latest.foreign_pct, latest.fx_pct)
        y, m = map(int, latest.period.split("-"))
        next_month = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
        account.weights_as_of = next_month - timedelta(days=1)
        updated = True
    return count, updated
