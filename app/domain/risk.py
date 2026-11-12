"""Риски: налог США на наследство (US-situs), концентрация по местам хранения, комиссии, долг."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal

from app.domain.money import HUNDRED, ZERO

# ---------- налог США на наследство ----------

ESTATE_LIMIT_USD = Decimal("60000")
ESTATE_WARN_USD = Decimal("45000")


@dataclass(frozen=True)
class EstateStatus:
    total_usd: Decimal
    limit_usd: Decimal
    warn_usd: Decimal
    level: str  # ok / warn / over
    over_warn_usd: Decimal  # сколько убрать, чтобы уйти ниже рабочего порога
    over_limit_usd: Decimal  # сколько убрать, чтобы уйти ниже лимита
    by_custodian: dict[str, Decimal]
    plan_to_warn: list[tuple[str, Decimal]]  # откуда и сколько перевести/заменить


def reduction_plan(by_custodian: Mapping[str, Decimal], excess: Decimal) -> list[tuple[str, Decimal]]:
    """Сколько и из какого места убрать, чтобы снять превышение: начиная с самых крупных мест."""
    plan: list[tuple[str, Decimal]] = []
    left = excess
    for name, amount in sorted(by_custodian.items(), key=lambda x: (-x[1], x[0])):
        if left <= 0:
            break
        take = min(amount, left)
        if take > 0:
            plan.append((name, take))
            left -= take
    return plan


def estate_status(
    by_custodian: Mapping[str, Decimal],
    limit_usd: Decimal = ESTATE_LIMIT_USD,
    warn_usd: Decimal = ESTATE_WARN_USD,
) -> EstateStatus:
    """by_custodian: стоимость US-situs бумаг (акции/ETF с домицилем США) по местам, в USD."""
    total = sum(by_custodian.values(), ZERO)
    if total >= limit_usd:
        level = "over"
    elif total >= warn_usd:
        level = "warn"
    else:
        level = "ok"
    over_warn = max(ZERO, total - warn_usd)
    return EstateStatus(
        total_usd=total,
        limit_usd=limit_usd,
        warn_usd=warn_usd,
        level=level,
        over_warn_usd=over_warn,
        over_limit_usd=max(ZERO, total - limit_usd),
        by_custodian=dict(by_custodian),
        plan_to_warn=reduction_plan(by_custodian, over_warn),
    )


# ---------- концентрация ----------


@dataclass(frozen=True)
class Share:
    name: str
    value: Decimal
    share: Decimal  # доля 0..1
    over_threshold: bool


def concentration(values: Mapping[str, Decimal], threshold_pct: Decimal) -> list[Share]:
    total = sum(values.values(), ZERO)
    out = []
    for name, value in sorted(values.items(), key=lambda x: (-x[1], x[0])):
        share = value / total if total > 0 else ZERO
        out.append(Share(name, value, share, share * HUNDRED > threshold_pct))
    return out


# ---------- комиссии ----------


@dataclass(frozen=True)
class FeeSchedule:
    name: str
    fee_trade: Decimal = ZERO  # фикс. за сделку
    fee_trade_pct: Decimal = ZERO  # % от суммы
    fee_trade_min: Decimal = ZERO  # минимум за сделку
    free_trades_per_month: int = 0


def trade_fee(schedule: FeeSchedule, trade_value: Decimal, trades_this_month: int = 0) -> Decimal:
    """Комиссия за одну сделку. trades_this_month — сколько сделок уже было в этом месяце."""
    if trade_value < 0:
        raise ValueError("Сумма сделки не может быть отрицательной")
    if trades_this_month < schedule.free_trades_per_month:
        return ZERO
    fee = schedule.fee_trade + trade_value * schedule.fee_trade_pct / HUNDRED
    return max(fee, schedule.fee_trade_min)


def total_fees(schedule: FeeSchedule, trade_value_each: Decimal, n_trades: int, trades_this_month: int = 0) -> Decimal:
    return sum(
        (trade_fee(schedule, trade_value_each, trades_this_month + i) for i in range(n_trades)), ZERO
    )


def cheapest_venues(
    schedules: Iterable[tuple[FeeSchedule, int]], trade_value: Decimal
) -> list[tuple[str, Decimal]]:
    """Где дешевле совершить сделку: [(место, комиссия)] по возрастанию.
    schedules: (тариф, уже сделок в этом месяце)."""
    ranked = [(s.name, trade_fee(s, trade_value, used)) for s, used in schedules]
    return sorted(ranked, key=lambda x: (x[1], x[0]))


def consolidation_saving(
    schedule: FeeSchedule, total_value: Decimal, n_trades: int, trades_this_month: int = 0
) -> Decimal:
    """Экономия, если n мелких сделок объединить в одну на ту же общую сумму."""
    if n_trades < 1:
        raise ValueError("Число сделок должно быть не меньше 1")
    split = total_fees(schedule, total_value / n_trades, n_trades, trades_this_month)
    single = trade_fee(schedule, total_value, trades_this_month)
    return split - single


# ---------- долг против инвестиций ----------


@dataclass(frozen=True)
class DebtVerdict:
    debt_rate: Decimal  # доля в год
    net_yield: Decimal  # доля в год после налога
    spread: Decimal  # net_yield − debt_rate
    annual_diff: Decimal  # на заданную сумму: + выгоднее инвестировать, − выгоднее гасить долг
    better: str  # invest / repay / equal


def debt_vs_invest(
    debt_rate_pct: Decimal,
    gross_yield_pct: Decimal,
    tax_rate: Decimal,
    amount: Decimal,
    growth_pct: Decimal = ZERO,
) -> DebtVerdict:
    """Честное сравнение: ставка долга против нетто-доходности.

    Нетто-доходность = (дивидендная доходность + ожидаемый рост цены) × (1 − налог).
    Рост цены не гарантирован, а проценты по долгу — гарантированы; по умолчанию рост = 0.
    """
    debt = debt_rate_pct / HUNDRED
    net = (gross_yield_pct + growth_pct) / HUNDRED * (1 - tax_rate)
    spread = net - debt
    better = "invest" if spread > 0 else "repay" if spread < 0 else "equal"
    return DebtVerdict(debt_rate=debt, net_yield=net, spread=spread, annual_diff=amount * spread, better=better)
