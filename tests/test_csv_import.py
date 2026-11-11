from decimal import Decimal as D

from app.config import load_pension_config
from app.importers.pension_csv import normalize_period, parse_official_csv

COLS = load_pension_config().columns


def test_parse_comma_decimal_and_percent_sign_and_semicolon():
    text = (
        "period;return_pct\n"
        "2026-01;1,23%\n"
        "02/2026;-0,5\n"
    )
    res = parse_official_csv(text, COLS)
    assert res.ok, res.errors
    assert [r.period for r in res.rows] == ["2026-01", "2026-02"]
    assert res.rows[0].return_pct == D("1.23")
    assert res.rows[1].return_pct == D("-0.5")
    assert res.rows[0].composition is None


def test_composition_parsed_and_validated():
    header = "period,return_pct,israel_equity_pct,foreign_equity_pct,israel_bonds_pct,foreign_bonds_pct,cash_other_pct\n"
    good = header + "2026-03,2.0,40,40,10,5,5\n"
    res = parse_official_csv(good, COLS)
    assert res.ok, res.errors
    assert res.rows[0].composition["foreign_equity"] == D(40)


def test_composition_not_100_is_reported_with_line_number():
    header = "period,return_pct,israel_equity_pct,foreign_equity_pct,israel_bonds_pct,foreign_bonds_pct,cash_other_pct\n"
    bad = header + "2026-03,2.0,40,40,10,5,4\n"
    res = parse_official_csv(bad, COLS)
    assert not res.ok
    assert "Строка 2" in res.errors[0] and "100" in res.errors[0]


def test_partial_composition_columns_rejected():
    text = "period,return_pct,israel_equity_pct\n2026-01,1,50\n"
    res = parse_official_csv(text, COLS)
    assert res.errors and "не полностью" in res.errors[0]


def test_missing_required_columns_and_bad_values():
    res = parse_official_csv("foo,bar\n1,2\n", COLS)
    assert res.errors and "period" in res.errors[0]
    res2 = parse_official_csv("period,return_pct\n2026-01,abc\n2026-13,1\n2026-02,1\n2026-02,2\n", COLS)
    msgs = " ".join(res2.errors)
    assert "не число" in msgs and "2026-13" not in msgs or "месяц" in msgs
    assert "повторяется" in msgs


def test_normalize_period_formats():
    assert normalize_period("2026-5") == "2026-05"
    assert normalize_period("2026-05-31") == "2026-05"
    assert normalize_period("5/2026") == "2026-05"
