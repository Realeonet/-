"""Израильские налоги — ОРИЕНТИРОВОЧНЫЙ расчёт, сверять с налоговым консультантом.

Заложенные правила (всё настраивается, спорные места помечены «проверить»):
  • Прирост капитала: ставка 25% с прибыли по каждому лоту.
  • Метод 'nominal' (по умолчанию, выбран пользователем): прибыль в ₪ = выручка по курсу дня продажи
    − себестоимость по курсу дня покупки. Курсовая разница входит в налогооблагаемую прибыль.
  • Метод 'linked' (альтернатива, проверить): прибыль в валюте актива × курс дня продажи
    (курс валюты выступает как «мадад», курсовая разница не облагается).
  • Дивиденды: итоговая ставка 25% от брутто; налог, удержанный за рубежом, засчитывается.
  • Убытки уменьшают прибыль того же года (зачёт — проверить условия).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from app.domain.lots import LotState, cost_of
from app.domain.money import HUNDRED, ZERO

DEFAULT_RATE = Decimal("0.25")
METHODS = ("nominal", "linked")
DISCLAIMER = "Ориентировочно — сверить с налоговым консультантом"


@dataclass(frozen=True)
class Gain:
    lot_id: int
    qty: Decimal
    proceeds_ccy: Decimal  # в валюте продажи, за вычетом комиссии продажи
    cost_ccy: Decimal  # в валюте лота
    proceeds_usd: Decimal
    cost_usd: Decimal
    gain_usd: Decimal
    proceeds_ils: Decimal
    cost_ils: Decimal
    gain_ils_nominal: Decimal
    gain_ils_linked: Decimal
    taxable_ils: Decimal  # по выбранному методу; отрицательное = убыток
    tax_ils: Decimal  # ≥ 0
    method: str


def compute_gain(
    lot: LotState,
    qty: Decimal,
    price: Decimal,
    fee: Decimal,
    sale_currency: str,
    fx_sale_ccy_ils: Decimal,
    fx_sale_usd_ils: Decimal,
    rate: Decimal = DEFAULT_RATE,
    method: str = "nominal",
) -> Gain:
    """Прибыль и налог по части лота.

    price — цена продажи за единицу в sale_currency; fee — комиссия продажи на эту часть.
    fx_sale_ccy_ils — ₪ за единицу валюты продажи в день продажи; fx_sale_usd_ils — ₪ за $1.
    """
    if method not in METHODS:
        raise ValueError(f"Неизвестный метод расчёта: {method}")
    if qty <= 0:
        raise ValueError("Количество должно быть больше нуля")
    if qty > lot.remaining:
        raise ValueError(f"В лоте {lot.id} осталось {lot.remaining}, запрошено {qty}")
    if min(fx_sale_ccy_ils, fx_sale_usd_ils, lot.fx_ccy_ils, lot.fx_usd_ils) <= 0:
        raise ValueError("Курс валюты должен быть больше нуля")

    proceeds_ccy = qty * price - fee
    cost_ccy = cost_of(lot, qty)

    proceeds_ils = proceeds_ccy * fx_sale_ccy_ils
    cost_ils = cost_ccy * lot.fx_ccy_ils
    gain_nominal = proceeds_ils - cost_ils

    proceeds_usd = proceeds_ils / fx_sale_usd_ils
    cost_usd = cost_ils / lot.fx_usd_ils
    gain_usd = proceeds_usd - cost_usd

    if lot.currency == "ILS":
        gain_linked = gain_nominal
    elif sale_currency == lot.currency:
        gain_linked = (proceeds_ccy - cost_ccy) * fx_sale_ccy_ils
    else:
        gain_linked = gain_usd * fx_sale_usd_ils

    taxable = gain_nominal if method == "nominal" else gain_linked
    tax = taxable * rate if taxable > 0 else ZERO
    return Gain(
        lot_id=lot.id,
        qty=qty,
        proceeds_ccy=proceeds_ccy,
        cost_ccy=cost_ccy,
        proceeds_usd=proceeds_usd,
        cost_usd=cost_usd,
        gain_usd=gain_usd,
        proceeds_ils=proceeds_ils,
        cost_ils=cost_ils,
        gain_ils_nominal=gain_nominal,
        gain_ils_linked=gain_linked,
        taxable_ils=taxable,
        tax_ils=tax,
        method=method,
    )


@dataclass(frozen=True)
class SaleSimulation:
    gains: list[Gain]
    proceeds_ils: Decimal
    proceeds_usd: Decimal
    taxable_ils: Decimal  # сумма по лотам (убытки уменьшают прибыль)
    tax_ils: Decimal  # 25% от положительной суммы
    net_ils: Decimal  # на руки после налога
    net_usd: Decimal


def simulate_sale(
    allocation: Sequence[tuple[LotState, Decimal]],
    price: Decimal,
    fee_total: Decimal,
    sale_currency: str,
    fx_sale_ccy_ils: Decimal,
    fx_sale_usd_ils: Decimal,
    rate: Decimal = DEFAULT_RATE,
    method: str = "nominal",
) -> SaleSimulation:
    """«Если продам» — по набору (лот, количество). Комиссия делится пропорционально количеству."""
    total_qty = sum((q for _, q in allocation), ZERO)
    if total_qty <= 0:
        raise ValueError("Нечего продавать")
    gains = [
        compute_gain(
            lot, q, price, fee_total * q / total_qty, sale_currency, fx_sale_ccy_ils, fx_sale_usd_ils, rate, method
        )
        for lot, q in allocation
    ]
    proceeds_ils = sum((g.proceeds_ils for g in gains), ZERO)
    taxable = sum((g.taxable_ils for g in gains), ZERO)
    tax = taxable * rate if taxable > 0 else ZERO
    net_ils = proceeds_ils - tax
    return SaleSimulation(
        gains=gains,
        proceeds_ils=proceeds_ils,
        proceeds_usd=proceeds_ils / fx_sale_usd_ils,
        taxable_ils=taxable,
        tax_ils=tax,
        net_ils=net_ils,
        net_usd=net_ils / fx_sale_usd_ils,
    )


def rank_lots_for_sale(
    lots: Iterable[LotState],
    price: Decimal,
    sale_currency: str,
    fx_sale_ccy_ils: Decimal,
    fx_sale_usd_ils: Decimal,
    rate: Decimal = DEFAULT_RATE,
    method: str = "nominal",
) -> list[tuple[LotState, Gain]]:
    """Какие лоты выгоднее продавать первыми: с наименьшей налогооблагаемой прибылью на единицу
    (убыточные — в начале списка)."""
    ranked = []
    for lot in lots:
        if lot.remaining <= 0:
            continue
        gain = compute_gain(
            lot, lot.remaining, price, ZERO, sale_currency, fx_sale_ccy_ils, fx_sale_usd_ils, rate, method
        )
        ranked.append((lot, gain))
    ranked.sort(key=lambda x: (x[1].taxable_ils / x[1].qty, x[0].buy_date, x[0].id))
    return ranked


@dataclass(frozen=True)
class DividendTax:
    gross: Decimal
    total_due: Decimal  # 25% от брутто
    foreign_credit: Decimal  # зачтённый иностранный налог (не больше total_due)
    il_withheld: Decimal
    to_pay: Decimal  # осталось доплатить/задекларировать в Израиле
    net: Decimal  # брутто − итоговый налог (не ниже фактически удержанного)


def dividend_tax(
    gross: Decimal,
    foreign_withheld: Decimal = ZERO,
    il_withheld: Decimal = ZERO,
    rate: Decimal = DEFAULT_RATE,
) -> DividendTax:
    """Дивиденд иностранного фонда: итог 25% от брутто, удержанное за рубежом засчитывается.

    Если за рубежом удержали больше 25%, излишек не возвращается через израильский расчёт
    (кредит ограничен суммой израильского налога) — проверить возможность возврата у источника.
    """
    if gross < 0 or foreign_withheld < 0 or il_withheld < 0:
        raise ValueError("Суммы не могут быть отрицательными")
    total_due = gross * rate
    credit = min(foreign_withheld, total_due)
    to_pay = max(ZERO, total_due - credit - il_withheld)
    actually_paid = foreign_withheld + il_withheld + to_pay
    return DividendTax(
        gross=gross,
        total_due=total_due,
        foreign_credit=credit,
        il_withheld=il_withheld,
        to_pay=to_pay,
        net=gross - actually_paid,
    )


def net_of_tax(gross: Decimal, rate: Decimal = DEFAULT_RATE) -> Decimal:
    """Брутто → нетто после налога (для прогнозов дохода)."""
    return gross * (1 - rate)


@dataclass(frozen=True)
class AnnualSummary:
    gains_ils: Decimal  # сумма прибылей
    losses_ils: Decimal  # сумма убытков (положительное число)
    net_ils: Decimal
    tax_ils: Decimal  # 25% от положительного результата года
    withheld_ils: Decimal  # уже удержано брокерами
    to_declare_ils: Decimal  # осталось задекларировать / доплатить (≥ 0)
    loss_carry_ils: Decimal  # убыток года для переноса (проверить правила)


def annual_summary(
    items: Iterable[tuple[Decimal, Decimal]], rate: Decimal = DEFAULT_RATE
) -> AnnualSummary:
    """items: (налогооблагаемый результат реализации в ₪, удержанный брокером налог в ₪)."""
    gains = losses = withheld = ZERO
    for taxable, held in items:
        if taxable >= 0:
            gains += taxable
        else:
            losses += -taxable
        withheld += held
    net = gains - losses
    tax = net * rate if net > 0 else ZERO
    return AnnualSummary(
        gains_ils=gains,
        losses_ils=losses,
        net_ils=net,
        tax_ils=tax,
        withheld_ils=withheld,
        to_declare_ils=max(ZERO, tax - withheld),
        loss_carry_ils=-net if net < 0 else ZERO,
    )


def effective_rate(base_pct: Decimal, surtax_pct: Decimal = ZERO) -> Decimal:
    """Ставка как доля: базовая 25% + опциональный מס יסף (вводится вручную, проверить)."""
    total = base_pct + surtax_pct
    if not (ZERO <= total <= HUNDRED):
        raise ValueError("Ставка налога должна быть от 0 до 100%")
    return total / HUNDRED
