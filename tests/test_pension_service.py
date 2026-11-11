from datetime import date
from decimal import Decimal as D

import pandas as pd
import pytest

from app.config import load_pension_config
from app.domain.pension import validate_weights
from app.services.pension import (
    AccountInputs,
    MissingProxyError,
    OfficialPoint,
    build_view,
    daily_changes,
    estimate_path,
    required_keys,
)

CFG = load_pension_config()


def series(values, start="2026-01-01"):
    idx = pd.bdate_range(start=start, periods=len(values))
    return pd.Series(values, index=idx, dtype=float)


def test_sp500_track_preset_sums_to_100_and_uses_gspc():
    preset = CFG.tracks["sp500"].preset
    validate_weights(preset)
    keys = required_keys(CFG, "sp500", preset)
    assert keys == {"foreign_equity": "^GSPC", "usd_ils": "USDILS=X"}


def test_daily_changes_fx_hedge_exact_within_float_tolerance():
    # ^GSPC +10% за день, USD/ILS +10%, хедж 0 → доходность в ₪ = 1.1*1.1-1 = 0.21
    weights = {"israel_equity": D(0), "foreign_equity": D(100), "israel_bonds": D(0),
               "foreign_bonds": D(0), "cash_other": D(0)}
    closes = {"foreign_equity": series([100.0, 110.0]), "usd_ils": series([3.0, 3.3])}
    (d, change), = daily_changes(weights, closes, D(0))
    assert d == date(2026, 1, 2)
    assert abs(change - D("0.21")) < D("1e-9")


def test_daily_changes_full_hedge_removes_fx():
    weights = {"israel_equity": D(0), "foreign_equity": D(100), "israel_bonds": D(0),
               "foreign_bonds": D(0), "cash_other": D(0)}
    closes = {"foreign_equity": series([100.0, 110.0]), "usd_ils": series([3.0, 3.3])}
    (_, change), = daily_changes(weights, closes, D(100))
    assert abs(change - D("0.10")) < D("1e-9")


def test_missing_foreign_proxy_raises():
    weights = {"israel_equity": D(0), "foreign_equity": D(100), "israel_bonds": D(0),
               "foreign_bonds": D(0), "cash_other": D(0)}
    with pytest.raises(MissingProxyError):
        daily_changes(weights, {"foreign_equity": series([1.0, 1.1])}, D(0))


def test_missing_israel_bonds_proxy_counts_as_zero():
    weights = {"israel_equity": D(0), "foreign_equity": D(0), "israel_bonds": D(100),
               "foreign_bonds": D(0), "cash_other": D(0)}
    changes = daily_changes(weights, {}, D(0))
    assert changes == []  # нет данных вообще — пустой ряд, без падения


def test_estimate_path_starts_after_balance_date_only():
    changes = [(date(2026, 1, 2), D("0.10")), (date(2026, 1, 5), D("-0.05"))]
    path = estimate_path(D("1000"), date(2026, 1, 2), changes)
    assert path == [(date(2026, 1, 5), D("1000") * D("0.95"))]


def test_build_view_end_to_end_and_comparison():
    weights = {"israel_equity": D(0), "foreign_equity": D(100), "israel_bonds": D(0),
               "foreign_bonds": D(0), "cash_other": D(0)}
    closes = {"foreign_equity": series([100.0, 110.0, 99.0]),
              "usd_ils": series([3.0, 3.0, 3.0])}
    inputs = AccountInputs(balance=D("10000"), balance_date=date(2026, 1, 1),
                           weights=weights, hedge_pct=D(100))
    view = build_view(inputs, closes, D(100), today=date(2026, 1, 5),
                      official=[OfficialPoint("2026-01", D("0.5"))])
    # +10% и затем -10%: 10000 * 1.1 * 0.9 = 9900
    assert abs(view.balance_today - D("9900")) < D("1e-6")
    assert view.today_change is not None
    assert view.comparisons and view.comparisons[0].used_composition is False
    assert abs(view.comparisons[0].diff_pp - (view.comparisons[0].estimate * 100 - D("0.5"))) < D("1e-9")


def test_build_view_warns_on_stale_manual_weights():
    weights = {"israel_equity": D(50), "foreign_equity": D(0), "israel_bonds": D(50),
               "foreign_bonds": D(0), "cash_other": D(0)}
    closes = {"israel_equity": series([100.0, 101.0])}  # котировок облигаций нет намеренно
    inputs = AccountInputs(balance=D("1000"), balance_date=date(2026, 1, 1), weights=weights,
                           hedge_pct=D(0), weights_as_of=date(2025, 1, 1), manual_track=True)
    view = build_view(inputs, closes, D(0), today=date(2026, 1, 5))
    assert any("45 дней" in w for w in view.warnings)
    assert any("облигаций" in w for w in view.warnings)
