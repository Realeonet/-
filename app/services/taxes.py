"""Реализованная прибыль и налог по продажам/обменам. Ориентировочно — сверять с консультантом."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session, joinedload

from app.db import Custodian, Disposal, Lot
from app.domain.lots import LotState
from app.domain.tax_il import AnnualSummary, Gain, annual_summary, compute_gain


@dataclass(frozen=True)
class RealizedRow:
    disposal: Disposal
    symbol: str
    asset_class: str
    custodian: str
    owner: str
    buy_date: object
    gain: Gain
    withheld_ils: Decimal
    status: str


def _historic_state(lot: Lot) -> LotState:
    # для уже совершённых продаж остаток не ограничивает расчёт — берём исходное количество
    return LotState(lot.id, lot.buy_date, lot.qty, lot.qty, lot.price, lot.fees, lot.currency,
                    lot.fx_ccy_ils, lot.fx_usd_ils)


def realized(s: Session, rate: Decimal, method: str, year: int | None = None) -> list[RealizedRow]:
    rows = (
        s.query(Disposal)
        .options(
            joinedload(Disposal.lot).joinedload(Lot.asset),
            joinedload(Disposal.lot).joinedload(Lot.custodian).joinedload(Custodian.owner),
        )
        .order_by(Disposal.date.desc(), Disposal.id.desc())
        .all()
    )
    out = []
    for d in rows:
        if year is not None and d.date.year != year:
            continue
        lot = d.lot
        gain = compute_gain(
            _historic_state(lot), d.qty, d.price, d.fee, d.currency, d.fx_ccy_ils, d.fx_usd_ils, rate, method
        )
        cust = lot.custodian
        if d.il_tax_withheld > 0 or cust.withholds_il_tax:
            status = "налог удерживает брокер"
        elif gain.taxable_ils > 0:
            status = "декларировать самому"
        else:
            status = "убыток — заявить для зачёта"
        out.append(
            RealizedRow(
                disposal=d,
                symbol=lot.asset.symbol,
                asset_class=lot.asset.asset_class,
                custodian=cust.name,
                owner=cust.owner.name,
                buy_date=lot.buy_date,
                gain=gain,
                withheld_ils=d.il_tax_withheld,
                status=status,
            )
        )
    return out


def summary_by_owner(rows: list[RealizedRow], rate: Decimal) -> dict[str, AnnualSummary]:
    by_owner: dict[str, list[tuple[Decimal, Decimal]]] = {}
    for r in rows:
        by_owner.setdefault(r.owner, []).append((r.gain.taxable_ils, r.withheld_ils))
    return {owner: annual_summary(items, rate) for owner, items in sorted(by_owner.items())}


def years_with_activity(s: Session) -> list[int]:
    years = {d.year for (d,) in s.query(Disposal.date).all()}
    return sorted(years, reverse=True)
