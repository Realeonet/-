"""Общие вещи интерфейса: запуск, настройки, обновление данных, поля ввода, таблицы."""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from decimal import Decimal

import pandas as pd
import streamlit as st

from app.adapters.base import SourceError
from app.config import load_pension_config, load_settings
from app.db import Asset, Custodian, Lot, Owner, get_engine, get_setting, init_db, make_session_factory, set_setting
from app.domain.money import D, fmt_money, fmt_pct, fmt_qty
from app.domain.tax_il import DISCLAIMER, effective_rate
from app.services.market import MarketData

HISTORY_YEARS = 5
AUTO_REFRESH_MINUTES = 60

DEFAULTS: dict[str, str] = {
    "tax_rate_pct": "25",
    "surtax_pct": "0",
    "tax_method": "nominal",
    "estate_limit_usd": "60000",
    "estate_warn_usd": "45000",
    "concentration_pct": "40",
    "sma_window": "150",
    "blacklist": "BITO, BITI, MSTY, NVDY, PLTW",
    "rebalance_targets": json.dumps({"SPYI": "50", "JEPI": "50"}),
    "buy_window_after": "30",
    "buy_window_until": "60",
    "xday_days": "2",
    "dca_asset": "BTC",
    "dca_full": "0",
    "dca_reduced": "0",
    "dca_threshold": "0",
    "debt_rate_pct": "0",
    "auto_refresh": "1",
    "auto_backup": "1",
}


@st.cache_resource
def runtime():
    settings = load_settings()
    engine = get_engine(settings.db_path)
    init_db(engine)
    return settings, make_session_factory(engine), load_pension_config()


def Session():  # noqa: N802 — используется как фабрика: `with Session() as s:`
    return runtime()[1]()


def market() -> MarketData:
    return MarketData(runtime()[1])


def today() -> date:
    return date.today()


def offline() -> bool:
    return os.getenv("FINANCE_OFFLINE") == "1"


# ---------- настройки ----------

def cfg(key: str) -> str:
    with Session() as s:
        return get_setting(s, key, DEFAULTS.get(key, "")) or ""


def cfg_dec(key: str) -> Decimal:
    try:
        return D(cfg(key) or "0")
    except ValueError:
        return D(DEFAULTS.get(key, "0"))


def cfg_int(key: str) -> int:
    return int(cfg_dec(key))


def cfg_set(**values) -> None:  # noqa: ANN003
    with Session() as s:
        for key, value in values.items():
            set_setting(s, key, str(value))
        s.commit()


def tax_rate() -> Decimal:
    return effective_rate(cfg_dec("tax_rate_pct"), cfg_dec("surtax_pct"))


def tax_method() -> str:
    method = cfg("tax_method")
    return method if method in ("nominal", "linked") else "nominal"


def blacklist() -> list[str]:
    return [b.strip().upper() for b in cfg("blacklist").split(",") if b.strip()]


# ---------- справочники ----------

def owners() -> list[Owner]:
    with Session() as s:
        return s.query(Owner).order_by(Owner.id).all()


def custodians(include_archived: bool = False) -> list[Custodian]:
    from sqlalchemy.orm import joinedload

    with Session() as s:
        q = s.query(Custodian).options(joinedload(Custodian.owner))
        if not include_archived:
            q = q.filter(Custodian.archived.is_(False))
        return q.order_by(Custodian.name).all()


def assets() -> list[Asset]:
    with Session() as s:
        return s.query(Asset).order_by(Asset.symbol).all()


def custodian_label(c: Custodian) -> str:
    return f"{c.name} · {c.owner.name}"


# ---------- обновление данных ----------

def refresh_data(show: bool = True) -> list[str]:
    """Курсы БИ, цены, история и дивиденды для всех активов. Возвращает список ошибок."""
    m = market()
    with Session() as s:
        all_assets = s.query(Asset).all()
        first_lot = s.query(Lot.buy_date).order_by(Lot.buy_date).first()
        currencies = {a.currency for a in all_assets} | {c for (c,) in s.query(Lot.currency).distinct()}
        currencies |= {c for (c,) in s.query(Custodian.fee_currency).distinct()}
    since_hist = today() - timedelta(days=365 * HISTORY_YEARS + 30)
    since = min(since_hist, first_lot[0]) if first_lot else since_hist
    errors: list[str] = []
    try:
        m.refresh_fx(currencies, since)
    except SourceError as exc:
        errors.append(f"Курсы Банка Израиля: {exc}")
    errors += m.refresh_quotes(all_assets)
    for asset in all_assets:
        try:
            m.refresh_history(asset, since_hist)
            if asset.dist_type != "acc":
                m.refresh_dividends(asset)
        except SourceError as exc:
            errors.append(f"{asset.symbol}: {exc}")
    errors += refresh_pension_proxies(m)
    st.session_state["refresh_errors"] = errors
    st.session_state["refresh_at"] = datetime.now()
    return errors


def pension_tickers() -> list[str]:
    """Все тикеры-прокси пенсионного модуля из конфига (кроме курса БИ)."""
    config = runtime()[2]
    tickers = set(config.proxies.values())
    for track in config.tracks.values():
        tickers |= set(track.proxies.values())
    if not config.usd_ils.startswith("BOI:"):
        tickers.add(config.usd_ils)
    return sorted(t for t in tickers if t)


def refresh_pension_proxies(m: MarketData) -> list[str]:
    from app.db import PensionAccount

    with Session() as s:
        first = s.query(PensionAccount.balance_date).order_by(PensionAccount.balance_date).first()
    if first is None:
        return []
    since = min(first[0], date(today().year - 1, 1, 1)) - timedelta(days=40)
    errors = []
    for ticker in pension_tickers():
        try:
            m.refresh_ticker_history(ticker, since)
        except SourceError as exc:
            errors.append(f"Пенсия, прокси {ticker}: {exc}")
    return errors


def ensure_fx(since: date, currencies: set[str] | None = None) -> str | None:
    """Докачать курсы БИ с даты (перед вводом старой покупки). Возвращает текст ошибки или None."""
    if offline():
        return None
    try:
        market().refresh_fx(currencies or {"USD"}, since)
    except SourceError as exc:
        return str(exc)
    return None


def auto_refresh() -> None:
    """Автообновление раз в час при открытии приложения (можно отключить в настройках)."""
    if offline() or cfg("auto_refresh") != "1":
        return
    last = market().last_refresh("quotes")
    if last is not None and datetime.now() - last < timedelta(minutes=AUTO_REFRESH_MINUTES):
        return
    with st.spinner("Обновляю курсы и котировки…"):
        refresh_data()


# ---------- ввод ----------

def dec_input(label: str, key: str, value: str = "", help: str | None = None, container=None) -> str:  # noqa: A002, ANN001
    """Текстовое поле для точного числа (без потерь float). Разбор — через parse_dec при отправке."""
    target = container or st
    return target.text_input(label, value=value, key=key, help=help, placeholder="0")


def parse_dec(raw: str, name: str, allow_empty: bool = False, positive: bool = False) -> Decimal | None:
    text = (raw or "").strip().replace(",", ".").replace(" ", "")
    if text == "":
        if allow_empty:
            return None
        raise ValueError(f"Заполните поле «{name}»")
    try:
        value = Decimal(text)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"«{name}»: введите число, например 12.5") from exc
    if not value.is_finite():
        raise ValueError(f"«{name}»: некорректное число")
    if positive and value <= 0:
        raise ValueError(f"«{name}» должно быть больше нуля")
    if value < 0:
        raise ValueError(f"«{name}» не может быть отрицательным")
    return value


# ---------- вывод ----------

def table(rows: list[dict], height: int | None = None) -> None:
    if not rows:
        st.caption("Нет данных.")
        return
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=height or "auto")


def ils(v: Decimal | None) -> str:
    return fmt_money(v, "₪")


def usd(v: Decimal | None) -> str:
    return fmt_money(v, "$")


def pct(v: Decimal | None, signed: bool = False) -> str:
    return fmt_pct(v, signed=signed)


def qty(v: Decimal | None) -> str:
    return fmt_qty(v)


def tax_note() -> None:
    st.caption(f"⚠️ {DISCLAIMER}.")


def flash(message: str, kind: str = "success") -> None:
    """Сообщение, которое переживает st.rerun()."""
    st.session_state.setdefault("_flash", []).append((kind, message))


def show_flash() -> None:
    for kind, message in st.session_state.pop("_flash", []):
        getattr(st, kind)(message)
