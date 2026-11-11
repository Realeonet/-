from datetime import date
from decimal import Decimal as D

import pytest

from app.domain.pension import (
    class_returns_ils,
    compound,
    daily_change,
    estimate_balance,
    foreign_return_ils,
    hedge_fraction,
    validate_weights,
    weights_stale,
)

SP500 = {"israel_equity": D(0), "foreign_equity": D(100), "israel_bonds": D(0),
         "foreign_bonds": D(0), "cash_other": D(0)}


def test_validate_weights_accepts_exact_100():
    validate_weights({"israel_equity": D("30.5"), "foreign_equity": D("40"),
                      "israel_bonds": D("20"), "foreign_bonds": D("5"), "cash_other": D("4.5")})


def test_validate_weights_rejects_sum_not_100():
    bad = dict(SP500, foreign_equity=D("99.99"))
    with pytest.raises(ValueError, match="ровно 100"):
        validate_weights(bad)


def test_validate_weights_rejects_negative_and_missing():
    with pytest.raises(ValueError, match="отрицательным"):
        validate_weights(dict(SP500, israel_equity=D("-1"), foreign_equity=D("101")))
    with pytest.raises(ValueError, match="Не заполнены"):
        validate_weights({"israel_equity": D(100)})


def test_foreign_return_without_hedge_is_full_fx_effect():
    # USD-актив +10%, шекель укрепился: USD/ILS 3.00 -> 2.70 (-10%)
    r = foreign_return_ils(D("0.10"), D("-0.10"), D("0"))
    assert r == D("1.10") * D("0.90") - 1  # = -0.01 точно
    assert r == D("-0.01")


def test_foreign_return_full_hedge_removes_fx():
    r = foreign_return_ils(D("0.10"), D("-0.10"), D("1"))
    assert r == D("0.10")


def test_foreign_return_half_hedge():
    # (1+0.10) * (1 + 0.10 * 0.5) - 1 = 1.10 * 1.05 - 1 = 0.155
    assert foreign_return_ils(D("0.10"), D("0.10"), hedge_fraction(D("50"))) == D("0.155")


def test_hedge_fraction_bounds():
    assert hedge_fraction(D("0")) == 0
    assert hedge_fraction(D("100")) == 1
    with pytest.raises(ValueError):
        hedge_fraction(D("100.1"))
    with pytest.raises(ValueError):
        hedge_fraction(D("-1"))


def test_class_returns_cash_is_zero_and_israel_untouched():
    cr = class_returns_ils({"israel_equity": D("0.02")}, {"foreign_equity": D("0.01")},
                           D("0.01"), D("0"))
    assert cr["israel_equity"] == D("0.02")
    assert cr["israel_bonds"] == 0
    assert cr["cash_other"] == 0
    assert cr["foreign_equity"] == D("1.01") * D("1.01") - 1


def test_daily_change_weighted_sum_exact():
    weights = {"israel_equity": D(50), "foreign_equity": D(30), "israel_bonds": D(10),
               "foreign_bonds": D(0), "cash_other": D(10)}
    returns = {"israel_equity": D("0.02"), "foreign_equity": D("0.01"),
               "israel_bonds": D("-0.001"), "foreign_bonds": D("0.5"), "cash_other": D(0)}
    # 0.5*0.02 + 0.3*0.01 + 0.1*(-0.001) = 0.01 + 0.003 - 0.0001 = 0.0129
    assert daily_change(weights, returns) == D("0.0129")


def test_compound_and_estimate_balance():
    assert compound([D("0.1"), D("0.1")]) == D("0.21")
    assert estimate_balance(D("1000"), [D("0.1"), D("-0.1")]) == D("990.0")
    assert estimate_balance(D("1000"), []) == D("1000")


def test_weights_stale_threshold():
    today = date(2026, 11, 11)
    assert weights_stale(None, today)
    assert not weights_stale(date(2026, 9, 27), today)  # 45 дней ровно — ещё свежо
    assert weights_stale(date(2026, 9, 26), today)      # 46 дней
