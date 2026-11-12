"""Наследство США, концентрация, комиссии, долг, алгоритм Минкина, доходность и сценарии."""
from datetime import date, datetime
from decimal import Decimal as D
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from app.domain import minkin
from app.domain.money import D as to_dec, fine_grams, fmt_money, fmt_pct, fmt_qty, major_currency
from app.domain.pension import hedge_from_exposures, validate_weights, weights_from_exposures
from app.domain.returns import (
    cagr,
    dca_backtest,
    dca_projection,
    project_ex_dates,
    total_return,
    trailing_yield,
    what_if,
)
from app.domain.risk import (
    FeeSchedule,
    cheapest_venues,
    concentration,
    consolidation_saving,
    debt_vs_invest,
    estate_status,
    trade_fee,
)

IL = ZoneInfo("Asia/Jerusalem")


# ---------- деньги и единицы ----------

def test_money_helpers():
    assert major_currency("GBp") == ("GBP", D("0.01"))
    assert major_currency("ILA") == ("ILS", D("0.01"))
    assert major_currency("usd") == ("USD", D(1))
    assert to_dec(0.1) == D("0.1") and to_dec("1,234.5") == D("1234.5")
    assert fine_grams(D(1), "oz_t", D("0.999")) == D("31.0723733232")
    assert fine_grams(D(1), "kg", D(1)) == D(1000)
    with pytest.raises(ValueError):
        fine_grams(D(1), "g", D("1.1"))
    assert fmt_money(D("1234567.895"), "₪") == "1 234 567.90 ₪"
    assert fmt_pct(D("0.12345")) == "12.35%" and fmt_pct(D("-0.5"), signed=True) == "-50.00%"
    assert fmt_qty(D("0.50000000")) == "0.5" and fmt_qty(D("1500")) == "1 500"


# ---------- наследство США ----------

def test_estate_thresholds_and_plan():
    ok = estate_status({"IBKR": D(44999)})
    assert ok.level == "ok" and ok.plan_to_warn == []
    warn = estate_status({"IBKR": D(45000)})
    assert warn.level == "warn" and warn.over_warn_usd == 0
    over = estate_status({"IBKR": D(50000), "Blink": D(20000)})
    assert over.level == "over" and over.total_usd == D(70000)
    assert over.over_limit_usd == D(10000) and over.over_warn_usd == D(25000)
    assert over.plan_to_warn == [("IBKR", D(25000))]
    spread = estate_status({"A": D(30000), "B": D(40000)}, warn_usd=D(20000))
    assert spread.plan_to_warn == [("B", D(40000)), ("A", D(10000))]


def test_concentration_flags_over_threshold():
    shares = concentration({"A": D(70), "B": D(30)}, D(50))
    assert [(s.name, s.share, s.over_threshold) for s in shares] == [("A", D("0.7"), True), ("B", D("0.3"), False)]
    assert concentration({}, D(50)) == []


# ---------- комиссии ----------

def test_trade_fee_free_quota_then_paid():
    blink = FeeSchedule("Blink", fee_trade=D(3), free_trades_per_month=10)
    assert trade_fee(blink, D(1000), 9) == 0
    assert trade_fee(blink, D(1000), 10) == D(3)
    pct = FeeSchedule("Bank", fee_trade_pct=D("0.5"), fee_trade_min=D(20))
    assert trade_fee(pct, D(1000)) == D(20)       # 0.5% = 5 < минимум 20
    assert trade_fee(pct, D(10000)) == D("50.0")


def test_cheapest_venue_and_consolidation():
    ibkr = FeeSchedule("IBKR", fee_trade=D("2.5"))
    blink = FeeSchedule("Blink", fee_trade=D(3), free_trades_per_month=10)
    assert cheapest_venues([(ibkr, 0), (blink, 2)], D(500)) == [("Blink", D(0)), ("IBKR", D("2.5"))]
    assert cheapest_venues([(ibkr, 0), (blink, 10)], D(500))[0] == ("IBKR", D("2.5"))
    assert consolidation_saving(ibkr, D(2000), 4) == D("7.5")


def test_debt_vs_invest_is_honest_about_tax():
    # овердрафт 12%, доходность 12% брутто → нетто 9% → выгоднее гасить долг
    v = debt_vs_invest(D(12), D(12), D("0.25"), D(10000))
    assert v.net_yield == D("0.0900") and v.better == "repay" and v.annual_diff == D("-300.0000")
    assert debt_vs_invest(D(5), D(12), D("0.25"), D(10000)).better == "invest"


# ---------- алгоритм Минкина ----------

def test_sma_signal():
    assert minkin.sma_signal([1.0] * 149) is None
    sig = minkin.sma_signal([float(i) for i in range(1, 151)])
    assert sig.sma == D("75.5") and sig.above and sig.price == D("150.0")
    below = minkin.sma_signal([100.0] * 149 + [50.0])
    assert not below.above and below.distance < 0


def test_rebalance_new_cash_goes_to_laggard_without_selling():
    rows = {r.symbol: r for r in minkin.rebalance({"SPYI": D(6000), "JEPI": D(4000)},
                                                  {"SPYI": D(50), "JEPI": D(50)}, D(1000))}
    assert rows["SPYI"].buy_with_new_cash == 0 and rows["JEPI"].buy_with_new_cash == D(1000)
    assert rows["SPYI"].trade_to_target == D(-500) and rows["JEPI"].trade_to_target == D(1500)
    assert rows["SPYI"].drift == D("0.1")
    with pytest.raises(ValueError):
        minkin.rebalance({}, {"A": D(60), "B": D(50)})


def test_rebalance_split_when_cash_exceeds_gap_and_empty_portfolio():
    rows = {r.symbol: r for r in minkin.rebalance({"A": D(100), "B": D(0)}, {"A": D(50), "B": D(50)}, D(300))}
    assert rows["A"].buy_with_new_cash == D(100) and rows["B"].buy_with_new_cash == D(200)
    empty = minkin.rebalance({}, {"A": D(50), "B": D(50)}, D(100))
    assert [r.buy_with_new_cash for r in empty] == [D(50), D(50)]


def test_blacklist():
    assert minkin.blacklist_hit(" msty ")
    assert not minkin.blacklist_hit("SPYI")
    assert minkin.blacklist_hit("xyz", ["XYZ"])


@pytest.mark.parametrize("day,start", [
    (date(2026, 1, 15), "17:00"),   # обе страны на зимнем времени
    (date(2026, 7, 1), "17:00"),    # обе на летнем
    (date(2026, 3, 16), "16:00"),   # США уже перешли на летнее, Израиль ещё нет
    (date(2026, 10, 27), "16:00"),  # Израиль уже на зимнем, США ещё нет
])
def test_buy_window_israel_time_with_dst(day, start):
    w = minkin.buy_window(day)
    assert w.start_il.strftime("%H:%M") == start
    assert (w.end_il - w.start_il).seconds == 30 * 60


def test_window_state():
    w = minkin.buy_window(date(2026, 7, 1))
    assert minkin.window_state(datetime(2026, 7, 1, 16, 59, tzinfo=IL), w) == "before"
    assert minkin.window_state(datetime(2026, 7, 1, 17, 15, tzinfo=IL), w) == "open"
    assert minkin.window_state(datetime(2026, 7, 1, 17, 31, tzinfo=IL), w) == "after"
    assert minkin.window_state(datetime(2026, 7, 4, 17, 15, tzinfo=IL), minkin.buy_window(date(2026, 7, 4))) == "closed"


def test_xday_warning_and_dca_rule():
    today = date(2026, 11, 10)
    assert minkin.xday_warning(date(2026, 11, 12), today)
    assert minkin.xday_warning(today, today)
    assert not minkin.xday_warning(date(2026, 11, 13), today)
    assert not minkin.xday_warning(date(2026, 11, 9), today)
    assert not minkin.xday_warning(None, today)
    assert minkin.dca_amount(D(100001), D(100000), D(500), D(100)) == D(100)
    assert minkin.dca_amount(D(100000), D(100000), D(500), D(100)) == D(500)


# ---------- доходность и сценарии ----------

def daily(values, start="2021-01-01", freq="D"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq=freq), dtype=float)


def test_total_return_and_cagr():
    s = daily([100.0] + [0.0] * 0 + [100.0] * 364 + [121.0] * 400)
    s.iloc[-1] = 121.0
    tr = total_return(pd.Series([100.0, 121.0], index=pd.to_datetime(["2024-01-01", "2026-01-01"])), 2)
    assert tr == D("0.21")
    assert abs(cagr(tr, 2) - D("0.10")) < D("1e-9")
    assert total_return(pd.Series([100.0, 121.0], index=pd.to_datetime(["2025-06-01", "2026-01-01"])), 2) is None
    assert total_return(pd.Series(dtype=float), 1) is None


def test_trailing_yield_uses_last_365_days_only():
    divs = pd.Series([1.0, 1.0, 1.0], index=pd.to_datetime(["2025-01-01", "2026-03-01", "2026-09-01"]))
    assert trailing_yield(divs, D(50), date(2026, 11, 1)) == D("0.04")
    assert trailing_yield(pd.Series(dtype=float), D(50), date(2026, 11, 1)) == 0


def test_project_ex_dates_from_monthly_history():
    hist = [date(2026, 7, 22), date(2026, 8, 19), date(2026, 9, 16), date(2026, 10, 21)]
    nxt = project_ex_dates(hist, date(2026, 11, 1), horizon_days=60)
    assert nxt and nxt[0] == date(2026, 11, 18)   # медианный шаг 28 дней
    assert project_ex_dates([date(2026, 1, 1)], date(2026, 11, 1)) == []


def test_dca_backtest_exact():
    # цена: янв 100, фев 200, мар 400 (по первому дню месяца)
    s = pd.Series([100.0, 200.0, 400.0], index=pd.to_datetime(["2026-01-02", "2026-02-02", "2026-03-02"]))
    r = dca_backtest(s, date(2026, 1, 1), date(2026, 3, 31), D(100))
    assert r.invested == D(300) and r.units == D("1.75")      # 1 + 0.5 + 0.25
    assert r.value == D("700.00") and r.gain == D("400.00")
    assert len(r.points) == 3


def test_dca_backtest_with_price_rule():
    s = pd.Series([100.0, 200.0, 400.0], index=pd.to_datetime(["2026-01-02", "2026-02-02", "2026-03-02"]))
    rule = lambda day, price: minkin.dca_amount(price, D(150), D(100), D(20))  # noqa: E731
    r = dca_backtest(s, date(2026, 1, 1), date(2026, 3, 31), rule)
    assert [p.amount for p in r.points] == [D(100), D(20), D(20)]
    assert r.invested == D(140) and r.units == D("1.15")      # 1 + 0.1 + 0.05


def test_dca_projection():
    flat = dca_projection(D(100), 12, D(0))
    assert flat[-1] == (12, D(1200), D(1200))
    grow = dca_projection(D(100), 12, D(12))
    assert grow[-1][1] == D(1200) and grow[-1][2] > D(1200)
    assert abs(dca_projection(D(0), 12, D(12), D(1000))[-1][2] - D(1120)) < D("1e-6")


def test_what_if_btc_one_million():
    w = what_if(D(2), D(100000), D(1000000), D("0.25"))
    assert (w.value, w.gain, w.tax, w.net) == (D(2000000), D(1900000), D("475000.00"), D("1525000.00"))
    assert what_if(D(1), D(100), D(50), D("0.25")).tax == 0


# ---------- пенсия: веса из официальных экспозиций ----------

def test_weights_from_exposures_sum_to_100():
    sp = weights_from_exposures(D(100), D(100))
    assert sp["foreign_equity"] == D(100)
    mix = weights_from_exposures(D(45), D(40))
    validate_weights(mix)
    assert mix == {"israel_equity": D(27), "foreign_equity": D(18), "israel_bonds": D(33),
                   "foreign_bonds": D(22), "cash_other": D(0)}
    validate_weights(weights_from_exposures(D("120.5"), D("69.45")))  # экспозиция > 100% обрезается
    validate_weights(weights_from_exposures(D("48.15"), D("49.06")))


def test_hedge_from_exposures():
    assert hedge_from_exposures(D(40), D(10)) == D(75)
    assert hedge_from_exposures(D(40), D(50)) == 0
    assert hedge_from_exposures(D(0), D(0)) == 0
