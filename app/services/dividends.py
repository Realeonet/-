"""Дивиденды: прогноз (брутто → −25% → нетто), календарь X-day, фактически полученное."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session, joinedload

from app.db import Asset, Custodian, Dividend
from app.domain.money import HUNDRED, ZERO, D
from app.domain.returns import annual_dividend_per_unit, project_ex_dates
from app.domain.tax_il import DEFAULT_RATE, DividendTax, dividend_tax
from app.services.market import MarketData
from app.services.portfolio import Position


@dataclass(frozen=True)
class NextEx:
    day: date
    estimated: bool  # True — прогноз по истории, False — введено вручную
    amount_per_unit: Decimal | None  # ожидаемая выплата на единицу (последняя известная)
    pay_date: date | None


@dataclass(frozen=True)
class DividendForecast:
    asset_id: int
    symbol: str
    custodian: str
    owner: str
    qty: Decimal
    currency: str
    per_unit_year: Decimal
    basis: str  # откуда взята оценка
    gross_year_ils: Decimal
    gross_year_usd: Decimal
    gross_month_ils: Decimal  # выплаты, ожидаемые в ближайшие 30 дней
    gross_month_usd: Decimal
    next_ex: NextEx | None


def upcoming_ex_dates(asset: Asset, market: MarketData, today: date, horizon_days: int = 365) -> list[NextEx]:
    """Будущие ex-даты: ручная (если задана и не прошла) + прогноз по истории выплат."""
    divs = market.dividends(asset)
    last_amount = D(float(divs.iloc[-1])) if len(divs) else None
    out: list[NextEx] = []
    if asset.next_ex_date and asset.next_ex_date >= today:
        out.append(NextEx(asset.next_ex_date, False, last_amount, asset.next_pay_date))
    history = [ts.date() for ts in divs.index]
    for day in project_ex_dates(history, today, horizon_days):
        if out and abs((day - out[0].day).days) <= 10:
            continue  # ручная дата уже покрывает эту выплату
        out.append(NextEx(day, True, last_amount, None))
    return sorted(out, key=lambda x: x.day)


def forecast(
    assets: dict[int, Asset], positions: Iterable[Position], market: MarketData, today: date
) -> list[DividendForecast]:
    """Прогноз по каждой паре (актив, место хранения). Только для выплачивающих активов."""
    usd_ils = market.usd_ils(today)
    grouped: dict[tuple[int, int], list[Position]] = {}
    for p in positions:
        grouped.setdefault((p.asset_id, p.custodian_id), []).append(p)
    out: list[DividendForecast] = []
    for (asset_id, _cust_id), group in grouped.items():
        asset = assets[asset_id]
        if asset.dist_type == "acc" or asset.asset_class not in ("stock", "etf"):
            continue
        qty = sum((p.qty for p in group), ZERO)
        info = market.price_info(asset)
        currency = info.currency if info else asset.currency
        fx = market.fx(currency, today)
        if fx is None or not usd_ils:
            continue
        per_unit = annual_dividend_per_unit(market.dividends(asset), today)
        basis = "выплаты за последние 12 месяцев"
        if per_unit == 0 and asset.manual_yield_pct and info is not None:
            per_unit = info.price * asset.manual_yield_pct / HUNDRED
            basis = "ручная доходность"
        if per_unit == 0:
            continue
        ex_list = upcoming_ex_dates(asset, market, today, 365)
        month_units = sum(
            (e.amount_per_unit or ZERO for e in ex_list if e.day <= today + timedelta(days=30)), ZERO
        )
        gross_year_ils = qty * per_unit * fx
        gross_month_ils = qty * month_units * fx
        out.append(
            DividendForecast(
                asset_id=asset.id,
                symbol=asset.symbol,
                custodian=group[0].custodian,
                owner=group[0].owner,
                qty=qty,
                currency=currency,
                per_unit_year=per_unit,
                basis=basis,
                gross_year_ils=gross_year_ils,
                gross_year_usd=gross_year_ils / usd_ils,
                gross_month_ils=gross_month_ils,
                gross_month_usd=gross_month_ils / usd_ils,
                next_ex=ex_list[0] if ex_list else None,
            )
        )
    return sorted(out, key=lambda f: (-f.gross_year_ils, f.symbol))


@dataclass(frozen=True)
class ReceivedDividend:
    row: Dividend
    symbol: str
    custodian: str
    owner: str
    tax: DividendTax  # в валюте выплаты
    gross_ils: Decimal
    to_pay_ils: Decimal
    net_ils: Decimal
    status: str  # удержан / декларировать / частично


def received(s: Session, rate: Decimal = DEFAULT_RATE, year: int | None = None) -> list[ReceivedDividend]:
    rows = (
        s.query(Dividend)
        .options(joinedload(Dividend.asset), joinedload(Dividend.custodian).joinedload(Custodian.owner))
        .order_by(Dividend.pay_date.desc(), Dividend.id.desc())
        .all()
    )
    out = []
    for r in rows:
        if year is not None and r.pay_date.year != year:
            continue
        tax = dividend_tax(r.gross, r.foreign_tax, r.il_tax_withheld, rate)
        if tax.to_pay == 0:
            status = "налог удержан полностью"
        elif r.foreign_tax + r.il_tax_withheld > 0:
            status = "удержан частично — доплатить и задекларировать"
        else:
            status = "не удержан — декларировать самому"
        out.append(
            ReceivedDividend(
                row=r,
                symbol=r.asset.symbol,
                custodian=r.custodian.name,
                owner=r.custodian.owner.name,
                tax=tax,
                gross_ils=r.gross * r.fx_ccy_ils,
                to_pay_ils=tax.to_pay * r.fx_ccy_ils,
                net_ils=tax.net * r.fx_ccy_ils,
                status=status,
            )
        )
    return out
