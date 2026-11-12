"""«Алгоритм Минкина» — личные правила. Все параметры настраиваются в интерфейсе."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.domain.money import HUNDRED, ZERO

NY = ZoneInfo("America/New_York")
IL = ZoneInfo("Asia/Jerusalem")
US_OPEN = time(9, 30)

DEFAULT_BLACKLIST = ("BITO", "BITI", "MSTY", "NVDY", "PLTW")
DEFAULT_SMA_WINDOW = 150


# ---------- SMA ----------


@dataclass(frozen=True)
class SmaSignal:
    price: Decimal
    sma: Decimal
    distance: Decimal  # (цена − SMA) / SMA
    above: bool
    window: int


def sma_signal(closes: Sequence[float | Decimal], window: int = DEFAULT_SMA_WINDOW) -> SmaSignal | None:
    """Цена относительно скользящей средней. None — если истории меньше, чем окно."""
    if window < 1:
        raise ValueError("Окно SMA должно быть не меньше 1")
    values = [Decimal(repr(float(v))) if not isinstance(v, Decimal) else v for v in closes]
    if len(values) < window:
        return None
    tail = values[-window:]
    sma = sum(tail, ZERO) / window
    price = values[-1]
    return SmaSignal(price=price, sma=sma, distance=(price - sma) / sma, above=price > sma, window=window)


# ---------- ребалансировка ----------


@dataclass(frozen=True)
class RebalanceRow:
    symbol: str
    value: Decimal
    share: Decimal
    target: Decimal
    drift: Decimal  # share − target
    buy_with_new_cash: Decimal  # сколько докупить из новых денег
    trade_to_target: Decimal  # полный ребаланс: + купить, − продать


def rebalance(
    values: Mapping[str, Decimal], targets_pct: Mapping[str, Decimal], new_cash: Decimal = ZERO
) -> list[RebalanceRow]:
    """Подсказка ребалансировки к целевым долям.

    buy_with_new_cash — распределение новых денег БЕЗ продаж (продажа = налог):
    деньги идут в отстающие позиции так, чтобы после покупки доли были максимально близки к цели.
    trade_to_target — что нужно сделать для точного попадания в цель (с продажами).
    """
    if sum(targets_pct.values(), ZERO) != HUNDRED:
        raise ValueError("Целевые доли должны давать в сумме 100%")
    if new_cash < 0:
        raise ValueError("Сумма новых денег не может быть отрицательной")
    symbols = list(targets_pct)
    current = {s: values.get(s, ZERO) for s in symbols}
    total = sum(current.values(), ZERO)
    total_after = total + new_cash

    # Цель после пополнения и «водоналивное» распределение новых денег по отстающим.
    desired = {s: total_after * targets_pct[s] / HUNDRED for s in symbols}
    deficits = {s: max(ZERO, desired[s] - current[s]) for s in symbols}
    deficit_sum = sum(deficits.values(), ZERO)
    buys = {
        s: (new_cash * deficits[s] / deficit_sum if deficit_sum > 0 else new_cash * targets_pct[s] / HUNDRED)
        for s in symbols
    }

    rows = []
    for s in symbols:
        share = current[s] / total if total > 0 else ZERO
        target = targets_pct[s] / HUNDRED
        rows.append(
            RebalanceRow(
                symbol=s,
                value=current[s],
                share=share,
                target=target,
                drift=share - target,
                buy_with_new_cash=buys[s],
                trade_to_target=desired[s] - current[s],
            )
        )
    return rows


# ---------- чёрный список ----------


def blacklist_hit(symbol: str, blacklist: Iterable[str] = DEFAULT_BLACKLIST) -> bool:
    return symbol.strip().upper() in {b.strip().upper() for b in blacklist if b.strip()}


# ---------- окно покупки ----------


@dataclass(frozen=True)
class BuyWindow:
    day: date
    start_il: datetime
    end_il: datetime
    open_il: datetime
    is_weekday: bool


def buy_window(day: date, after_min: int = 30, until_min: int = 60) -> BuyWindow:
    """Окно покупки: через after_min…until_min минут после открытия рынка США (09:30 Нью-Йорк),
    в часовом поясе Израиля. Переходы на летнее/зимнее время в обеих странах учитываются.
    Праздники биржи не учитываются — только будни."""
    if not (0 <= after_min < until_min):
        raise ValueError("Окно задано неверно: начало должно быть раньше конца")
    open_ny = datetime.combine(day, US_OPEN, tzinfo=NY)
    open_il = open_ny.astimezone(IL)
    return BuyWindow(
        day=day,
        start_il=(open_ny + timedelta(minutes=after_min)).astimezone(IL),
        end_il=(open_ny + timedelta(minutes=until_min)).astimezone(IL),
        open_il=open_il,
        is_weekday=day.weekday() < 5,
    )


def window_state(now: datetime, window: BuyWindow) -> str:
    """before / open / after / closed (выходной)."""
    if not window.is_weekday:
        return "closed"
    now_il = now.astimezone(IL)
    if now_il < window.start_il:
        return "before"
    if now_il <= window.end_il:
        return "open"
    return "after"


# ---------- X-day ----------


def xday_warning(next_ex: date | None, today: date, days_before: int = 2) -> bool:
    """True, если до ex-dividend дня осталось не больше days_before дней (включая сам X-day):
    покупка сейчас = налог 25% с ближайшей выплаты."""
    if next_ex is None:
        return False
    delta = (next_ex - today).days
    return 0 <= delta <= days_before


# ---------- крипто-DCA ----------


def dca_amount(
    btc_price: Decimal, threshold_price: Decimal, full_amount: Decimal, reduced_amount: Decimal
) -> Decimal:
    """Если BTC выше порога — уменьшенная покупка; если на пороге или ниже — полная."""
    return reduced_amount if btc_price > threshold_price else full_amount
