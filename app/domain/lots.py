"""Лоты: себестоимость, подбор FIFO, ручной выбор, разбиение лота при переводе.

Чистые функции над Decimal — без базы данных.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.domain.money import ZERO


@dataclass(frozen=True)
class LotState:
    id: int
    buy_date: date
    qty: Decimal  # исходное количество лота
    remaining: Decimal
    price: Decimal  # за единицу, в валюте лота
    fees: Decimal  # доп. затраты на весь лот (комиссия, НДС), в валюте лота
    currency: str
    fx_ccy_ils: Decimal  # ₪ за единицу валюты лота на дату покупки
    fx_usd_ils: Decimal  # ₪ за $1 на дату покупки


class InsufficientQuantity(ValueError):
    pass


def cost_of(lot: LotState, qty: Decimal) -> Decimal:
    """Себестоимость qty единиц лота в валюте лота: цена + пропорциональная доля доп. затрат."""
    if qty < 0:
        raise ValueError("Количество не может быть отрицательным")
    if lot.qty == 0:
        return ZERO
    return qty * lot.price + lot.fees * qty / lot.qty


def unit_cost(lot: LotState) -> Decimal:
    return cost_of(lot, lot.qty) / lot.qty if lot.qty else ZERO


def fifo_allocate(lots: Iterable[LotState], qty: Decimal) -> list[tuple[LotState, Decimal]]:
    """FIFO: самые старые лоты первыми (при равной дате — по id)."""
    if qty <= 0:
        raise ValueError("Количество должно быть больше нуля")
    left = qty
    out: list[tuple[LotState, Decimal]] = []
    for lot in sorted(lots, key=lambda x: (x.buy_date, x.id)):
        if lot.remaining <= 0:
            continue
        take = min(lot.remaining, left)
        out.append((lot, take))
        left -= take
        if left == 0:
            break
    if left > 0:
        raise InsufficientQuantity(f"Не хватает количества: недостаёт {left}")
    return out


def manual_allocate(
    lots: Sequence[LotState], picks: Mapping[int, Decimal]
) -> list[tuple[LotState, Decimal]]:
    """Ручной выбор: {id лота: количество}. Проверяет остатки."""
    by_id = {lot.id: lot for lot in lots}
    out: list[tuple[LotState, Decimal]] = []
    for lot_id, q in picks.items():
        if q == 0:
            continue
        if q < 0:
            raise ValueError("Количество не может быть отрицательным")
        lot = by_id.get(lot_id)
        if lot is None:
            raise ValueError(f"Лот {lot_id} не найден")
        if q > lot.remaining:
            raise InsufficientQuantity(f"В лоте {lot_id} осталось {lot.remaining}, запрошено {q}")
        out.append((lot, q))
    if not out:
        raise ValueError("Не выбрано ни одного лота")
    return sorted(out, key=lambda x: (x[0].buy_date, x[0].id))


@dataclass(frozen=True)
class TransferSplit:
    """Результат разбиения лота при переводе."""

    source_qty: Decimal
    source_remaining: Decimal
    source_fees: Decimal
    dest_qty: Decimal
    dest_price: Decimal
    dest_fees: Decimal
    fees_moved: Decimal  # доля исходных доп. затрат, ушедшая с переведённой частью


def split_for_transfer(
    lot: LotState,
    qty_sent: Decimal,
    qty_received: Decimal | None = None,
    extra_fee_ccy: Decimal = ZERO,
) -> TransferSplit:
    """Перевод части лота в другое место хранения.

    Дата и цена покупки сохраняются. Полная себестоимость переведённой части не теряется:
    если получено меньше, чем отправлено (комиссия сети в самом активе), или уплачена денежная
    комиссия (extra_fee_ccy, уже в валюте лота) — разница попадает в доп. затраты нового лота.
    """
    if qty_received is None:
        qty_received = qty_sent
    if qty_sent <= 0 or qty_received <= 0:
        raise ValueError("Количество должно быть больше нуля")
    if qty_received > qty_sent:
        raise ValueError("Получено не может быть больше, чем отправлено")
    if qty_sent > lot.remaining:
        raise InsufficientQuantity(f"В лоте {lot.id} осталось {lot.remaining}, запрошено {qty_sent}")
    if extra_fee_ccy < 0:
        raise ValueError("Комиссия не может быть отрицательной")

    fees_moved = lot.fees * qty_sent / lot.qty
    total_cost = qty_sent * lot.price + fees_moved + extra_fee_ccy
    return TransferSplit(
        source_qty=lot.qty - qty_sent,
        source_remaining=lot.remaining - qty_sent,
        source_fees=lot.fees - fees_moved,
        dest_qty=qty_received,
        dest_price=lot.price,
        dest_fees=total_cost - qty_received * lot.price,
        fees_moved=fees_moved,
    )
