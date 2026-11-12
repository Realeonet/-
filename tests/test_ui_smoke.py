"""Дымовые тесты интерфейса: каждая страница открывается на заполненной базе без сети,
основные формы (новое место, покупка, продажа, вход) действительно меняют данные."""
from datetime import date, datetime
from decimal import Decimal as D
from pathlib import Path

import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from app.config import load_settings
from app.db import (Asset, BalancePoint, Custodian, Disposal, Lot, Owner, PensionAccount, PricePoint, Quote,
                    get_engine, get_setting, init_db, make_session_factory)
from app.services import ledger
from app.services.market import MarketData

APP = Path(__file__).resolve().parent.parent / "streamlit_app.py"

PAGES = [
    ("p_capital", "render"), ("p_custodians", "render"), ("p_ops", "render"), ("p_pension", "render"),
    ("p_income", "render_dividends"), ("p_income", "render_taxes"), ("p_rules", "render_estate"),
    ("p_rules", "render_minkin"), ("p_analysis", "render_compare"), ("p_rules", "render_fees"),
    ("p_rules", "render_debt"), ("p_analysis", "render_sandbox"), ("p_family", "render"),
    ("p_import", "render"), ("p_settings", "render"),
]


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "bk"))
    monkeypatch.setenv("FINANCE_OFFLINE", "1")
    st.cache_resource.clear()
    yield tmp_path
    st.cache_resource.clear()


def seed(data_dir):
    engine = get_engine(load_settings().db_path)
    init_db(engine)
    sf = make_session_factory(engine)
    m = MarketData(sf)
    m.set_manual_fx("USD", date(2024, 1, 1), D("3.6"))
    m.set_manual_fx("USD", date(2025, 6, 1), D("3.2"))
    with sf() as s:
        me, wife = s.query(Owner).order_by(Owner.id).all()
        broker = Custodian(name="Тест-брокер", kind="broker", owner_id=me.id, country="US", fee_trade=D("2.5"))
        wallet = Custodian(name="Тест-кошелёк", kind="self_custody", owner_id=me.id, location_note="дома")
        s.add_all([broker, wallet])
        spyi = Asset(symbol="SPYI", name="Income ETF", asset_class="etf", currency="USD", domicile="US",
                     dist_type="dist", price_source="yfinance", source_id="SPYI", manual_yield_pct=D(10))
        jepi = Asset(symbol="JEPI", name="Income ETF 2", asset_class="etf", currency="USD", domicile="US",
                     dist_type="dist", price_source="yfinance", source_id="JEPI")
        btc = Asset(symbol="BTC", name="Bitcoin", asset_class="crypto", currency="USD", price_source="coingecko",
                    source_id="bitcoin", history_ticker="BTC-USD")
        eth = Asset(symbol="ETH", name="Ethereum", asset_class="crypto", currency="USD", price_source="coingecko",
                    source_id="ethereum")
        s.add_all([spyi, jepi, btc, eth])
        s.flush()
        now = datetime.now()
        for a, price in ((spyi, 50), (jepi, 58), (btc, 70000), (eth, 2000)):
            s.add(Quote(key=a.price_key, price=D(price), currency="USD", as_of=now, source="тест"))
        idx = pd.bdate_range(end=date.today(), periods=260)
        for i, day in enumerate(idx):
            s.add(PricePoint(key=spyi.price_key, date=day.date(), close=D(45) + D(i) / 50))
            s.add(PricePoint(key="yfinance:BTC-USD", date=day.date(), close=D(60000) + D(i) * 40))
            s.add(PricePoint(key="yfinance:^GSPC", date=day.date(), close=D(6000) + D(i) * 5))
        ledger.add_lot(s, m, spyi, broker, date(2024, 1, 10), D(1000), D(48), fees=D("2.5"))
        ledger.add_lot(s, m, spyi, broker, date(2025, 6, 2), D(200), D(51))
        ledger.add_lot(s, m, jepi, broker, date(2025, 6, 2), D(100), D(55))
        ledger.add_lot(s, m, btc, wallet, date(2024, 1, 10), D("0.5"), D(40000))
        ledger.sell(s, m, jepi, broker.id, date(2025, 6, 3), D(57), qty=D(10))
        ledger.add_dividend(s, m, spyi, broker, date(2025, 6, 5), D(100), foreign_tax=D(25))
        acc = PensionAccount(owner_id=wife.id, product_type="hishtalmut", fund_name="Тест", track_key="sp500",
                             balance_ils=D(50000), balance_date=idx[100].date(), fx_hedge_pct=D(0))
        acc.set_weights({"israel_equity": D(0), "foreign_equity": D(100), "israel_bonds": D(0),
                         "foreign_bonds": D(0), "cash_other": D(0)})
        s.add(acc)
        s.flush()
        s.add(BalancePoint(account_id=acc.id, date=acc.balance_date, balance_ils=D(50000), source="report"))
        s.commit()
    return sf


def page(module: str, fn: str) -> AppTest:
    return AppTest.from_string(f"from app.ui import {module}\n{module}.{fn}()\n", default_timeout=60)


@pytest.mark.parametrize("module,fn", PAGES)
def test_page_renders_on_seeded_db(data_dir, module, fn):
    seed(data_dir)
    at = page(module, fn).run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.title, "страница должна иметь заголовок"


@pytest.mark.parametrize("module,fn", PAGES)
def test_page_renders_on_empty_db(data_dir, module, fn):
    at = page(module, fn).run()
    assert not at.exception, [e.value for e in at.exception]


def test_capital_shows_totals(data_dir):
    seed(data_dir)
    at = page("p_capital", "render").run()
    metrics = {m.label: m.value for m in at.metric}
    # 1200 × 50 + 90 × 58 + 0.5 × 70000 = 100 220 $; пенсия оценивается отдельно в ₪
    assert metrics["Капитал, $"].startswith("1")
    at.toggle[0].set_value(False).run()
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Капитал, $"] == "100 220.00 $"
    assert metrics["Капитал, ₪"] == "320 704.00 ₪"


def test_create_custodian_via_form(data_dir):
    sf = seed(data_dir)
    at = page("p_custodians", "render").run()
    at.text_input(key="new_c_name").input("Новый брокер")
    next(b for b in at.button if b.key and "new_c_form" in b.key).click().run()
    assert not at.exception
    with sf() as s:
        assert s.query(Custodian).filter_by(name="Новый брокер").count() == 1


def test_buy_then_sell_via_forms(data_dir):
    sf = seed(data_dir)
    at = page("p_ops", "render").run()
    at.text_input(key="buy_qty").input("3")
    at.text_input(key="buy_price").input("10.5")
    at.button(key="buy_submit").click().run()
    assert not at.exception and not at.error, [e.value for e in at.error]
    with sf() as s:
        lot = s.query(Lot).order_by(Lot.id.desc()).first()
        assert (lot.qty, lot.price, lot.buy_date, lot.fx_usd_ils) == (D(3), D("10.5"), date.today(), D("3.2"))
        before = s.query(Disposal).count()
    at = page("p_ops", "render").run()
    at.text_input(key="sell_qty").input("2")
    at.run()
    assert any("Налогооблагаемая" in m.label for m in at.metric)      # предварительный расчёт показан
    at.button(key="sell_submit").click().run()
    assert not at.exception and not at.error, [e.value for e in at.error]
    with sf() as s:
        assert s.query(Disposal).count() == before + 1


def test_sell_more_than_held_is_rejected(data_dir):
    sf = seed(data_dir)
    at = page("p_ops", "render").run()
    at.text_input(key="sell_qty").input("999999")
    at.button(key="sell_submit").click().run()
    assert any("Не хватает" in e.value for e in at.error)
    with sf() as s:
        assert s.query(Disposal).count() == 1


def test_first_run_password_and_login(data_dir):
    at = AppTest.from_file(str(APP), default_timeout=60).run()
    assert "Первый запуск" in at.title[0].value
    at.text_input[0].input("short")
    at.text_input[1].input("short")
    at.button[0].click().run()
    assert at.error and "auth" not in at.session_state
    at.text_input[0].input("long-enough-1")
    at.text_input[1].input("long-enough-1")
    at.button[0].click().run()
    assert not at.exception and at.session_state["auth"] is True
    sf = make_session_factory(get_engine(load_settings().db_path))
    with sf() as s:
        stored = get_setting(s, "password_hash")
    assert stored and "long-enough-1" not in stored
    assert list((data_dir / "bk").glob("finance-backup-auto-*.fbk"))    # автобэкап при входе

    fresh = AppTest.from_file(str(APP), default_timeout=60).run()
    assert fresh.title[0].value == "📊 Мой капитал"
    fresh.text_input[0].input("long-enough-1")
    fresh.button[0].click().run()
    assert not fresh.exception and fresh.session_state["auth"] is True
