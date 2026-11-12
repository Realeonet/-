"""Общие фикстуры: база в памяти и подставные источники данных (тесты не ходят в сеть)."""
from datetime import date, datetime
from decimal import Decimal as D

import pandas as pd
import pytest

from app.adapters.base import LiveQuote, SourceError
from app.db import Asset, Custodian, Owner, get_engine, init_db, make_session_factory
from app.services.market import MarketData, Sources

TODAY = date(2026, 11, 11)


class FakeYahoo:
    def __init__(self):
        self.quotes = {}      # ticker -> (price, currency)
        self.series = {}      # ticker -> pd.Series
        self.divs = {}        # ticker -> pd.Series
        self.calls = []

    def quote(self, symbol):
        self.calls.append(("quote", symbol))
        if symbol not in self.quotes:
            raise SourceError(f"нет {symbol}")
        price, ccy = self.quotes[symbol]
        return LiveQuote(D(str(price)), ccy, datetime(2026, 11, 10), "Yahoo Finance")

    def history(self, symbol, start, end, adjusted=False):
        self.calls.append(("history", symbol, start, end, adjusted))
        s = self.series.get(symbol, pd.Series(dtype=float))
        return s[(s.index >= pd.Timestamp(start)) & (s.index <= pd.Timestamp(end))]

    def dividends(self, symbol):
        return self.divs.get(symbol, pd.Series(dtype=float))


class FakeCoinGecko:
    def __init__(self):
        self.prices = {}

    def quotes(self, ids):
        return {i: LiveQuote(D(str(self.prices[i])), "USD", datetime(2026, 11, 11, 4), "CoinGecko")
                for i in ids if i in self.prices}

    def history(self, coin_id, days=365):
        return pd.Series(dtype=float)


class FakeMetals:
    def __init__(self):
        self.per_gram = {}

    def quote(self, code):
        if code not in self.per_gram:
            raise SourceError(f"нет {code}")
        return LiveQuote(D(str(self.per_gram[code])), "USD", datetime(2026, 11, 11, 4), "спот (тест)")

    def history(self, code, start, end):
        return pd.Series(dtype=float)


class FakeBoi:
    def __init__(self):
        self.data = {}        # currency -> {date: Decimal}
        self.calls = []

    def rates(self, currencies, start, end):
        self.calls.append((tuple(currencies), start, end))
        return {c: {d: r for d, r in self.data.get(c, {}).items() if start <= d <= end} for c in currencies}


@pytest.fixture
def sf():
    engine = get_engine(":memory:")
    init_db(engine)
    return make_session_factory(engine)


@pytest.fixture
def sources():
    return Sources(yahoo=FakeYahoo(), coingecko=FakeCoinGecko(), boi=FakeBoi(), metals=FakeMetals())


@pytest.fixture
def market(sf, sources):
    m = MarketData(sf, sources, today=lambda: TODAY)
    for day, rate in [(date(2024, 1, 10), "3.5"), (date(2025, 6, 2), "3.0"), (date(2026, 11, 11), "3.2")]:
        m.set_manual_fx("USD", day, D(rate))
    m.set_manual_fx("GBP", date(2026, 11, 11), D("4.0"))
    return m


@pytest.fixture
def world(sf):
    """Минимальный мир: два владельца, три места хранения, несколько активов (вымышленные данные)."""
    with sf() as s:
        leonid = s.query(Owner).filter_by(name="Леонид").one()
        luba = s.query(Owner).filter_by(name="Люба").one()
        ibkr = Custodian(name="Тест-брокер США", kind="broker", owner_id=leonid.id, country="US", currency="USD",
                         fee_trade=D("2.5"))
        il = Custodian(name="Тест-брокер IL", kind="broker", owner_id=luba.id, country="IL", currency="ILS",
                       withholds_il_tax=True, free_trades_per_month=10, fee_trade=D(3))
        exch = Custodian(name="Тест-биржа", kind="exchange", owner_id=leonid.id, currency="USD")
        cold = Custodian(name="Тест-кошелёк", kind="self_custody", owner_id=leonid.id, wallet_type="аппаратный",
                         network="Bitcoin")
        safe = Custodian(name="Тест-сейф", kind="physical", owner_id=leonid.id, location_note="дома")
        s.add_all([ibkr, il, exch, cold, safe])
        assets = [
            Asset(symbol="SPYI", name="US income ETF", asset_class="etf", currency="USD", domicile="US",
                  dist_type="dist", price_source="yfinance", source_id="SPYI"),
            Asset(symbol="CSPX", name="UCITS acc", asset_class="etf", currency="USD", domicile="IE",
                  dist_type="acc", price_source="yfinance", source_id="CSPX.L"),
            Asset(symbol="VUSA", name="UCITS dist GBp", asset_class="etf", currency="GBp", domicile="IE",
                  dist_type="dist", price_source="yfinance", source_id="VUSA.L"),
            Asset(symbol="BTC", name="Bitcoin", asset_class="crypto", currency="USD", price_source="coingecko",
                  source_id="bitcoin", history_ticker="BTC-USD"),
            Asset(symbol="ETH", name="Ethereum", asset_class="crypto", currency="USD", price_source="coingecko",
                  source_id="ethereum"),
            Asset(symbol="SILVER", name="Серебро", asset_class="metal", currency="USD", price_source="metal",
                  source_id="XAG", valuation_adj_pct=D("-5")),
            Asset(symbol="USD-CASH", name="Наличные доллары", asset_class="cash", currency="USD",
                  price_source="cash"),
        ]
        s.add_all(assets)
        s.commit()
        return {
            "owners": {"leonid": leonid.id, "luba": luba.id},
            "cust": {"ibkr": ibkr.id, "il": il.id, "exch": exch.id, "cold": cold.id, "safe": safe.id},
            "asset": {a.symbol: a.id for a in assets},
        }
