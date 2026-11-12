"""Рыночные данные с локальным кэшем в SQLite.

Чтение (цены, курсы, история) идёт только из кэша — интерфейс работает и без интернета.
Обновление (refresh_*) ходит в источники через адаптеры и складывает результат в кэш.
"""

from __future__ import annotations

import bisect
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

import pandas as pd
from sqlalchemy import func
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, sessionmaker

from app.adapters.base import LiveQuote, SourceError
from app.adapters.boi import SUPPORTED as BOI_SUPPORTED
from app.adapters.boi import BoiSource
from app.adapters.coingecko import MAX_FREE_DAYS, CoinGeckoSource
from app.adapters.metals import MetalsSource
from app.adapters.yahoo import YahooSource
from app.db import Asset, DivPoint, FxPoint, PricePoint, Quote, get_setting, set_setting
from app.domain.money import ONE, D, from_market_float, major_currency

STALE_DAYS = 5


@dataclass(frozen=True)
class PriceInfo:
    price: Decimal  # за единицу актива (для металлов — за грамм чистого металла)
    currency: str  # валюта котировки, может быть GBp / ILA
    as_of: date
    source: str
    stale: bool


@dataclass
class Sources:
    yahoo: YahooSource = field(default_factory=YahooSource)
    coingecko: CoinGeckoSource = field(default_factory=CoinGeckoSource)
    boi: BoiSource = field(default_factory=BoiSource)
    metals: MetalsSource | None = None

    def __post_init__(self) -> None:
        if self.metals is None:
            self.metals = MetalsSource(self.yahoo)


class MarketData:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        sources: Sources | None = None,
        today: Callable[[], date] = date.today,
    ) -> None:
        self._sf = session_factory
        self.sources = sources or Sources()
        self._today = today
        self._fx_cache: dict[str, tuple[list[date], list[Decimal]]] | None = None

    # ================= чтение из кэша =================

    def _load_fx(self) -> dict[str, tuple[list[date], list[Decimal]]]:
        if self._fx_cache is None:
            cache: dict[str, tuple[list[date], list[Decimal]]] = {}
            with self._sf() as s:
                rows = s.query(FxPoint).order_by(FxPoint.currency, FxPoint.date).all()
            for row in rows:
                dates, rates = cache.setdefault(row.currency, ([], []))
                dates.append(row.date)
                rates.append(row.rate)
            self._fx_cache = cache
        return self._fx_cache

    def fx(self, currency: str, day: date | None = None) -> Decimal | None:
        """₪ за 1 единицу валюты котировки: последний официальный курс на дату или раньше.
        Понимает пенсы (GBp) и агороты (ILA). None — если курса в кэше нет."""
        major, factor = major_currency(currency)
        if major == "ILS":
            return factor
        dates, rates = self._load_fx().get(major, ([], []))
        if not dates:
            return None
        pos = bisect.bisect_right(dates, day or self._today()) - 1
        if pos < 0:
            return None
        return rates[pos] * factor

    def fx_date(self, currency: str, day: date | None = None) -> date | None:
        major, _ = major_currency(currency)
        dates, _rates = self._load_fx().get(major, ([], []))
        pos = bisect.bisect_right(dates, day or self._today()) - 1
        return dates[pos] if pos >= 0 else None

    def usd_ils(self, day: date | None = None) -> Decimal | None:
        return self.fx("USD", day)

    def fx_series(self, currency: str) -> pd.Series:
        major, factor = major_currency(currency)
        dates, rates = self._load_fx().get(major, ([], []))
        return pd.Series([float(r * factor) for r in rates], index=pd.to_datetime(dates), dtype=float)

    def price_info(self, asset: Asset) -> PriceInfo | None:
        today = self._today()
        if asset.price_source == "cash":
            return PriceInfo(ONE, asset.currency, today, "наличные", False)
        manual = None
        if asset.manual_price is not None:
            as_of = asset.manual_price_date or today
            manual = PriceInfo(asset.manual_price, asset.currency, as_of, "ручной ввод", False)
        if asset.price_source == "manual":
            return manual
        with self._sf() as s:
            quote = s.get(Quote, asset.price_key)
            last = None
            if quote is None:
                last = (
                    s.query(PricePoint)
                    .filter(PricePoint.key == asset.price_key)
                    .order_by(PricePoint.date.desc())
                    .first()
                )
        if quote is not None:
            as_of = quote.as_of.date()
            info = PriceInfo(
                quote.price, quote.currency or asset.currency, as_of, quote.source,
                (today - as_of).days > STALE_DAYS,
            )
        elif last is not None:
            info = PriceInfo(
                last.close, asset.currency, last.date, "история (кэш)", (today - last.date).days > STALE_DAYS
            )
        else:
            return manual
        # Ручная цена новее рыночной — значит пользователь сознательно её переопределил.
        if manual is not None and manual.as_of > info.as_of:
            return manual
        return info

    def history(self, key: str, start: date | None = None, end: date | None = None) -> pd.Series:
        with self._sf() as s:
            q = s.query(PricePoint.date, PricePoint.close).filter(PricePoint.key == key)
            if start:
                q = q.filter(PricePoint.date >= start)
            if end:
                q = q.filter(PricePoint.date <= end)
            rows = q.order_by(PricePoint.date).all()
        if not rows:
            return pd.Series(dtype=float)
        return pd.Series([float(r[1]) for r in rows], index=pd.to_datetime([r[0] for r in rows]), dtype=float)

    def asset_history(self, asset: Asset, start: date | None = None, adjusted: bool = False) -> pd.Series:
        return self.history(self.history_key(asset, adjusted), start)

    def dividends(self, asset: Asset) -> pd.Series:
        with self._sf() as s:
            rows = (
                s.query(DivPoint.ex_date, DivPoint.amount)
                .filter(DivPoint.key == asset.price_key)
                .order_by(DivPoint.ex_date)
                .all()
            )
        if not rows:
            return pd.Series(dtype=float)
        return pd.Series([float(r[1]) for r in rows], index=pd.to_datetime([r[0] for r in rows]), dtype=float)

    @staticmethod
    def history_key(asset: Asset, adjusted: bool = False) -> str:
        return ("adj:" if adjusted else "") + asset.price_key

    def last_refresh(self, what: str = "quotes") -> datetime | None:
        with self._sf() as s:
            raw = get_setting(s, f"refreshed:{what}")
        return datetime.fromisoformat(raw) if raw else None

    # ================= запись в кэш =================

    def _mark(self, what: str) -> None:
        with self._sf() as s:
            set_setting(s, f"refreshed:{what}", datetime.now().isoformat(timespec="seconds"))
            s.commit()

    def _store_quote(self, key: str, quote: LiveQuote) -> None:
        with self._sf() as s:
            row = s.get(Quote, key)
            if row is None:
                s.add(Quote(key=key, price=quote.price, currency=quote.currency, as_of=quote.as_of,
                            source=quote.source))
            else:
                row.price, row.currency, row.as_of, row.source = (
                    quote.price, quote.currency, quote.as_of, quote.source
                )
            s.commit()

    def _store_series(self, key: str, series: pd.Series) -> int:
        series = series.dropna()
        if series.empty:
            return 0
        rows = [
            {"key": key, "date": ts.date(), "close": from_market_float(v)} for ts, v in series.items() if v > 0
        ]
        with self._sf() as s:
            for i in range(0, len(rows), 400):
                stmt = sqlite_insert(PricePoint).values(rows[i : i + 400])
                s.execute(
                    stmt.on_conflict_do_update(index_elements=["key", "date"], set_={"close": stmt.excluded.close})
                )
            s.commit()
        return len(rows)

    def set_manual_fx(self, currency: str, day: date, rate: Decimal) -> None:
        """Ручной ввод курса (если БИ недоступен)."""
        major, _ = major_currency(currency)
        with self._sf() as s:
            row = s.get(FxPoint, (major, day))
            if row is None:
                s.add(FxPoint(currency=major, date=day, rate=rate, source="manual"))
            else:
                row.rate, row.source = rate, "manual"
            s.commit()
        self._fx_cache = None

    # ================= обновление из сети =================

    def refresh_fx(self, currencies: Iterable[str], since: date) -> None:
        """Курсы БИ для валют с даты since по сегодня (докачивает только недостающее)."""
        majors = sorted({major_currency(c)[0] for c in currencies} | {"USD"})
        # валюты, которых нет у Банка Израиля, пропускаем: для них курс вводится вручную
        majors = [c for c in majors if c != "ILS" and c in BOI_SUPPORTED]
        today = self._today()
        with self._sf() as s:
            bounds = {
                c: s.query(func.min(FxPoint.date), func.max(FxPoint.date))
                .filter(FxPoint.currency == c, FxPoint.source == "boi")  # ручные курсы не считаются покрытием
                .one()
                for c in majors
            }
        start = today
        for c in majors:
            first, last = bounds[c]
            if first is None or first > since + timedelta(days=7):
                need = since - timedelta(days=10)  # запас: на дату покупки курс мог быть «за пятницу»
            else:
                need = last - timedelta(days=3)
            start = min(start, need)
        data = self.sources.boi.rates(majors, start, today)
        rows = [
            {"currency": c, "date": d, "rate": r, "source": "boi"} for c, pts in data.items() for d, r in pts.items()
        ]
        with self._sf() as s:
            for i in range(0, len(rows), 400):
                stmt = sqlite_insert(FxPoint).values(rows[i : i + 400])
                # ручные курсы не затираем только если БИ не дал значения; официальный курс важнее
                s.execute(
                    stmt.on_conflict_do_update(
                        index_elements=["currency", "date"],
                        set_={"rate": stmt.excluded.rate, "source": stmt.excluded.source},
                    )
                )
            s.commit()
        self._fx_cache = None
        self._mark("fx")

    def refresh_quotes(self, assets: Iterable[Asset]) -> list[str]:
        """Текущие цены. Возвращает список сообщений об ошибках (пустой — всё хорошо)."""
        errors: list[str] = []
        by_source: dict[str, dict[str, Asset]] = {}
        for a in assets:
            if a.price_source in ("yfinance", "coingecko", "metal") and a.source_id:
                by_source.setdefault(a.price_source, {})[a.source_id] = a

        for ticker, asset in by_source.get("yfinance", {}).items():
            try:
                self._store_quote(asset.price_key, self.sources.yahoo.quote(ticker))
            except SourceError as exc:
                errors.append(f"{asset.symbol}: {exc}")

        coins = by_source.get("coingecko", {})
        if coins:
            try:
                quotes = self.sources.coingecko.quotes(list(coins))
                for coin_id, asset in coins.items():
                    if coin_id in quotes:
                        self._store_quote(asset.price_key, quotes[coin_id])
                    else:
                        errors.append(f"{asset.symbol}: CoinGecko не знает монету «{coin_id}»")
            except SourceError as exc:
                errors.append(f"Крипта: {exc}")

        for code, asset in by_source.get("metal", {}).items():
            try:
                self._store_quote(asset.price_key, self.sources.metals.quote(code))
            except SourceError as exc:
                errors.append(f"{asset.symbol}: {exc}")
        self._mark("quotes")
        return errors

    def _fetch_start(self, key: str, since: date) -> date:
        with self._sf() as s:
            first, last = (
                s.query(func.min(PricePoint.date), func.max(PricePoint.date)).filter(PricePoint.key == key).one()
            )
        if first is None or first > since + timedelta(days=7):
            return since
        return last - timedelta(days=5)

    def refresh_ticker_history(self, ticker: str, since: date, adjusted: bool = False) -> int:
        """История произвольного тикера Yahoo (индексы-прокси и т.п.). Ключ кэша: yfinance:<тикер>."""
        key = ("adj:" if adjusted else "") + f"yfinance:{ticker}"
        start = self._fetch_start(key, since)
        return self._store_series(key, self.sources.yahoo.history(ticker, start, self._today(), adjusted))

    def refresh_history(self, asset: Asset, since: date, adjusted: bool = False) -> int:
        """Дневная история актива с даты since. Источник выбирается по типу актива."""
        key = self.history_key(asset, adjusted)
        today = self._today()
        start = self._fetch_start(key, since)
        if asset.price_source == "yfinance":
            series = self.sources.yahoo.history(asset.source_id, start, today, adjusted)
        elif asset.price_source == "coingecko":
            if asset.history_ticker:
                series = self.sources.yahoo.history(asset.history_ticker, start, today, adjusted)
            else:
                days = min((today - start).days + 1, MAX_FREE_DAYS)
                series = self.sources.coingecko.history(asset.source_id, days)
        elif asset.price_source == "metal":
            series = self.sources.metals.history(asset.source_id, start, today)
        else:
            return 0
        return self._store_series(key, series)

    def refresh_dividends(self, asset: Asset) -> int:
        if asset.price_source != "yfinance" or not asset.source_id:
            return 0
        series = self.sources.yahoo.dividends(asset.source_id)
        rows = [
            {"key": asset.price_key, "ex_date": ts.date(), "amount": D(float(v))}
            for ts, v in series.items()
            if v > 0
        ]
        with self._sf() as s:
            for i in range(0, len(rows), 400):
                stmt = sqlite_insert(DivPoint).values(rows[i : i + 400])
                s.execute(
                    stmt.on_conflict_do_update(
                        index_elements=["key", "ex_date"], set_={"amount": stmt.excluded.amount}
                    )
                )
            s.commit()
        return len(rows)

    def refresh_everything(self, assets: list[Asset], since: date, currencies: Iterable[str]) -> list[str]:
        """Полное обновление: курсы, цены, история, дивиденды. Ошибки собираются, а не прерывают процесс."""
        errors: list[str] = []
        try:
            self.refresh_fx(currencies, since)
        except SourceError as exc:
            errors.append(f"Курсы Банка Израиля: {exc}")
        errors += self.refresh_quotes(assets)
        for asset in assets:
            try:
                self.refresh_history(asset, since)
                if asset.dist_type != "acc":
                    self.refresh_dividends(asset)
            except SourceError as exc:
                errors.append(f"{asset.symbol}: {exc}")
        self._mark("all")
        return errors
