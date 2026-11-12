"""Оценка портфеля: позиции по лотам, группировки, история стоимости."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

import pandas as pd
from sqlalchemy.orm import Session, joinedload

from app.db import ASSET_CLASSES, Asset, Custodian, Disposal, Lot
from app.domain.lots import cost_of
from app.domain.money import HUNDRED, ZERO, major_currency
from app.services.ledger import lot_state
from app.services.market import MarketData, PriceInfo


@dataclass(frozen=True)
class Position:
    """Открытый лот с текущей оценкой."""

    lot_id: int
    asset_id: int
    symbol: str
    asset_name: str
    asset_class: str
    custodian_id: int
    custodian: str
    custodian_kind: str
    self_custody: bool
    owner_id: int
    owner: str
    us_situs: bool
    buy_date: date
    qty: Decimal
    lot_currency: str
    unit_cost: Decimal  # в валюте лота, с долей комиссий
    price: PriceInfo | None
    value_ils: Decimal | None
    value_usd: Decimal | None
    cost_ils: Decimal
    cost_usd: Decimal

    @property
    def gain_ils(self) -> Decimal | None:
        return None if self.value_ils is None else self.value_ils - self.cost_ils

    @property
    def gain_usd(self) -> Decimal | None:
        return None if self.value_usd is None else self.value_usd - self.cost_usd

    @property
    def class_label(self) -> str:
        return ASSET_CLASSES.get(self.asset_class, self.asset_class)


def adjusted_price(asset: Asset, info: PriceInfo) -> Decimal:
    """Цена с поправкой актива (премия/дисконт к споту для металлов)."""
    return info.price * (HUNDRED + (asset.valuation_adj_pct or ZERO)) / HUNDRED


def load_positions(s: Session, market: MarketData, today: date | None = None) -> list[Position]:
    lots = (
        s.query(Lot)
        .options(joinedload(Lot.asset), joinedload(Lot.custodian).joinedload(Custodian.owner))
        .order_by(Lot.buy_date, Lot.id)
        .all()
    )
    usd_ils = market.usd_ils(today)
    price_cache: dict[int, PriceInfo | None] = {}
    out: list[Position] = []
    for lot in lots:
        if lot.qty_remaining <= 0:
            continue
        asset, cust = lot.asset, lot.custodian
        if asset.id not in price_cache:
            price_cache[asset.id] = market.price_info(asset)
        info = price_cache[asset.id]
        state = lot_state(lot)
        cost_ccy = cost_of(state, lot.qty_remaining)
        cost_ils = cost_ccy * lot.fx_ccy_ils
        value_ils = value_usd = None
        if info is not None and usd_ils:
            fx = market.fx(info.currency, today)
            if fx is not None:
                value_ils = lot.qty_remaining * adjusted_price(asset, info) * fx
                value_usd = value_ils / usd_ils
        out.append(
            Position(
                lot_id=lot.id,
                asset_id=asset.id,
                symbol=asset.symbol,
                asset_name=asset.name,
                asset_class=asset.asset_class,
                custodian_id=cust.id,
                custodian=cust.name,
                custodian_kind=cust.kind,
                self_custody=cust.is_self_custody,
                owner_id=cust.owner_id,
                owner=cust.owner.name,
                us_situs=asset.us_situs,
                buy_date=lot.buy_date,
                qty=lot.qty_remaining,
                lot_currency=lot.currency,
                unit_cost=cost_ccy / lot.qty_remaining,
                price=info,
                value_ils=value_ils,
                value_usd=value_usd,
                cost_ils=cost_ils,
                cost_usd=cost_ils / lot.fx_usd_ils,
            )
        )
    return out


@dataclass(frozen=True)
class Group:
    name: str
    value_ils: Decimal
    value_usd: Decimal
    cost_ils: Decimal
    cost_usd: Decimal
    qty: Decimal
    lots: int
    unpriced: int

    @property
    def gain_ils(self) -> Decimal:
        return self.value_ils - self.cost_ils

    @property
    def gain_usd(self) -> Decimal:
        return self.value_usd - self.cost_usd

    @property
    def gain_pct_usd(self) -> Decimal | None:
        return self.gain_usd / self.cost_usd if self.cost_usd > 0 else None

    @property
    def gain_pct_ils(self) -> Decimal | None:
        return self.gain_ils / self.cost_ils if self.cost_ils > 0 else None


def group_positions(positions: Iterable[Position], key: Callable[[Position], str]) -> list[Group]:
    """Группировка с итогами. Лоты без цены в стоимость не входят и считаются в unpriced."""
    acc: dict[str, list] = {}
    for p in positions:
        row = acc.setdefault(key(p), [ZERO, ZERO, ZERO, ZERO, ZERO, 0, 0])
        row[5] += 1
        row[4] += p.qty
        if p.value_ils is None or p.value_usd is None:
            row[6] += 1
            continue
        row[0] += p.value_ils
        row[1] += p.value_usd
        row[2] += p.cost_ils
        row[3] += p.cost_usd
    groups = [Group(name, *vals) for name, vals in acc.items()]
    return sorted(groups, key=lambda g: (-g.value_ils, g.name))


def totals(positions: Iterable[Position]) -> Group:
    groups = group_positions(positions, lambda _p: "Итого")
    return groups[0] if groups else Group("Итого", ZERO, ZERO, ZERO, ZERO, ZERO, 0, 0)


def us_situs_by_owner(positions: Iterable[Position]) -> dict[str, dict[str, Decimal]]:
    """{владелец: {место хранения: стоимость US-situs в USD}}"""
    out: dict[str, dict[str, Decimal]] = {}
    for p in positions:
        if p.us_situs and p.value_usd is not None:
            by_cust = out.setdefault(p.owner, {})
            by_cust[p.custodian] = by_cust.get(p.custodian, ZERO) + p.value_usd
    return out


def value_history(s: Session, market: MarketData, start: date, end: date) -> pd.DataFrame:
    """Стоимость портфеля по дням (₪ и $), восстановленная по лотам и истории цен.

    Колонки: по одной на класс активов (₪), плюс total_ils и total_usd.
    Допущения: до первой известной цены берётся первая известная; ручные цены считаются постоянными.
    """
    days = pd.date_range(start, end, freq="D")
    if len(days) == 0:
        return pd.DataFrame()
    lots = s.query(Lot).options(joinedload(Lot.asset)).all()
    disposals = s.query(Disposal).all()
    sold_by_lot: dict[int, list[Disposal]] = {}
    for d in disposals:
        sold_by_lot.setdefault(d.lot_id, []).append(d)

    qty_by_asset: dict[int, pd.Series] = {}
    assets: dict[int, Asset] = {}
    for lot in lots:
        assets[lot.asset_id] = lot.asset
        series = qty_by_asset.setdefault(lot.asset_id, pd.Series(0.0, index=days))
        series[days >= pd.Timestamp(lot.buy_date)] += float(lot.qty)
        for d in sold_by_lot.get(lot.id, []):
            series[days >= pd.Timestamp(d.date)] -= float(d.qty)

    usd = market.fx_series("USD").reindex(days, method="ffill").bfill()
    frame = pd.DataFrame(index=days)
    for asset_id, qty in qty_by_asset.items():
        asset = assets[asset_id]
        info = market.price_info(asset)
        if asset.price_source == "cash":
            prices = pd.Series(1.0, index=days)
        else:
            hist = market.asset_history(asset, start - timedelta(days=10))
            if hist.empty:
                if info is None:
                    continue
                prices = pd.Series(float(info.price), index=days)
            else:
                prices = hist.reindex(days.union(hist.index)).ffill().bfill().reindex(days)
        currency = info.currency if info is not None else asset.currency
        major, factor = major_currency(currency)
        if major == "ILS":
            fx = pd.Series(float(factor), index=days)
        else:
            fx_raw = market.fx_series(currency)
            if fx_raw.empty:
                continue
            fx = fx_raw.reindex(days, method="ffill").bfill()
        adj = float((HUNDRED + (asset.valuation_adj_pct or ZERO)) / HUNDRED)
        value = qty.clip(lower=0) * prices * adj * fx
        label = ASSET_CLASSES.get(asset.asset_class, asset.asset_class)
        frame[label] = frame.get(label, 0.0) + value
    if frame.empty:
        return frame
    frame = frame.fillna(0.0)
    frame["total_ils"] = frame.sum(axis=1)
    frame["total_usd"] = frame["total_ils"] / usd if not usd.empty else float("nan")
    return frame
