"""Расчёт пенсионных счетов из локального кэша (общий для сводки капитала и страницы пенсии)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pandas as pd
from sqlalchemy.orm import joinedload, selectinload

from app.db import PensionAccount
from app.domain.money import ZERO
from app.services.market import MarketData
from app.services.pension import AccountView, MissingProxyError, build_view, load_closes, official_points
from app.ui.common import Session, runtime


def load_accounts() -> list[PensionAccount]:
    with Session() as s:
        return (
            s.query(PensionAccount)
            .options(joinedload(PensionAccount.owner), selectinload(PensionAccount.official),
                     selectinload(PensionAccount.balance_points))
            .order_by(PensionAccount.id)
            .all()
        )


def make_fetch(m: MarketData):
    def fetch(symbol: str, start: date, end: date) -> pd.Series:
        if symbol.startswith("BOI:"):
            series = m.fx_series(symbol[4:])
            return series[(series.index >= pd.Timestamp(start)) & (series.index <= pd.Timestamp(end))]
        return m.history(f"yfinance:{symbol}", start, end)

    return fetch


def compute(acc: PensionAccount, m: MarketData, today: date) -> tuple[AccountView | None, str | None]:
    """(оценка, текст ошибки). Читает только кэш — в сеть не ходит."""
    config = runtime()[2]
    points = official_points(acc)
    starts = [acc.balance_date, date(today.year, 1, 1)] + [date.fromisoformat(f"{p.period}-01") for p in points]
    start = min(starts) - timedelta(days=10)
    weights_list = [acc.weights()] + [p.composition for p in points if p.composition is not None]
    try:
        closes = load_closes(config, acc.track_key, weights_list, start, today, make_fetch(m))
        view = build_view(
            acc.balance_ils, acc.balance_date, acc.weights(), acc.fx_hedge_pct, closes, today, points,
            weights_as_of=acc.weights_as_of, manual_track=config.tracks[acc.track_key].preset is None,
        )
        return view, None
    except MissingProxyError as exc:
        return None, str(exc)
    except ValueError as exc:
        return None, str(exc)


def totals_by_owner(m: MarketData, today: date) -> dict[str, Decimal]:
    """Оценённый баланс пенсионных счетов по владельцам, ₪ (если оценки нет — последний введённый баланс)."""
    out: dict[str, Decimal] = {}
    for acc in load_accounts():
        view, _error = compute(acc, m, today)
        value = view.balance_today if view else acc.balance_ils
        out[acc.owner.name] = out.get(acc.owner.name, ZERO) + value
    return out
