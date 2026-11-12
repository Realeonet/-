"""Операции с лотами: покупка, продажа (FIFO или вручную), перевод между местами, обмен (swap).

Функции меняют объекты в переданной сессии; фиксацию (commit) делает вызывающий код,
поэтому любая операция либо проходит целиком, либо не оставляет следов.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import date
from decimal import ROUND_DOWN, Decimal

from sqlalchemy.orm import Session

from app.db import Asset, Custodian, Disposal, Dividend, Lot, Transfer, TransferPart
from app.domain.lots import LotState, fifo_allocate, manual_allocate, split_for_transfer
from app.domain.money import ZERO
from app.services.market import MarketData


QTY_STEP = Decimal("1E-18")  # шаг дробления количества между лотами (точнее любой монеты)
MAX_FX_AGE_DAYS = 10  # курс БИ не публикуется в выходные и праздники; больший разрыв — признак пробела в данных


class LedgerError(ValueError):
    pass


def lot_state(lot: Lot) -> LotState:
    return LotState(
        id=lot.id,
        buy_date=lot.buy_date,
        qty=lot.qty,
        remaining=lot.qty_remaining,
        price=lot.price,
        fees=lot.fees,
        currency=lot.currency,
        fx_ccy_ils=lot.fx_ccy_ils,
        fx_usd_ils=lot.fx_usd_ils,
    )


def resolve_fx(
    market: MarketData,
    currency: str,
    day: date,
    fx_ccy_ils: Decimal | None = None,
    fx_usd_ils: Decimal | None = None,
) -> tuple[Decimal, Decimal]:
    """Курсы на дату: (₪ за единицу валюты, ₪ за $1). Ручные значения имеют приоритет."""
    ccy = fx_ccy_ils if fx_ccy_ils is not None else market.fx(currency, day)
    usd = fx_usd_ils if fx_usd_ils is not None else market.usd_ils(day)
    if ccy is None or usd is None:
        raise LedgerError(
            f"Нет курса Банка Израиля на {day:%d.%m.%Y} для {currency}. "
            "Обновите курсы (кнопка «Обновить данные») или введите курс вручную."
        )
    for code, given in ((currency, fx_ccy_ils), ("USD", fx_usd_ils)):
        if given is not None:
            continue
        rate_day = market.fx_date(code, day)
        if rate_day is not None and (day - rate_day).days > MAX_FX_AGE_DAYS:
            raise LedgerError(
                f"Ближайший известный курс {code} — от {rate_day:%d.%m.%Y}, это слишком далеко от "
                f"{day:%d.%m.%Y}. Обновите курсы или введите курс на эту дату вручную "
                "(«Настройки → Курсы и данные»)."
            )
    if ccy <= 0 or usd <= 0:
        raise LedgerError("Курс валюты должен быть больше нуля")
    return ccy, usd


def add_lot(
    s: Session,
    market: MarketData,
    asset: Asset,
    custodian: Custodian,
    buy_date: date,
    qty: Decimal,
    price: Decimal,
    fees: Decimal = ZERO,
    vat: Decimal = ZERO,
    currency: str | None = None,
    fx_ccy_ils: Decimal | None = None,
    fx_usd_ils: Decimal | None = None,
    kind: str = "buy",
    purity: Decimal | None = None,
    form: str | None = None,
    gross_weight_g: Decimal | None = None,
    note: str | None = None,
    today: date | None = None,
) -> Lot:
    """Новый лот. fees — все доп. затраты на лот (комиссия + НДС); vat — справочно, часть fees."""
    if qty <= 0:
        raise LedgerError("Количество должно быть больше нуля")
    if price < 0 or fees < 0 or vat < 0:
        raise LedgerError("Цена и комиссии не могут быть отрицательными")
    if vat > fees:
        raise LedgerError("НДС не может быть больше суммы доп. затрат")
    if buy_date > (today or date.today()):
        raise LedgerError("Дата покупки не может быть в будущем")
    currency = currency or asset.currency
    ccy, usd = resolve_fx(market, currency, buy_date, fx_ccy_ils, fx_usd_ils)
    lot = Lot(
        asset_id=asset.id,
        custodian_id=custodian.id,
        buy_date=buy_date,
        qty=qty,
        qty_remaining=qty,
        price=price,
        fees=fees,
        vat=vat,
        currency=currency,
        fx_ccy_ils=ccy,
        fx_usd_ils=usd,
        kind=kind,
        purity=purity,
        form=form,
        gross_weight_g=gross_weight_g,
        note=note,
    )
    s.add(lot)
    s.flush()
    return lot


def open_lots(s: Session, asset_id: int, custodian_id: int | None = None) -> list[Lot]:
    q = s.query(Lot).filter(Lot.asset_id == asset_id, Lot.qty_remaining != "0")
    if custodian_id is not None:
        q = q.filter(Lot.custodian_id == custodian_id)
    return [lot for lot in q.order_by(Lot.buy_date, Lot.id).all() if lot.qty_remaining > 0]


def _allocate(
    lots: list[Lot], qty: Decimal | None, picks: Mapping[int, Decimal] | None
) -> list[tuple[Lot, Decimal]]:
    states = [lot_state(lot) for lot in lots]
    by_id = {lot.id: lot for lot in lots}
    try:
        if picks:
            alloc = manual_allocate(states, picks)
        else:
            if qty is None:
                raise LedgerError("Укажите количество")
            alloc = fifo_allocate(states, qty)
    except ValueError as exc:
        raise LedgerError(str(exc)) from exc
    return [(by_id[state.id], q) for state, q in alloc]


def sell(
    s: Session,
    market: MarketData,
    asset: Asset,
    custodian_id: int,
    day: date,
    price: Decimal,
    qty: Decimal | None = None,
    fee: Decimal = ZERO,
    currency: str | None = None,
    picks: Mapping[int, Decimal] | None = None,
    kind: str = "sale",
    il_tax_withheld: Decimal = ZERO,
    fx_ccy_ils: Decimal | None = None,
    fx_usd_ils: Decimal | None = None,
    note: str | None = None,
) -> list[Disposal]:
    """Продажа из одного места хранения. picks={id лота: кол-во} — ручной выбор, иначе FIFO.
    Комиссия и удержанный налог делятся между лотами пропорционально количеству."""
    if price < 0 or fee < 0 or il_tax_withheld < 0:
        raise LedgerError("Цена, комиссия и налог не могут быть отрицательными")
    currency = currency or asset.currency
    ccy, usd = resolve_fx(market, currency, day, fx_ccy_ils, fx_usd_ils)
    alloc = _allocate(open_lots(s, asset.id, custodian_id), qty, picks)
    total = sum((q for _, q in alloc), ZERO)
    group = str(uuid.uuid4())
    out = []
    for lot, q in alloc:
        if day < lot.buy_date:
            raise LedgerError(f"Дата продажи раньше даты покупки лота №{lot.id} ({lot.buy_date:%d.%m.%Y})")
        lot.qty_remaining = lot.qty_remaining - q
        disposal = Disposal(
            lot_id=lot.id,
            date=day,
            qty=q,
            price=price,
            fee=fee * q / total,
            currency=currency,
            fx_ccy_ils=ccy,
            fx_usd_ils=usd,
            kind=kind,
            il_tax_withheld=il_tax_withheld * q / total,
            group_id=group,
            note=note,
        )
        s.add(disposal)
        out.append(disposal)
    s.flush()
    return out


def delete_disposal_group(s: Session, group_id: str) -> int:
    """Отмена продажи или обмена: количество возвращается в лоты.
    Для обмена удаляется и полученный лот — если он ещё не продавался и не переводился."""
    rows = s.query(Disposal).filter(Disposal.group_id == group_id).all()
    if group_id:
        for received in s.query(Lot).filter(Lot.group_id == group_id).all():
            moved = (
                s.query(TransferPart)
                .filter((TransferPart.source_lot_id == received.id) | (TransferPart.dest_lot_id == received.id))
                .count()
            )
            if received.disposals or received.qty_remaining != received.qty or moved:
                raise LedgerError(
                    "Полученный при обмене лот уже продавался или переводился — отмена обмена невозможна"
                )
            s.delete(received)
    for d in rows:
        lot = s.get(Lot, d.lot_id)
        lot.qty_remaining = lot.qty_remaining + d.qty
        s.delete(d)
    s.flush()
    return len(rows)


def transfer(
    s: Session,
    market: MarketData,
    asset: Asset,
    from_custodian: Custodian,
    to_custodian: Custodian,
    day: date,
    qty_sent: Decimal,
    qty_received: Decimal | None = None,
    fee_amount: Decimal = ZERO,
    fee_currency: str = "USD",
    picks: Mapping[int, Decimal] | None = None,
    flagged_exchange: bool = False,
    note: str | None = None,
) -> Transfer:
    """Перевод между местами хранения: НЕ продажа. Дата и цена покупки лотов сохраняются.

    qty_received < qty_sent — комиссия сети, уплаченная самим активом.
    fee_amount — денежная комиссия; она добавляется к себестоимости переведённых лотов.
    """
    if from_custodian.id == to_custodian.id:
        raise LedgerError("Место отправления и получения совпадают")
    if qty_received is None:
        qty_received = qty_sent
    if qty_sent <= 0 or qty_received <= 0 or qty_received > qty_sent:
        raise LedgerError("Проверьте количества: получено должно быть > 0 и не больше отправленного")
    if fee_amount < 0:
        raise LedgerError("Комиссия не может быть отрицательной")
    alloc = _allocate(open_lots(s, asset.id, from_custodian.id), qty_sent, picks)
    total_sent = sum((q for _, q in alloc), ZERO)
    if picks and total_sent != qty_sent:
        raise LedgerError(f"Выбрано лотов на {total_sent}, а отправлено {qty_sent}")

    fee_ils = ZERO
    if fee_amount > 0:
        fee_fx, _ = resolve_fx(market, fee_currency, day)
        fee_ils = fee_amount * fee_fx

    record = Transfer(
        date=day,
        asset_id=asset.id,
        from_custodian_id=from_custodian.id,
        to_custodian_id=to_custodian.id,
        qty_sent=qty_sent,
        qty_received=qty_received,
        fee_amount=fee_amount,
        fee_currency=fee_currency,
        flagged_exchange=flagged_exchange,
        note=note,
    )
    s.add(record)
    s.flush()
    received_left = qty_received
    for index, (lot, q) in enumerate(alloc):
        if day < lot.buy_date:
            raise LedgerError(f"Дата перевода раньше даты покупки лота №{lot.id}")
        # Делим полученное между лотами без потери точности: без комиссии сети — ровно q,
        # иначе пропорционально, а последнему лоту достаётся точный остаток.
        if qty_received == total_sent:
            part_received = q
        elif index == len(alloc) - 1:
            part_received = received_left
        else:
            part_received = (qty_received * q / total_sent).quantize(QTY_STEP, rounding=ROUND_DOWN)
        received_left -= part_received
        extra_ccy = ZERO
        if fee_ils > 0:
            # Комиссия добавляется к себестоимости в ₪: делим на курс дня ПОКУПКИ лота,
            # потому что именно по нему себестоимость лота потом переводится в шекели.
            extra_ccy = fee_ils * q / total_sent / lot.fx_ccy_ils
        try:
            split = split_for_transfer(lot_state(lot), q, part_received, extra_ccy)
        except ValueError as exc:
            raise LedgerError(str(exc)) from exc
        old_qty = lot.qty
        lot.qty, lot.qty_remaining, lot.fees = split.source_qty, split.source_remaining, split.source_fees
        vat_moved = lot.vat * q / old_qty
        lot.vat = lot.vat - vat_moved
        weight_moved = None
        if lot.gross_weight_g is not None:
            weight_moved = lot.gross_weight_g * q / old_qty
            lot.gross_weight_g = lot.gross_weight_g - weight_moved
        dest = Lot(
            asset_id=asset.id,
            custodian_id=to_custodian.id,
            buy_date=lot.buy_date,
            qty=split.dest_qty,
            qty_remaining=split.dest_qty,
            price=split.dest_price,
            fees=split.dest_fees,
            vat=vat_moved,
            currency=lot.currency,
            fx_ccy_ils=lot.fx_ccy_ils,
            fx_usd_ils=lot.fx_usd_ils,
            kind="transfer_in",
            origin_lot_id=lot.origin_lot_id or lot.id,
            purity=lot.purity,
            form=lot.form,
            gross_weight_g=weight_moved,
            note=lot.note,
        )
        s.add(dest)
        s.flush()
        s.add(
            TransferPart(
                transfer_id=record.id,
                source_lot_id=lot.id,
                dest_lot_id=dest.id,
                qty_sent=q,
                fees_moved=split.fees_moved,
            )
        )
    s.flush()
    return record


def undo_transfer(s: Session, transfer_id: int) -> None:
    """Отмена перевода — только если полученные лоты ещё не продавались и не переводились дальше."""
    record = s.get(Transfer, transfer_id)
    if record is None:
        raise LedgerError("Перевод не найден")
    for part in record.parts:
        dest = s.get(Lot, part.dest_lot_id)
        source = s.get(Lot, part.source_lot_id)
        if dest is None or source is None:
            raise LedgerError("Лоты этого перевода уже изменены — отмена невозможна")
        if dest.qty_remaining != dest.qty or dest.disposals:
            raise LedgerError("Полученный лот уже продавался или переводился дальше — отмена невозможна")
        if s.query(TransferPart).filter(TransferPart.source_lot_id == dest.id).count():
            raise LedgerError("Полученный лот уже переводился дальше — отмена невозможна")
    for part in list(record.parts):
        dest = s.get(Lot, part.dest_lot_id)
        source = s.get(Lot, part.source_lot_id)
        source.qty = source.qty + part.qty_sent
        source.qty_remaining = source.qty_remaining + part.qty_sent
        source.fees = source.fees + part.fees_moved
        source.vat = source.vat + dest.vat
        if dest.gross_weight_g is not None:
            source.gross_weight_g = (source.gross_weight_g or ZERO) + dest.gross_weight_g
        s.delete(dest)
    s.delete(record)
    s.flush()


def swap(
    s: Session,
    market: MarketData,
    from_asset: Asset,
    to_asset: Asset,
    custodian: Custodian,
    day: date,
    qty_from: Decimal,
    qty_to: Decimal,
    value_usd: Decimal,
    fee_usd: Decimal = ZERO,
    picks: Mapping[int, Decimal] | None = None,
    to_custodian: Custodian | None = None,
    note: str | None = None,
) -> tuple[list[Disposal], Lot]:
    """Обмен одного актива на другой (крипта на крипту) — налоговая реализация.

    value_usd — рыночная стоимость отданного в USD на момент обмена.
    Старые лоты закрываются по этой стоимости (за вычетом комиссии), новый лот получает
    себестоимость = то, что фактически получено: value_usd − fee_usd.
    """
    if from_asset.id == to_asset.id:
        raise LedgerError("Обмен актива на самого себя невозможен")
    if qty_from <= 0 or qty_to <= 0 or value_usd <= 0:
        raise LedgerError("Количества и стоимость должны быть больше нуля")
    if fee_usd < 0 or fee_usd >= value_usd:
        raise LedgerError("Комиссия должна быть неотрицательной и меньше стоимости обмена")
    disposals = sell(
        s, market, from_asset, custodian.id, day, price=value_usd / qty_from, qty=None if picks else qty_from,
        fee=fee_usd, currency="USD", picks=picks, kind="swap", note=note,
    )
    sold = sum((d.qty for d in disposals), ZERO)
    if sold != qty_from:
        raise LedgerError(f"Выбрано лотов на {sold}, а в обмене указано {qty_from}")
    new_lot = add_lot(
        s, market, to_asset, to_custodian or custodian, day, qty_to, (value_usd - fee_usd) / qty_to,
        currency="USD", kind="swap_in", note=note, today=day,
    )
    new_lot.group_id = disposals[0].group_id
    return disposals, new_lot


def delete_lot(s: Session, lot_id: int) -> None:
    lot = s.get(Lot, lot_id)
    if lot is None:
        raise LedgerError("Лот не найден")
    if lot.disposals:
        raise LedgerError("По лоту есть продажи — сначала отмените их")
    used = (
        s.query(TransferPart)
        .filter((TransferPart.source_lot_id == lot_id) | (TransferPart.dest_lot_id == lot_id))
        .count()
    )
    if used:
        raise LedgerError("Лот участвует в переводе — сначала отмените перевод")
    s.delete(lot)
    s.flush()


def add_dividend(
    s: Session,
    market: MarketData,
    asset: Asset,
    custodian: Custodian,
    pay_date: date,
    gross: Decimal,
    foreign_tax: Decimal = ZERO,
    il_tax_withheld: Decimal = ZERO,
    currency: str | None = None,
    ex_date: date | None = None,
    note: str | None = None,
) -> Dividend:
    if gross <= 0:
        raise LedgerError("Сумма выплаты должна быть больше нуля")
    if foreign_tax < 0 or il_tax_withheld < 0 or foreign_tax + il_tax_withheld > gross:
        raise LedgerError("Проверьте суммы удержанного налога")
    currency = currency or asset.currency
    ccy, usd = resolve_fx(market, currency, pay_date)
    row = Dividend(
        asset_id=asset.id,
        custodian_id=custodian.id,
        ex_date=ex_date,
        pay_date=pay_date,
        gross=gross,
        currency=currency,
        foreign_tax=foreign_tax,
        il_tax_withheld=il_tax_withheld,
        fx_ccy_ils=ccy,
        fx_usd_ils=usd,
        note=note,
    )
    s.add(row)
    s.flush()
    return row
