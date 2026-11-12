"""Запасной импорт официальной доходности пенсии из CSV."""
from decimal import Decimal as D

from app.config import load_pension_config
from app.importers.pension_csv import normalize_period, parse_official_csv

COLS = load_pension_config().columns
HEADER = "period,return_pct,israel_equity_pct,foreign_equity_pct,israel_bonds_pct,foreign_bonds_pct,cash_other_pct\n"


def test_returns_only_with_comma_decimal_and_semicolon():
    res = parse_official_csv("period;return_pct\n2026-01;1,23%\n02/2026;-0,5\n", COLS)
    assert res.ok, res.errors
    assert [(r.period, r.return_pct) for r in res.rows] == [("2026-01", D("1.23")), ("2026-02", D("-0.5"))]
    assert res.rows[0].composition is None


def test_composition_validated_with_line_number():
    assert parse_official_csv(HEADER + "2026-03,2.0,40,40,10,5,5\n", COLS).rows[0].composition["foreign_equity"] == 40
    bad = parse_official_csv(HEADER + "2026-03,2.0,40,40,10,5,4\n", COLS)
    assert not bad.ok and "Строка 2" in bad.errors[0] and "100" in bad.errors[0]
    partial = parse_official_csv("period,return_pct,israel_equity_pct\n2026-01,1,50\n", COLS)
    assert "не полностью" in partial.errors[0]


def test_bad_rows_and_period_formats():
    res = parse_official_csv("period,return_pct\n2026-01,abc\n2026-13,1\n2026-02,1\n2026-02,2\n", COLS)
    text = " ".join(res.errors)
    assert "не число" in text and "месяц" in text and "повторяется" in text
    assert parse_official_csv("foo,bar\n1,2\n", COLS).errors
    assert normalize_period("2026-5") == "2026-05" and normalize_period("5/2026") == "2026-05"
