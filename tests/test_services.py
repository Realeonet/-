"""Сервисы: кэш рынка, операции с лотами, оценка портфеля, налоги, дивиденды."""
from datetime import date
from decimal import Decimal as D

import pandas as pd
import pytest

from app.db import Asset, Custodian, Disposal, Lot, Transfer
from app.domain.money import GRAMS_PER_TROY_OZ
from app.services import ledger, taxes
from app.services.dividends import forecast, received, upcoming_ex_dates
from app.services.portfolio import group_positions, load_positions, totals, us_situs_by_owner, value_history
from tests.conftest import TODAY


def A(s, world, sym):
    return s.get(Asset, world["asset"][sym])


def C(s, world, key):
    return s.get(Custodian, world["cust"][key])


# ---------- рынок ----------

def test_fx_uses_last_rate_on_or_before_date_and_minor_units(market):
    assert market.fx("USD", date(2024, 1, 10)) == D("3.5")
    assert market.fx("USD", date(2024, 1, 13)) == D("3.5")      # выходной → последний опубликованный
    assert market.fx("USD", date(2024, 1, 9)) is None            # раньше первого курса
    assert market.fx("USD") == D("3.2")
    assert market.fx("ILS") == 1 and market.fx("ILA") == D("0.01")
    assert market.fx("GBp") == D("0.04")                          # пенсы: 4.0 / 100
    assert market.fx("EUR") is None


def test_refresh_fx_fetches_only_missing_range(sf, market, sources):
    sources.boi.data = {"USD": {date(2026, 11, 10): D("3.25")}, "EUR": {date(2026, 11, 10): D("3.5")}}
    market.refresh_fx(["EUR", "GBp"], date(2024, 1, 1))
    currencies, start, end = sources.boi.calls[-1]
    assert set(currencies) == {"EUR", "GBP", "USD"} and end == TODAY
    assert start == date(2023, 12, 22)                            # у EUR нет кэша → с запасом 10 дней
    assert market.fx("EUR", date(2026, 11, 10)) == D("3.5")
    assert market.fx("EUR", date(2026, 11, 9)) is None
    assert market.fx("USD", date(2026, 11, 10)) == D("3.25")


def test_quotes_history_and_manual_override(sf, market, sources, world):
    sources.yahoo.quotes = {"SPYI": (52.5, "USD")}
    sources.coingecko.prices = {"bitcoin": 70000}
    sources.metals.per_gram = {"XAG": 1.6}
    with sf() as s:
        assets = s.query(Asset).all()
        errors = market.refresh_quotes(assets)
        assert any("CSPX" in e for e in errors) and any("ETH" in e for e in errors)
        spyi, btc = A(s, world, "SPYI"), A(s, world, "BTC")
        assert market.price_info(spyi).price == D("52.5")
        assert market.price_info(btc).price == D("70000")
        assert market.price_info(A(s, world, "USD-CASH")).price == 1
        assert market.price_info(A(s, world, "ETH")) is None
        # ручная цена новее рыночной — берётся она
        spyi.manual_price, spyi.manual_price_date = D("60"), TODAY
        assert market.price_info(spyi).source == "ручной ввод"
        spyi.manual_price_date = date(2026, 1, 1)
        assert market.price_info(spyi).price == D("52.5")

        idx = pd.date_range("2026-01-01", periods=5)
        sources.yahoo.series["SPYI"] = pd.Series([50.0, 51.0, float("nan"), 52.0, 53.0], index=idx)
        assert market.refresh_history(spyi, date(2026, 1, 1)) == 4
        hist = market.asset_history(spyi)
        assert list(hist.values) == [50.0, 51.0, 52.0, 53.0]
        # повторное обновление докачивает хвост, а не всё заново
        market.refresh_history(spyi, date(2026, 1, 1))
        assert sources.yahoo.calls[-1][2] == date(2025, 12, 31)


# ---------- покупка и оценка ----------

def seed_basic(s, market, world):
    spyi, ibkr = A(s, world, "SPYI"), C(s, world, "ibkr")
    lot = ledger.add_lot(s, market, spyi, ibkr, date(2024, 1, 10), D(10), D(100), fees=D(10))
    s.commit()
    return lot


def test_add_lot_takes_official_fx_and_validates(sf, market, world):
    with sf() as s:
        lot = seed_basic(s, market, world)
        assert lot.fx_ccy_ils == D("3.5") and lot.fx_usd_ils == D("3.5") and lot.qty_remaining == D(10)
        spyi, ibkr = A(s, world, "SPYI"), C(s, world, "ibkr")
        with pytest.raises(ledger.LedgerError, match="Нет курса"):
            ledger.add_lot(s, market, spyi, ibkr, date(2020, 1, 1), D(1), D(1))
        with pytest.raises(ledger.LedgerError, match="в будущем"):
            ledger.add_lot(s, market, spyi, ibkr, date(2030, 1, 1), D(1), D(1))
        with pytest.raises(ledger.LedgerError):
            ledger.add_lot(s, market, spyi, ibkr, date(2024, 1, 10), D(0), D(1))
        # ручной курс разрешает дату без официального
        manual = ledger.add_lot(s, market, spyi, ibkr, date(2020, 1, 1), D(1), D(1), fx_ccy_ils=D("3.45"),
                                fx_usd_ils=D("3.45"))
        assert manual.fx_ccy_ils == D("3.45")


def test_positions_valuation_in_usd_and_ils(sf, market, sources, world):
    sources.yahoo.quotes = {"SPYI": (150, "USD"), "VUSA.L": (8000, "GBp")}
    sources.metals.per_gram = {"XAG": 1.6}
    with sf() as s:
        seed_basic(s, market, world)
        ledger.add_lot(s, market, A(s, world, "VUSA"), C(s, world, "il"), date(2025, 6, 2), D(5), D(7000),
                       fx_ccy_ils=D("0.045"))
        grams = D(10) * GRAMS_PER_TROY_OZ
        ledger.add_lot(s, market, A(s, world, "SILVER"), C(s, world, "safe"), date(2025, 6, 2), grams, D("1.0"),
                       fees=D("56"), vat=D("56"), purity=D("0.999"), form="монеты")
        s.commit()
        market.refresh_quotes(s.query(Asset).all())
        pos = {p.symbol: p for p in load_positions(s, market, TODAY)}

    spyi = pos["SPYI"]
    assert spyi.value_usd == D("1500") and spyi.value_ils == D("4800.0")
    assert spyi.cost_ils == D("3535.0") and spyi.cost_usd == D("1010")
    assert spyi.gain_usd == D("490") and spyi.gain_ils == D("1265.0")
    assert spyi.us_situs and spyi.owner == "Леонид"

    vusa = pos["VUSA"]                                   # 5 × 8000 пенсов × 0.04 ₪/пенс = 1600 ₪
    assert vusa.value_ils == D("1600.00") and vusa.value_usd == D("500")
    assert vusa.cost_ils == D("1575.000") and not vusa.us_situs

    silver = pos["SILVER"]                               # дисконт −5% к споту; НДС в себестоимости
    assert silver.value_usd == grams * D("1.6") * D("0.95")
    assert silver.cost_usd == grams * D("1.0") + D("56")
    assert silver.self_custody

    by_class = {g.name: g for g in group_positions(pos.values(), lambda p: p.class_label)}
    assert by_class["ETF / фонды"].value_usd == D("2000")
    assert totals(pos.values()).lots == 3
    assert us_situs_by_owner(pos.values()) == {"Леонид": {"Тест-брокер США": D("1500")}}


def test_unpriced_lot_is_excluded_from_totals_and_counted(sf, market, world):
    with sf() as s:
        seed_basic(s, market, world)
        t = totals(load_positions(s, market, TODAY))
    assert t.value_usd == 0 and t.unpriced == 1 and t.lots == 1


# ---------- продажа ----------

def test_sell_fifo_across_lots_and_realized_tax(sf, market, world):
    with sf() as s:
        spyi, ibkr = A(s, world, "SPYI"), C(s, world, "ibkr")
        seed_basic(s, market, world)                                             # 10 @ 100 (+10), курс 3.5
        ledger.add_lot(s, market, spyi, ibkr, date(2025, 6, 2), D(10), D(120))  # курс 3.0
        s.commit()
        disposals = ledger.sell(s, market, spyi, ibkr.id, TODAY, D(150), qty=D(14), fee=D(7))
        s.commit()
        assert [(d.qty, d.fee) for d in disposals] == [(D(10), D(5)), (D(4), D(2))]
        lots = s.query(Lot).order_by(Lot.id).all()
        assert [l.qty_remaining for l in lots] == [D(0), D(6)]
        for l in lots:
            assert l.qty == l.qty_remaining + sum(d.qty for d in l.disposals)

        rows = taxes.realized(s, D("0.25"), "nominal", 2026)
        by_lot = {r.disposal.lot_id: r for r in rows}
        g1 = by_lot[lots[0].id].gain          # выручка (1500−5)×3.2=4784; себестоимость 1010×3.5=3535
        assert g1.gain_ils_nominal == D("1249.0") and g1.gain_usd == D("485")
        g2 = by_lot[lots[1].id].gain          # (600−2)×3.2=1913.6; 480×3.0=1440
        assert g2.gain_ils_nominal == D("473.6")
        summary = taxes.summary_by_owner(rows, D("0.25"))["Леонид"]
        assert summary.net_ils == D("1722.6") and summary.tax_ils == D("430.650")
        assert summary.to_declare_ils == D("430.650")
        assert all(r.status == "декларировать самому" for r in rows)
        assert taxes.years_with_activity(s) == [2026]

        with pytest.raises(ledger.LedgerError, match="Не хватает"):
            ledger.sell(s, market, spyi, ibkr.id, TODAY, D(150), qty=D(7))


def test_sell_manual_pick_and_undo(sf, market, world):
    with sf() as s:
        spyi, ibkr = A(s, world, "SPYI"), C(s, world, "ibkr")
        first = seed_basic(s, market, world)
        second = ledger.add_lot(s, market, spyi, ibkr, date(2025, 6, 2), D(10), D(120))
        s.commit()
        ds = ledger.sell(s, market, spyi, ibkr.id, TODAY, D(150), picks={second.id: D(3)}, il_tax_withheld=D(30))
        s.commit()
        assert first.qty_remaining == D(10) and second.qty_remaining == D(7)
        assert ds[0].il_tax_withheld == D(30)
        assert taxes.realized(s, D("0.25"), "nominal")[0].status == "налог удерживает брокер"
        with pytest.raises(ledger.LedgerError, match="раньше даты покупки"):
            ledger.sell(s, market, spyi, ibkr.id, date(2025, 1, 1), D(150), picks={second.id: D(1)},
                        fx_ccy_ils=D(3), fx_usd_ils=D(3))
        s.rollback()
        assert ledger.delete_disposal_group(s, ds[0].group_id) == 1
        s.commit()
        assert second.qty_remaining == D(10) and s.query(Disposal).count() == 0
        with pytest.raises(ledger.LedgerError):
            ledger.delete_lot(s, 999)
        ledger.delete_lot(s, second.id)
        s.commit()
        assert s.query(Lot).count() == 1


# ---------- перевод ----------

def test_transfer_keeps_date_cost_and_is_not_a_sale(sf, market, sources, world):
    sources.coingecko.prices = {"bitcoin": 70000}
    with sf() as s:
        btc, exch, cold = A(s, world, "BTC"), C(s, world, "exch"), C(s, world, "cold")
        ledger.add_lot(s, market, btc, exch, date(2024, 1, 10), D(1), D(30000), fees=D(30))
        s.commit()
        tr = ledger.transfer(s, market, btc, exch, cold, date(2025, 6, 2), D("0.5"), D("0.4995"))
        s.commit()
        src, dst = s.query(Lot).order_by(Lot.id).all()
        assert (src.qty, src.qty_remaining, src.fees) == (D("0.5"), D("0.5"), D("15"))
        assert dst.custodian_id == cold.id and dst.buy_date == date(2024, 1, 10)
        assert dst.price == D(30000) and dst.fx_usd_ils == D("3.5") and dst.kind == "transfer_in"
        assert dst.qty == D("0.4995") and dst.fees == D("30.0000") and dst.origin_lot_id == src.id
        assert s.query(Disposal).count() == 0                         # перевод — не продажа
        assert taxes.realized(s, D("0.25"), "nominal") == []

        market.refresh_quotes([btc])
        groups = {g.name: g for g in group_positions(load_positions(s, market, TODAY), lambda p: p.custodian)}
        assert groups["Тест-кошелёк"].value_usd == D("0.4995") * 70000
        # себестоимость не потеряна: 15015 + 15015
        assert groups["Тест-кошелёк"].cost_usd + groups["Тест-биржа"].cost_usd == D("30030")

        ledger.undo_transfer(s, tr.id)
        s.commit()
        only = s.query(Lot).one()
        assert (only.qty, only.qty_remaining, only.fees) == (D("1.0"), D("1.0"), D("30"))
        assert s.query(Transfer).count() == 0


def test_transfer_money_fee_goes_to_cost_and_guards(sf, market, world):
    with sf() as s:
        spyi, ibkr, il = A(s, world, "SPYI"), C(s, world, "ibkr"), C(s, world, "il")
        seed_basic(s, market, world)
        tr = ledger.transfer(s, market, spyi, ibkr, il, date(2025, 6, 2), D(10), fee_amount=D(90),
                             fee_currency="ILS")
        s.commit()
        dst = s.query(Lot).filter(Lot.custodian_id == il.id).one()
        assert dst.fees == D(10) + D(30)                 # 90 ₪ / 3.0 = $30 добавлено к себестоимости
        with pytest.raises(ledger.LedgerError, match="совпадают"):
            ledger.transfer(s, market, spyi, il, il, TODAY, D(1))
        with pytest.raises(ledger.LedgerError, match="Не хватает"):
            ledger.transfer(s, market, spyi, ibkr, il, TODAY, D(1))
        s.rollback()
        ledger.sell(s, market, spyi, il.id, TODAY, D(150), qty=D(1))
        s.commit()
        with pytest.raises(ledger.LedgerError, match="отмена невозможна"):
            ledger.undo_transfer(s, tr.id)


# ---------- обмен крипты ----------

def test_swap_is_taxable_and_sets_new_cost_basis(sf, market, world):
    with sf() as s:
        btc, eth, exch = A(s, world, "BTC"), A(s, world, "ETH"), C(s, world, "exch")
        ledger.add_lot(s, market, btc, exch, date(2024, 1, 10), D(1), D(30000))
        s.commit()
        disposals, new_lot = ledger.swap(s, market, btc, eth, exch, date(2025, 6, 2), D("0.5"), D(10),
                                         value_usd=D(20000), fee_usd=D(20))
        s.commit()
        assert disposals[0].kind == "swap" and disposals[0].price == D(40000) and disposals[0].fee == D(20)
        assert new_lot.kind == "swap_in" and new_lot.price == D(1998) and new_lot.qty == D(10)
        row = taxes.realized(s, D("0.25"), "nominal")[0]
        # выручка 19980×3.0=59940; себестоимость 15000×3.5=52500
        assert row.gain.gain_ils_nominal == D("7440.0") and row.gain.tax_ils == D("1860.000")
        assert row.gain.gain_usd == D("4980")
        with pytest.raises(ledger.LedgerError):
            ledger.swap(s, market, btc, btc, exch, TODAY, D("0.1"), D(1), D(100))


# ---------- дивиденды ----------

def test_dividend_forecast_calendar_and_received(sf, market, sources, world):
    sources.yahoo.quotes = {"SPYI": (50, "USD"), "CSPX.L": (800, "USD")}
    monthly = pd.date_range("2025-12-17", periods=12, freq="28D")
    sources.yahoo.divs = {"SPYI": pd.Series([0.5] * 12, index=monthly)}
    with sf() as s:
        spyi, cspx, ibkr = A(s, world, "SPYI"), A(s, world, "CSPX"), C(s, world, "ibkr")
        seed_basic(s, market, world)
        ledger.add_lot(s, market, cspx, ibkr, date(2025, 6, 2), D(2), D(700))
        s.commit()
        market.refresh_quotes([spyi, cspx])
        market.refresh_dividends(spyi)
        positions = load_positions(s, market, TODAY)
        assets = {a.id: a for a in s.query(Asset).all()}
        fc = forecast(assets, positions, market, TODAY)
        assert [f.symbol for f in fc] == ["SPYI"]                 # накопительный CSPX выплат не даёт
        f = fc[0]
        assert f.per_unit_year == D("6.0") and f.gross_year_usd == D("60.0") and f.gross_year_ils == D("192.00")
        assert f.next_ex.estimated and f.next_ex.day == date(2026, 11, 18)
        assert f.gross_month_usd == D("5.0")                      # одна выплата в ближайшие 30 дней
        spyi.next_ex_date = date(2026, 11, 20)
        ex = upcoming_ex_dates(spyi, market, TODAY, 60)
        assert not ex[0].estimated and ex[0].day == date(2026, 11, 20) and ex[1].day == date(2026, 12, 16)

        ledger.add_dividend(s, market, spyi, ibkr, date(2025, 6, 2), D(100), foreign_tax=D(25))
        ledger.add_dividend(s, market, cspx, ibkr, date(2025, 6, 3), D(100))
        s.commit()
        rec = {r.symbol: r for r in received(s, D("0.25"), 2025)}
        assert rec["SPYI"].to_pay_ils == 0 and rec["SPYI"].status == "налог удержан полностью"
        assert rec["CSPX"].to_pay_ils == D("75.00") and "декларировать" in rec["CSPX"].status
        assert rec["CSPX"].net_ils == D("225.00")
        with pytest.raises(ledger.LedgerError):
            ledger.add_dividend(s, market, spyi, ibkr, TODAY, D(10), foreign_tax=D(11))


# ---------- история стоимости ----------

def test_value_history_reconstructs_from_lots(sf, market, sources, world):
    idx = pd.date_range("2026-11-01", "2026-11-11")
    sources.yahoo.series["SPYI"] = pd.Series(100.0, index=idx)
    sources.yahoo.quotes = {"SPYI": (100, "USD")}
    with sf() as s:
        spyi, ibkr = A(s, world, "SPYI"), C(s, world, "ibkr")
        seed_basic(s, market, world)
        ledger.sell(s, market, spyi, ibkr.id, date(2026, 11, 11), D(100), qty=D(4))
        s.commit()
        market.refresh_quotes([spyi])
        market.refresh_history(spyi, date(2026, 11, 1))
        frame = value_history(s, market, date(2026, 11, 9), date(2026, 11, 11))
    assert list(frame["total_usd"].round(6)) == [1000.0, 1000.0, 600.0]
    assert frame["total_ils"].iloc[-1] == pytest.approx(600 * 3.2)
