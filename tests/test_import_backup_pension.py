"""Импорт CSV, зашифрованный бэкап, пароль, пенсионный сервис."""
from datetime import date
from decimal import Decimal as D

import pandas as pd
import pytest

from app.adapters.gov_il import OfficialRecord, parse_record
from app.backup import auto_backup, decrypt_blob, encrypt_session, restore_blob
from app.config import load_pension_config
from app.db import (Asset, Custodian, Disposal, Dividend, Lot, Owner, PensionAccount, PricePoint, Setting,
                    get_engine, get_setting, init_db, make_session_factory, set_setting)
from app.domain.pension import validate_weights
from app.importers.trades_csv import csv_headers, parse_generic, parse_ibkr, to_date, to_decimal
from app.security import hash_password, verify_password
from app.services import ledger
from app.services.importing import apply_import, unknown_symbols
from app.services.pension import (MissingProxyError, OfficialPoint, apply_official_records, build_view,
                                  daily_changes, estimate_path, required_symbols)
from tests.conftest import TODAY

IBKR = '''Statement,Header,Field Name,Field Value
Statement,Data,Title,Activity Statement
Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,Realized P/L,MTM P/L,Code
Trades,Data,Order,Stocks,USD,SPYI,"2024-01-10, 10:01:02",10,100,100.5,-1000,-1,1001,0,5,O
Trades,Data,Order,Stocks,USD,SPYI,"2025-06-02, 11:00:00","1,000",50,50,-50000,-2.5,50002.5,0,0,O
Trades,Data,Order,Stocks,USD,SPYI,"2026-11-11, 11:00:00",-4,150,150,600,-1,-400.4,198.6,0,C
Trades,Data,Order,Forex,USD,USD.ILS,"2025-06-02, 11:00:00",1000,3.0,3.0,-3000,0,,,0,
Trades,SubTotal,,Stocks,USD,SPYI,,1006,,,-50400,-4.5,,,,
Dividends,Header,Currency,Date,Description,Amount
Dividends,Data,USD,2025-06-02,SPYI(US78433H3030) Cash Dividend USD 0.50 per Share (Ordinary Dividend),5
Dividends,Data,USD,2025-06-02,NOPE(US0000000000) Cash Dividend USD 1 per Share (Ordinary Dividend),7
Dividends,Data,Total,,,12
Withholding Tax,Header,Currency,Date,Description,Amount,Code
Withholding Tax,Data,USD,2025-06-02,SPYI(US78433H3030) Cash Dividend USD 0.50 per Share - US Tax,-1.25,
Withholding Tax,Data,Total,,,-1.25,
'''


def test_number_and_date_parsing():
    assert to_decimal("1,234.50") == D("1234.50") and to_decimal("1.234,50", True) == D("1234.50")
    assert to_decimal("(12.5)") == D("-12.5") and to_decimal("₪ 10") == D(10)
    with pytest.raises(ValueError):
        to_decimal("abc")
    assert to_date("2024-01-10, 10:01:02") == date(2024, 1, 10)
    assert to_date("31/12/2025") == date(2025, 12, 31) and to_date("31.12.2025") == date(2025, 12, 31)
    assert to_date("12/31/2025", "%m/%d/%Y") == date(2025, 12, 31)
    with pytest.raises(ValueError):
        to_date("вчера")


def test_parse_ibkr_trades_dividends_withholding():
    p = parse_ibkr(IBKR)
    assert not p.errors
    assert [(t.day, t.qty, t.price, t.fee) for t in p.trades] == [
        (date(2024, 1, 10), D(10), D(100), D(1)),
        (date(2025, 6, 2), D(1000), D(50), D("2.5")),
        (date(2026, 11, 11), D(-4), D(150), D(1)),
    ]
    spyi = [d for d in p.dividends if d.symbol == "SPYI"][0]
    assert spyi.gross == D(5) and spyi.foreign_tax == D("1.25")
    assert any("Forex" in w for w in p.warnings)
    assert parse_ibkr("просто текст\nбез разделов").errors


def test_apply_import_is_idempotent_and_reports_problems(sf, market, world):
    parsed = parse_ibkr(IBKR)
    with sf() as s:
        cust = s.get(Custodian, world["cust"]["ibkr"])
        assert unknown_symbols(s, parsed) == ["NOPE"]
        rep = apply_import(s, market, cust, parsed)
        s.commit()
        assert (rep.lots, rep.sales, rep.dividends, rep.skipped) == (2, 1, 1, 0)
        assert len(rep.errors) == 1 and "NOPE" in rep.errors[0]
        lots = s.query(Lot).order_by(Lot.id).all()
        assert [(l.qty, l.qty_remaining, l.fees, l.fx_usd_ils) for l in lots] == [
            (D(10), D(6), D(1), D("3.5")), (D(1000), D(1000), D("2.5"), D("3.0"))]
        assert s.query(Disposal).one().fee == D(1)
        div = s.query(Dividend).one()
        assert div.foreign_tax == D("1.25") and div.fx_usd_ils == D("3.0")
        again = apply_import(s, market, cust, parsed)
        s.commit()
        assert (again.lots, again.sales, again.dividends, again.skipped) == (0, 0, 0, 4)
        assert s.query(Lot).count() == 2


def test_import_sell_without_holdings_is_reported_not_applied(sf, market, world):
    text = "Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,Date/Time,Quantity,T. Price,Comm/Fee\n" \
           'Trades,Data,Order,Stocks,USD,SPYI,"2025-06-02, 10:00:00",-5,100,-1\n'
    with sf() as s:
        rep = apply_import(s, market, s.get(Custodian, world["cust"]["ibkr"]), parse_ibkr(text))
        s.commit()
        assert rep.sales == 0 and "Не хватает" in rep.errors[0] and s.query(Disposal).count() == 0
        # после исправления причины та же строка импортируется
        ledger.add_lot(s, market, s.get(Asset, world["asset"]["SPYI"]), s.get(Custodian, world["cust"]["ibkr"]),
                       date(2024, 1, 10), D(5), D(90))
        rep2 = apply_import(s, market, s.get(Custodian, world["cust"]["ibkr"]), parse_ibkr(text))
        assert rep2.sales == 1


def test_parse_generic_with_mapping_hebrew_sides_and_decimal_comma():
    text = "תאריך;נייר;פעולה;כמות;שער;עמלה;סכום\n" \
           "10/01/2024;SPYI;קניה;10;100,5;1,5;\n" \
           "02/06/2025;SPYI;מכירה;4;150;2;\n" \
           "02/06/2025;SPYI;דיבידנד;;;;12,5\n" \
           "02/06/2025;SPYI;העברה;1;1;;\n"
    assert csv_headers(text) == ["תאריך", "נייר", "פעולה", "כמות", "שער", "עמלה", "סכום"]
    mapping = {"date_col": "תאריך", "symbol_col": "נייר", "side_col": "פעולה", "qty_col": "כמות",
               "price_col": "שער", "fee_col": "עמלה", "amount_col": "סכום", "decimal_comma": True,
               "default_currency": "ILS"}
    p = parse_generic(text, mapping)
    assert not p.errors
    assert [(t.qty, t.price, t.fee, t.currency) for t in p.trades] == [
        (D(10), D("100.5"), D("1.5"), "ILS"), (D(-4), D(150), D(2), "ILS")]
    assert p.dividends[0].gross == D("12.5")
    assert any("העברה" in w for w in p.warnings)
    assert parse_generic(text, {"date_col": "нет"}).errors


def test_parse_generic_signed_quantity_without_side_column():
    text = "Date,Ticker,Qty,Price\n2024-01-10,BTC,0.5,30000\n2025-06-02,BTC,-0.1,60000\n2025-06-03,BTC,x,1\n"
    p = parse_generic(text, {"date_col": "Date", "symbol_col": "Ticker", "qty_col": "Qty", "price_col": "Price"})
    assert [t.qty for t in p.trades] == [D("0.5"), D("-0.1")]
    assert len(p.errors) == 1 and "Строка 4" in p.errors[0]


# ---------- бэкап ----------

def test_backup_roundtrip_restores_everything_but_cache_and_password(sf, market, world):
    with sf() as s:
        ledger.add_lot(s, market, s.get(Asset, world["asset"]["SPYI"]), s.get(Custodian, world["cust"]["ibkr"]),
                       date(2024, 1, 10), D("10.5"), D("100.123456789"), fees=D("1.5"))
        owner = s.query(Owner).first()
        acc = PensionAccount(owner_id=owner.id, product_type="hishtalmut", fund_name="Тест", track_key="general",
                             balance_ils=D("12345.67"), balance_date=date(2026, 3, 1), fx_hedge_pct=D(0))
        acc.set_weights({"israel_equity": D(40), "foreign_equity": D(40), "israel_bonds": D(10),
                         "foreign_bonds": D(5), "cash_other": D(5)})
        s.add(acc)
        s.add(PricePoint(key="yfinance:SPYI", date=date(2026, 1, 1), close=D(50)))
        set_setting(s, "password_hash", "OLD")
        set_setting(s, "estate_warn_usd", "40000")
        s.commit()
        blob = encrypt_session(s, "correct horse battery")
    assert b"12345.67" not in blob and "Тест".encode() not in blob
    payload = decrypt_blob(blob, "correct horse battery")
    assert "price_points" not in payload["tables"] and "fx_points" not in payload["tables"]

    engine = get_engine(":memory:")
    init_db(engine)
    dst_sf = make_session_factory(engine)
    with dst_sf() as d:
        set_setting(d, "password_hash", "CURRENT")
        d.add(Custodian(name="будет удалён", kind="other", owner_id=d.query(Owner).first().id))
        d.commit()
        restore_blob(d, blob, "correct horse battery")
        d.commit()
        lot = d.query(Lot).one()
        assert (lot.qty, lot.price, lot.fees, lot.buy_date) == (D("10.5"), D("100.123456789"), D("1.5"),
                                                               date(2024, 1, 10))
        assert lot.custodian.name == "Тест-брокер США" and lot.asset.symbol == "SPYI"
        assert d.query(Custodian).filter_by(name="будет удалён").count() == 0
        assert d.query(PensionAccount).one().balance_ils == D("12345.67")
        assert get_setting(d, "password_hash") == "CURRENT"          # пароль входа остаётся текущим
        assert get_setting(d, "estate_warn_usd") == "40000"
        assert d.query(Custodian).filter_by(withholds_il_tax=True).count() == 1


def test_backup_rejects_wrong_passphrase_and_garbage(sf):
    with sf() as s:
        blob = encrypt_session(s, "one two three")
        with pytest.raises(ValueError, match="парольная фраза"):
            restore_blob(s, blob, "wrong")
        with pytest.raises(ValueError):
            restore_blob(s, b"hello", "one two three")
        with pytest.raises(ValueError):
            encrypt_session(s, "")


def test_auto_backup_once_per_day_and_prunes(sf, tmp_path):
    with sf() as s:
        first = auto_backup(s, "passphrase1", tmp_path)
        assert first is not None and first.exists()
        assert auto_backup(s, "passphrase1", tmp_path) is None       # сегодня уже есть
        for i in range(40):
            (tmp_path / f"finance-backup-auto-202501{i:02d}-000000.fbk").write_bytes(b"x")
        (first).unlink()
        auto_backup(s, "passphrase1", tmp_path)
        assert len(list(tmp_path.glob("finance-backup-auto-*.fbk"))) == 30


def test_password_hash():
    stored = hash_password("supersecret")
    assert verify_password("supersecret", stored) and not verify_password("supersecre", stored)
    assert not verify_password("supersecret", "garbage")
    with pytest.raises(ValueError):
        hash_password("short")


# ---------- пенсия ----------

CFG = load_pension_config()
SP = {"israel_equity": D(0), "foreign_equity": D(100), "israel_bonds": D(0), "foreign_bonds": D(0),
      "cash_other": D(0)}


def ser(values, start="2026-01-01"):
    return pd.Series(values, index=pd.bdate_range(start=start, periods=len(values)), dtype=float)


def test_pension_config_and_required_symbols():
    validate_weights(CFG.tracks["sp500"].preset)
    assert required_symbols(CFG, "sp500", CFG.tracks["sp500"].preset) == {"foreign_equity": "^GSPC",
                                                                         "usd_ils": "BOI:USD"}
    mixed = {"israel_equity": D(50), "foreign_equity": D(0), "israel_bonds": D(50), "foreign_bonds": D(0),
             "cash_other": D(0)}
    assert required_symbols(CFG, "general", mixed) == {"israel_equity": "^TA125.TA"}
    assert CFG.tracks["stocks"].preset is None


def test_pension_daily_change_fx_and_hedge():
    closes = {"foreign_equity": ser([100.0, 110.0]), "usd_ils": ser([3.0, 3.3])}
    (d, unhedged), = daily_changes(SP, closes, D(0))
    assert d == date(2026, 1, 2) and abs(unhedged - D("0.21")) < D("1e-9")       # 1.1 × 1.1 − 1
    (_, hedged), = daily_changes(SP, closes, D(100))
    assert abs(hedged - D("0.10")) < D("1e-9")
    (_, half), = daily_changes(SP, closes, D(50))
    assert abs(half - D("0.155")) < D("1e-9")                                    # 1.1 × 1.05 − 1
    with pytest.raises(MissingProxyError):
        daily_changes(SP, {"foreign_equity": ser([1.0, 1.1])}, D(0))


def test_pension_view_balance_and_comparison():
    closes = {"foreign_equity": ser([100.0, 110.0, 99.0]), "usd_ils": ser([3.0, 3.0, 3.0])}
    view = build_view(D(10000), date(2026, 1, 1), SP, D(100), closes, date(2026, 1, 5),
                      official=[OfficialPoint("2026-01", D("0.5"))])
    assert abs(view.balance_today - D(9900)) < D("1e-6")              # +10% затем −10%
    assert abs(view.day_change - D("-0.1")) < D("1e-9") and abs(view.day_change_ils - D(-1100)) < D("1e-6")
    assert view.last_day == date(2026, 1, 5)
    cmp = view.comparisons[0]
    assert abs(cmp.estimate - D("-0.01")) < D("1e-9") and abs(cmp.diff_pp - D("-1.5")) < D("1e-7")
    assert view.exposure_ils["foreign_equity"] == view.balance_today
    assert estimate_path(D(1000), date(2026, 1, 2), [(date(2026, 1, 2), D("0.1")),
                                                     (date(2026, 1, 5), D("-0.05"))]) == [
        (date(2026, 1, 5), D("950.00"))]


def test_pension_warnings_for_stale_weights_and_missing_bonds():
    weights = {"israel_equity": D(50), "foreign_equity": D(0), "israel_bonds": D(50), "foreign_bonds": D(0),
               "cash_other": D(0)}
    view = build_view(D(1000), date(2026, 1, 1), weights, D(0), {"israel_equity": ser([100.0, 102.0])},
                      date(2026, 1, 5), weights_as_of=date(2025, 1, 1), manual_track=True)
    assert any("45 дней" in w for w in view.warnings) and any("облигаций" in w for w in view.warnings)
    assert abs(view.balance_today - D(1010)) < D("1e-6")              # половина портфеля выросла на 2%


def test_official_records_parse_and_apply(sf):
    rec = parse_record({"REPORT_PERIOD": 202609, "MONTHLY_YIELD": 1.87, "TOTAL_ASSETS": 1000,
                        "STOCK_MARKET_EXPOSURE": 450, "FOREIGN_EXPOSURE": 400, "FOREIGN_CURRENCY_EXPOSURE": 100,
                        "AVG_ANNUAL_MANAGEMENT_FEE": 0.5})
    assert rec.period == "2026-09" and rec.return_pct == D("1.87")
    assert (rec.stock_pct, rec.foreign_pct, rec.fx_pct) == (D(45), D(40), D(10))
    assert parse_record({"REPORT_PERIOD": 202609, "MONTHLY_YIELD": None}) is None
    with sf() as s:
        acc = PensionAccount(owner_id=s.query(Owner).first().id, product_type="gemel", fund_name="Тест",
                             track_key="general", balance_ils=D(1000), balance_date=TODAY, fx_hedge_pct=D(0))
        acc.set_weights({"israel_equity": D(100), "foreign_equity": D(0), "israel_bonds": D(0),
                         "foreign_bonds": D(0), "cash_other": D(0)})
        s.add(acc)
        older = OfficialRecord("2026-08", D("0.5"), None, None, None, None, None)
        count, updated = apply_official_records(acc, [older, rec], "data.gov.il", update_weights=True)
        s.commit()
        assert (count, updated) == (2, True)
        assert acc.weights() == {"israel_equity": D(27), "foreign_equity": D(18), "israel_bonds": D(33),
                                 "foreign_bonds": D(22), "cash_other": D(0)}
        assert acc.fx_hedge_pct == D(75) and acc.weights_as_of == date(2026, 9, 30)
        assert [o.period for o in acc.official] == ["2026-08", "2026-09"]
        count2, _ = apply_official_records(acc, [rec], "data.gov.il", update_weights=False)
        s.commit()
        assert count2 == 1 and len(acc.official) == 2                 # без дублей
