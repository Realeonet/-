"""Чистая математика пенсионного модуля.

Все величины — Decimal. Проценты в весах — 0..100, доли и доходности — дробные (0.05 = 5%).
Округление делается только при показе (см. format_money / format_pct в services).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date
from decimal import Decimal

CLASSES: tuple[str, ...] = (
    "israel_equity",
    "foreign_equity",
    "israel_bonds",
    "foreign_bonds",
    "cash_other",
)
FOREIGN_CLASSES: frozenset[str] = frozenset({"foreign_equity", "foreign_bonds"})

ZERO = Decimal("0")
ONE = Decimal("1")
HUNDRED = Decimal("100")
STALE_WEIGHTS_DAYS = 45

PRODUCT_TYPES: dict[str, str] = {
    "pension": "קרן פנסיה — пенсионный фонд",
    "hishtalmut": "קרן השתלמות — фонд обучения (штальмут)",
    "gemel": "קופת גמל — фонд накоплений",
    "menahalim": "ביטוח מנהלים — страхование менеджеров",
}

CLASS_LABELS: dict[str, str] = {
    "israel_equity": "Израильские акции",
    "foreign_equity": "Зарубежные акции",
    "israel_bonds": "Израильские облигации",
    "foreign_bonds": "Зарубежные облигации",
    "cash_other": "Кэш и прочее",
}


def validate_weights(weights: Mapping[str, Decimal]) -> None:
    """Веса в процентах: все 5 классов, неотрицательные, сумма ровно 100."""
    missing = [c for c in CLASSES if c not in weights]
    if missing:
        raise ValueError(f"Не заполнены веса: {', '.join(missing)}")
    for c in CLASSES:
        if weights[c] < 0:
            raise ValueError(f"Вес не может быть отрицательным: {c}")
    total = sum((Decimal(weights[c]) for c in CLASSES), ZERO)
    if total != HUNDRED:
        raise ValueError(f"Сумма весов должна быть ровно 100%, сейчас {total}%")


def hedge_fraction(hedge_pct: Decimal) -> Decimal:
    """Хедж в процентах (0..100) → доля (0..1)."""
    if not (ZERO <= hedge_pct <= HUNDRED):
        raise ValueError("Доля хеджирования должна быть от 0 до 100%")
    return hedge_pct / HUNDRED


def foreign_return_ils(r_usd: Decimal, r_fx: Decimal, hedge: Decimal) -> Decimal:
    """Доходность зарубежной части в шекелях.

    r_usd  — доходность актива в долларах за период.
    r_fx   — изменение курса USD/ILS за тот же период (доля).
    hedge  — доля хеджирования 0..1 (0 = валютный риск полностью, 1 = полностью захеджирован).

    return_ILS = (1 + r_USD) * (1 + r_FX * (1 - hedge)) - 1
    """
    return (ONE + r_usd) * (ONE + r_fx * (ONE - hedge)) - ONE


def class_returns_ils(
    local: Mapping[str, Decimal],
    usd: Mapping[str, Decimal],
    r_fx: Decimal,
    hedge: Decimal,
) -> dict[str, Decimal]:
    """Доходность каждого класса в шекелях за период.

    local — доходности израильских классов уже в ILS (israel_equity, israel_bonds).
    usd   — доходности зарубежных классов в USD (foreign_equity, foreign_bonds).
    Кэш всегда 0.
    """
    result: dict[str, Decimal] = {"cash_other": ZERO}
    for c in ("israel_equity", "israel_bonds"):
        result[c] = Decimal(local.get(c, ZERO))
    for c in FOREIGN_CLASSES:
        result[c] = foreign_return_ils(Decimal(usd.get(c, ZERO)), r_fx, hedge)
    return result


def daily_change(weights_pct: Mapping[str, Decimal], class_returns: Mapping[str, Decimal]) -> Decimal:
    """Σ(w_i × r_i), где w_i = вес/100."""
    return sum(
        (Decimal(weights_pct[c]) / HUNDRED) * Decimal(class_returns[c]) for c in CLASSES
    )


def estimated_change_ils(balance: Decimal, change: Decimal) -> Decimal:
    """Изменение баланса в ₪ за период (оценка)."""
    return Decimal(balance) * change


def compound(returns: Iterable[Decimal]) -> Decimal:
    """Суммарная доходность за ряд периодов: Π(1 + r) − 1."""
    total = ONE
    for r in returns:
        total *= ONE + Decimal(r)
    return total - ONE


def estimate_balance(balance: Decimal, changes: Iterable[Decimal]) -> Decimal:
    """Оценённый баланс после серии дневных изменений."""
    return Decimal(balance) * (ONE + compound(changes))


def weights_stale(as_of: date | None, today: date) -> bool:
    """True, если веса старше STALE_WEIGHTS_DAYS дней или дата неизвестна."""
    if as_of is None:
        return True
    return (today - as_of).days > STALE_WEIGHTS_DAYS
