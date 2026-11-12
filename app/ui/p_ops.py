"""Экран «Активы и операции»: покупка, продажа, перевод, обмен, справочник активов."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import streamlit as st
from sqlalchemy.orm import joinedload

from app.adapters.base import SourceError
from app.adapters.metals import METALS
from app.db import ASSET_CLASSES, PRICE_SOURCES, Asset, Custodian, Disposal, Dividend, Lot, Transfer
from app.domain import minkin
from app.domain.money import WEIGHT_UNITS, ZERO, fine_grams
from app.domain.tax_il import simulate_sale
from app.services import ledger
from app.services.dividends import upcoming_ex_dates
from app.ui.common import (Session, assets, blacklist, cfg_int, custodian_label, custodians, dec_input,
                           ensure_fx, flash, ils, market, offline, parse_dec, pct, qty, show_flash, table,
                           tax_method, tax_note, tax_rate, today, usd)

DIST = {"na": "не применимо", "acc": "Acc — накопительный", "dist": "Dist — выплачивает"}
SOURCE_FOR_CLASS = {"stock": "yfinance", "etf": "yfinance", "crypto": "coingecko", "metal": "metal",
                    "cash": "cash", "other": "manual"}
UNIT_LABELS = {"g": "граммы", "oz_t": "тройские унции", "kg": "килограммы"}


def _clear(prefix: str) -> None:
    for key in [k for k in st.session_state if k.startswith(prefix)]:
        del st.session_state[key]


def _holdings(asset_id: int) -> dict[int, Decimal]:
    """{id места: остаток} для актива."""
    with Session() as s:
        out: dict[int, Decimal] = {}
        for lot in ledger.open_lots(s, asset_id):
            out[lot.custodian_id] = out.get(lot.custodian_id, ZERO) + lot.qty_remaining
        return out


def _lot_picker(prefix: str, lots: list[Lot]) -> dict[int, str]:
    st.caption("Сколько взять из каждого лота (пусто = не трогать):")
    picks = {}
    for lot in lots:
        cost = lot.price + (lot.fees / lot.qty if lot.qty else ZERO)
        picks[lot.id] = st.text_input(
            f"Лот №{lot.id} · {lot.buy_date:%d.%m.%Y} · остаток {qty(lot.qty_remaining)} · "
            f"себестоимость {qty(cost.quantize(Decimal('0.0001')))} {lot.currency}",
            key=f"{prefix}_pick_{lot.id}", placeholder="0",
        )
    return picks


def _parse_picks(raw: dict[int, str]) -> dict[int, Decimal]:
    out = {}
    for lot_id, text in raw.items():
        value = parse_dec(text, f"лот №{lot_id}", allow_empty=True)
        if value:
            out[lot_id] = value
    if not out:
        raise ValueError("Укажите количество хотя бы для одного лота")
    return out


# ======================= покупка =======================

def _buy_warnings(asset: Asset) -> None:
    m, day = market(), today()
    if minkin.blacklist_hit(asset.symbol, blacklist()) or minkin.blacklist_hit(asset.source_id, blacklist()):
        st.error(f"⛔ {asset.symbol} в чёрном списке «алгоритма Минкина». Подумайте дважды.")
    if asset.asset_class in ("stock", "etf") and asset.dist_type != "acc":
        ex = upcoming_ex_dates(asset, m, day, 30)
        days = cfg_int("xday_days")
        if ex and minkin.xday_warning(ex[0].day, day, days):
            kind = "прогноз" if ex[0].estimated else "введён вручную"
            st.warning(f"⏳ X-day {ex[0].day:%d.%m.%Y} ({kind}) — через {(ex[0].day - day).days} дн. "
                       "Покупка сейчас = налог 25% с ближайшей выплаты. Правило: не покупать за "
                       f"{days} дн. до X-day.")
    if asset.asset_class in ("stock", "etf") and not asset.source_id.upper().endswith((".L", ".TA")):
        w = minkin.buy_window(day, cfg_int("buy_window_after"), cfg_int("buy_window_until"))
        st.caption(f"🕒 Окно покупки сегодня: {w.start_il:%H:%M}–{w.end_il:%H:%M} по Израилю "
                   f"(рынок США открывается в {w.open_il:%H:%M}).")


def tab_buy() -> None:
    asset_list, cust_list = assets(), custodians()
    if not asset_list or not cust_list:
        st.info("Сначала создайте место хранения (раздел «Места хранения») и актив (вкладка «Активы»).")
        return
    a, b = st.columns(2)
    asset = a.selectbox("Актив", asset_list, format_func=lambda x: f"{x.symbol} — {x.name}", key="buy_asset")
    cust = b.selectbox("Место хранения", cust_list, format_func=custodian_label, key="buy_cust")
    _buy_warnings(asset)
    is_metal = asset.asset_class == "metal"
    c1, c2, c3 = st.columns(3)
    buy_date = c1.date_input("Дата покупки", value=today(), max_value=today(), key="buy_date",
                             format="DD.MM.YYYY")
    currency = c2.text_input("Валюта цены", value=asset.currency, key=f"buy_ccy_{asset.id}",
                             help="Обычно совпадает с валютой актива. Лондон: GBp — пенсы. Тель-Авив: ILA — агороты.")
    purity = form = weight = None
    if is_metal:
        st.markdown("**Физический металл**")
        m1, m2, m3, m4 = st.columns(4)
        weight_raw = dec_input("Вес", "buy_weight", container=m1)
        unit = m2.selectbox("Единица", list(WEIGHT_UNITS), format_func=lambda u: UNIT_LABELS[u], key="buy_unit")
        purity_raw = dec_input("Проба", "buy_purity", "0.999", container=m3, help="Например 0.999 или 0.9167")
        form = m4.text_input("Форма", key="buy_form", placeholder="слиток / монета")
        p1, p2, p3 = st.columns(3)
        net_raw = dec_input("Уплачено без НДС", "buy_net", container=p1)
        vat_raw = dec_input("НДС (מע״מ)", "buy_vat", "0", container=p2,
                            help="НДС включается в себестоимость. Правила по НДС на металлы — проверить.")
        fee_raw = dec_input("Прочие комиссии", "buy_fee", "0", container=p3)
    else:
        qty_raw = dec_input("Количество", "buy_qty", container=c3)
        p1, p2, p3 = st.columns(3)
        price_raw = dec_input("Цена за единицу", "buy_price", container=p1)
        total_raw = dec_input("…или общая сумма", "buy_total", container=p2,
                              help="Если заполнено, цена за единицу = сумма / количество.")
        fee_raw = dec_input("Комиссия", "buy_fee", "0", container=p3)
    with st.expander("Курс валюты вручную (обычно не нужно)"):
        st.caption("По умолчанию берётся официальный курс Банка Израиля на дату покупки.")
        x1, x2 = st.columns(2)
        fx_ccy_raw = dec_input(f"₪ за 1 {currency}", "buy_fx_ccy", container=x1)
        fx_usd_raw = dec_input("₪ за $1", "buy_fx_usd", container=x2)
    note = st.text_input("Заметка", key="buy_note")

    if not st.button("Добавить лот", type="primary", key="buy_submit"):
        return
    try:
        fee = parse_dec(fee_raw, "Комиссия") or ZERO
        vat = ZERO
        if is_metal:
            weight = parse_dec(weight_raw, "Вес", positive=True)
            purity = parse_dec(purity_raw, "Проба", positive=True)
            quantity = fine_grams(weight, unit, purity)
            net = parse_dec(net_raw, "Уплачено без НДС", positive=True)
            vat = parse_dec(vat_raw, "НДС") or ZERO
            price = net / quantity
            fee = fee + vat
            gross_g = weight * WEIGHT_UNITS[unit]
        else:
            quantity = parse_dec(qty_raw, "Количество", positive=True)
            total = parse_dec(total_raw, "Общая сумма", allow_empty=True)
            price = total / quantity if total else parse_dec(price_raw, "Цена за единицу")
            gross_g = None
        fx_ccy = parse_dec(fx_ccy_raw, "Курс валюты", allow_empty=True)
        fx_usd = parse_dec(fx_usd_raw, "Курс доллара", allow_empty=True)
        if fx_ccy is None or fx_usd is None:
            error = ensure_fx(buy_date, {currency, "USD"})
            if error:
                st.warning(f"Курсы БИ не обновились: {error}")
        with Session() as s:
            ledger.add_lot(
                s, market(), s.get(Asset, asset.id), s.get(Custodian, cust.id), buy_date, quantity, price,
                fees=fee, vat=vat, currency=currency.strip() or asset.currency, fx_ccy_ils=fx_ccy,
                fx_usd_ils=fx_usd, purity=purity, form=(form or None), gross_weight_g=gross_g,
                note=note.strip() or None,
            )
            s.commit()
    except (ValueError, ledger.LedgerError) as exc:
        st.error(str(exc))
        return
    _clear("buy_")
    flash(f"Лот добавлен: {asset.symbol}, {qty(quantity)}" + (" г чистого металла" if is_metal else ""))
    st.rerun()


# ======================= продажа =======================

def _select_holding(prefix: str, classes: tuple[str, ...] | None = None):
    """Выбор актива и места, где он лежит. Возвращает (asset, custodian, lots) или None."""
    held = [a for a in assets() if _holdings(a.id) and (classes is None or a.asset_class in classes)]
    if not held:
        st.info("Нет открытых лотов." if classes is None else "Нет открытых лотов этого класса.")
        return None
    a, b = st.columns(2)
    asset = a.selectbox("Актив", held, format_func=lambda x: f"{x.symbol} — {x.name}", key=f"{prefix}_asset")
    holdings = _holdings(asset.id)
    cust_list = [c for c in custodians(include_archived=True) if c.id in holdings]
    cust = b.selectbox("Место хранения", cust_list, key=f"{prefix}_cust_{asset.id}",
                       format_func=lambda c: f"{custodian_label(c)} — {qty(holdings[c.id])}")
    with Session() as s:
        lots = ledger.open_lots(s, asset.id, cust.id)
    return asset, cust, lots


def tab_sell() -> None:
    picked = _select_holding("sell")
    if picked is None:
        return
    asset, cust, lots = picked
    m = market()
    info = m.price_info(asset)
    c1, c2, c3 = st.columns(3)
    day = c1.date_input("Дата продажи", value=today(), max_value=today(), key="sell_date", format="DD.MM.YYYY")
    default_price = str(info.price) if info else ""
    currency = info.currency if info else asset.currency
    price_raw = dec_input(f"Цена за единицу, {currency}", f"sell_price_{asset.id}", default_price, container=c2)
    fee_raw = dec_input("Комиссия продажи", "sell_fee", "0", container=c3)
    method = st.radio("Какие лоты продавать", ["FIFO — самые старые первыми", "Выбрать лоты вручную"],
                      horizontal=True, key="sell_method")
    manual = method.startswith("Выбрать")
    raw_picks, qty_raw = {}, ""
    if manual:
        raw_picks = _lot_picker(f"sell_{asset.id}_{cust.id}", lots)
    else:
        qty_raw = dec_input("Количество", "sell_qty")
    withheld_raw = "0"
    if cust.withholds_il_tax:
        withheld_raw = dec_input("Налог, удержанный брокером, ₪", "sell_withheld", "0",
                                 help="Это место хранения удерживает налог само. Впишите сумму из отчёта, если знаете.")
    else:
        st.caption("ℹ️ Это место хранения не удерживает израильский налог — прибыль нужно декларировать самому.")

    # предварительный расчёт
    sim = None
    try:
        price = parse_dec(price_raw, "Цена", allow_empty=True)
        fee = parse_dec(fee_raw, "Комиссия", allow_empty=True) or ZERO
        states = [ledger.lot_state(lot) for lot in lots]
        if manual:
            picks = {k: v for k, v in ((i, parse_dec(t, "лот", allow_empty=True)) for i, t in raw_picks.items()) if v}
            alloc = ledger.manual_allocate(states, picks) if picks else None
        else:
            quantity = parse_dec(qty_raw, "Количество", allow_empty=True)
            alloc = ledger.fifo_allocate(states, quantity) if quantity else None
        fx_ccy, fx_usd = m.fx(currency, day), m.usd_ils(day)
        if alloc and price is not None and fx_ccy and fx_usd:
            sim = simulate_sale(alloc, price, fee, currency, fx_ccy, fx_usd, tax_rate(), tax_method())
    except ValueError as exc:
        st.warning(str(exc))
    if sim:
        st.markdown("**Предварительный расчёт**")
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Выручка", usd(sim.proceeds_usd), ils(sim.proceeds_ils), delta_color="off", delta_arrow="off")
        k2.metric("Налогооблагаемая прибыль, ₪", ils(sim.taxable_ils))
        k3.metric(f"Налог {pct(tax_rate())}", ils(sim.tax_ils))
        k4.metric("На руки после налога", ils(sim.net_ils), usd(sim.net_usd), delta_color="off", delta_arrow="off")
        table([{
            "Лот №": g.lot_id, "Кол-во": qty(g.qty), "Прибыль, $": usd(g.gain_usd),
            "Прибыль, ₪ (номинальная)": ils(g.gain_ils_nominal), "Прибыль, ₪ (привязка к валюте)": ils(g.gain_ils_linked),
            "Налог, ₪": ils(g.tax_ils),
        } for g in sim.gains])
        tax_note()

    if not st.button("Записать продажу", type="primary", key="sell_submit"):
        return
    try:
        price = parse_dec(price_raw, "Цена")
        fee = parse_dec(fee_raw, "Комиссия") or ZERO
        withheld = parse_dec(withheld_raw, "Удержанный налог") or ZERO
        picks = _parse_picks(raw_picks) if manual else None
        quantity = None if manual else parse_dec(qty_raw, "Количество", positive=True)
        error = ensure_fx(day, {currency, "USD"})
        if error:
            st.warning(f"Курсы БИ не обновились: {error}")
        with Session() as s:
            ledger.sell(s, market(), s.get(Asset, asset.id), cust.id, day, price, qty=quantity, fee=fee,
                        currency=currency, picks=picks, il_tax_withheld=withheld)
            s.commit()
    except (ValueError, ledger.LedgerError) as exc:
        st.error(str(exc))
        return
    _clear("sell_")
    flash(f"Продажа {asset.symbol} записана. Налог — в разделе «Налоги».")
    st.rerun()


# ======================= перевод =======================

def tab_transfer() -> None:
    st.caption("Перевод между местами хранения (биржа → холодный кошелёк, брокер → брокер). "
               "Это не продажа: дата и цена покупки лотов сохраняются, налога нет.")
    picked = _select_holding("tr")
    if picked is None:
        return
    asset, cust, lots = picked
    targets = [c for c in custodians() if c.id != cust.id]
    if not targets:
        st.info("Нужно второе место хранения.")
        return
    a, b = st.columns(2)
    to = a.selectbox("Куда", targets, format_func=custodian_label, key="tr_to")
    day = b.date_input("Дата перевода", value=today(), max_value=today(), key="tr_date", format="DD.MM.YYYY")
    if to.owner_id != cust.owner_id:
        st.warning("Владельцы мест разные. Перевод между супругами — проверьте налоговые последствия.")
    c1, c2, c3, c4 = st.columns(4)
    sent_raw = dec_input("Отправлено", "tr_sent", container=c1)
    recv_raw = dec_input("Получено", "tr_recv", container=c2,
                         help="Пусто = столько же. Меньше — если комиссия сети списана самим активом.")
    fee_raw = dec_input("Денежная комиссия", "tr_fee", "0", container=c3,
                        help="Добавляется к себестоимости переведённых лотов.")
    fee_ccy = c4.selectbox("Валюта комиссии", ["USD", "ILS", "EUR", "GBP"], key="tr_fee_ccy")
    manual = st.checkbox("Выбрать лоты вручную (иначе FIFO)", key="tr_manual")
    raw_picks = _lot_picker(f"tr_{asset.id}_{cust.id}", lots) if manual else {}
    flagged = st.checkbox(
        "По сути это обмен, а не перевод", key="tr_flag",
        help="Например, вывод через конвертацию в другой токен. Запись получит пометку «проверить налог». "
             "Настоящий обмен одной монеты на другую оформляйте на вкладке «Обменять».")
    note = st.text_input("Заметка", key="tr_note")
    if not st.button("Записать перевод", type="primary", key="tr_submit"):
        return
    try:
        sent = parse_dec(sent_raw, "Отправлено", positive=True)
        recv = parse_dec(recv_raw, "Получено", allow_empty=True)
        fee = parse_dec(fee_raw, "Комиссия") or ZERO
        picks = _parse_picks(raw_picks) if manual else None
        ensure_fx(day, {fee_ccy, "USD"} | {lot.currency for lot in lots})
        with Session() as s:
            ledger.transfer(s, market(), s.get(Asset, asset.id), s.get(Custodian, cust.id),
                            s.get(Custodian, to.id), day, sent, recv, fee, fee_ccy, picks, flagged,
                            note.strip() or None)
            s.commit()
    except (ValueError, ledger.LedgerError) as exc:
        st.error(str(exc))
        return
    _clear("tr_")
    flash(f"Перевод {asset.symbol}: {cust.name} → {to.name} записан")
    st.rerun()


# ======================= обмен =======================

def tab_swap() -> None:
    st.caption("Обмен одной криптовалюты на другую — налоговая реализация: старые лоты закрываются по рыночной "
               "стоимости, новый лот получает эту стоимость как цену покупки.")
    picked = _select_holding("sw", ("crypto",))
    if picked is None:
        return
    asset, cust, lots = picked
    others = [a for a in assets() if a.id != asset.id and a.asset_class == "crypto"]
    if not others:
        st.info("Создайте актив, который получаете (вкладка «Активы»).")
        return
    m = market()
    info = m.price_info(asset)
    a, b = st.columns(2)
    to_asset = a.selectbox("Получаю", others, format_func=lambda x: f"{x.symbol} — {x.name}", key="sw_to")
    day = b.date_input("Дата обмена", value=today(), max_value=today(), key="sw_date", format="DD.MM.YYYY")
    c1, c2, c3, c4 = st.columns(4)
    from_raw = dec_input(f"Отдаю, {asset.symbol}", "sw_from", container=c1)
    to_raw = dec_input(f"Получаю, {to_asset.symbol}", "sw_toqty", container=c2)
    hint = f"Текущая цена {asset.symbol}: {qty(info.price)} USD." if info else ""
    value_raw = dec_input("Рыночная стоимость отданного, $", "sw_value", container=c3, help=hint)
    fee_raw = dec_input("Комиссия, $", "sw_fee", "0", container=c4)
    if not st.button("Записать обмен", type="primary", key="sw_submit"):
        return
    try:
        q_from = parse_dec(from_raw, "Отдаю", positive=True)
        q_to = parse_dec(to_raw, "Получаю", positive=True)
        value = parse_dec(value_raw, "Рыночная стоимость", positive=True)
        fee = parse_dec(fee_raw, "Комиссия") or ZERO
        ensure_fx(day, {"USD"})
        with Session() as s:
            ledger.swap(s, market(), s.get(Asset, asset.id), s.get(Asset, to_asset.id), s.get(Custodian, cust.id),
                        day, q_from, q_to, value, fee)
            s.commit()
    except (ValueError, ledger.LedgerError) as exc:
        st.error(str(exc))
        return
    _clear("sw_")
    flash(f"Обмен {asset.symbol} → {to_asset.symbol} записан. Это реализация — см. раздел «Налоги».")
    st.rerun()


# ======================= активы =======================

def _guess_domicile(ticker: str, name: str) -> str:
    upper = ticker.upper()
    if upper.endswith(".TA"):
        return "IL"
    if "UCITS" in name.upper():
        return "IE"
    if "." not in upper and "=" not in upper and "-" not in upper:
        return "US"
    return ""


def _lookup_panel(asset_class: str) -> None:
    """Поиск в источнике и автозаполнение полей новой карточки."""
    m = market()
    if asset_class in ("stock", "etf"):
        a, b = st.columns([3, 1])
        ticker = a.text_input("Тикер Yahoo Finance", key="na_lookup",
                              help="США: SPYI. Лондон: CSPX.L. Тель-Авив: TEVA.TA.").strip()
        if b.button("Найти", key="na_find", disabled=offline() or not ticker):
            try:
                prof = m.sources.yahoo.profile(ticker)
                st.session_state.update({
                    "na_symbol": ticker.split(".")[0].upper(), "na_source_id": ticker, "na_name": prof["name"],
                    "na_currency": prof["currency"] or "USD", "na_dist": prof["dist_type"],
                    "na_expense": str(prof["expense_ratio_pct"] or ""),
                    "na_domicile": _guess_domicile(ticker, prof["name"]),
                })
                _clear("na_f_")
                flash(f"Найдено: {prof['name']} · {prof['currency']} · {prof['exchange']}. Проверьте поля ниже.")
                st.rerun()
            except SourceError as exc:
                st.error(str(exc))
    elif asset_class == "crypto":
        a, b = st.columns([3, 1])
        query = a.text_input("Название или тикер монеты", key="na_lookup", help="Например: ATOM, solana").strip()
        if b.button("Найти", key="na_find", disabled=offline() or not query):
            try:
                st.session_state["na_coins"] = m.sources.coingecko.search(query)[:15]
            except SourceError as exc:
                st.error(str(exc))
        coins = st.session_state.get("na_coins") or []
        if coins:
            coin = st.selectbox("Результаты CoinGecko", coins, key="na_coin_pick",
                                format_func=lambda c: f"{c['symbol']} — {c['name']} (id: {c['id']}, ранг {c['rank']})")
            if st.button("Выбрать эту монету", key="na_coin_use"):
                st.session_state.update({
                    "na_symbol": coin["symbol"], "na_source_id": coin["id"], "na_name": coin["name"],
                    "na_currency": "USD", "na_history": f"{coin['symbol']}-USD",
                })
                _clear("na_f_")
                st.rerun()
    elif asset_class == "metal":
        code = st.selectbox("Металл", list(METALS), format_func=lambda c: f"{METALS[c][0]} ({c})", key="na_metal")
        if st.session_state.get("na_source_id") != code:
            st.session_state.update({"na_source_id": code, "na_currency": "USD", "na_symbol": "",
                                     "na_name": METALS[code][0]})
            _clear("na_f_metal")
        st.caption("Количество металла ведётся в граммах чистого металла, цена — спот в USD за грамм. "
                   "Для разных форм (монеты/слитки) можно завести отдельные активы с разной поправкой к споту.")


def _asset_fields(prefix: str, a: Asset | None, asset_class: str) -> dict:
    ss = st.session_state
    def val(name: str, attr: str, default: str = "") -> str:  # noqa: E306
        if a is not None:
            current = getattr(a, attr)
            return "" if current is None else str(current)
        return ss.get(f"na_{name}", default)

    out: dict = {}
    c1, c2, c3 = st.columns(3)
    out["symbol"] = c1.text_input("Символ (как показывать)", value=val("symbol", "symbol"), key=f"{prefix}_symbol")
    out["name"] = c2.text_input("Название", value=val("name", "name"), key=f"{prefix}_name")
    out["currency"] = c3.text_input("Валюта котировки", value=val("currency", "currency", "USD"),
                                    key=f"{prefix}_currency", help="USD, ILS, EUR, GBP; GBp — пенсы; ILA — агороты.")
    sources = list(PRICE_SOURCES)
    default_source = a.price_source if a else SOURCE_FOR_CLASS[asset_class]
    c1, c2, c3 = st.columns(3)
    out["price_source"] = c1.selectbox("Источник цены", sources, index=sources.index(default_source),
                                       format_func=lambda k: PRICE_SOURCES[k], key=f"{prefix}_source")
    out["source_id"] = c2.text_input("Тикер / id в источнике", value=val("source_id", "source_id"),
                                     key=f"{prefix}_source_id")
    out["history_ticker"] = c3.text_input(
        "Тикер Yahoo для длинной истории", value=val("history", "history_ticker"), key=f"{prefix}_hist",
        help="Для крипты: BTC-USD, ETH-USD… CoinGecko бесплатно отдаёт только 365 дней.")
    if asset_class in ("stock", "etf"):
        c1, c2, c3, c4 = st.columns(4)
        out["domicile"] = c1.text_input(
            "Домициль фонда", value=val("domicile", "domicile"), key=f"{prefix}_dom",
            help="US / IE / IL / LU… От него зависит налог США на наследство: US = US-situs. Проверьте по проспекту.")
        dist_keys = list(DIST)
        current = (a.dist_type if a else ss.get("na_dist", "na"))
        out["dist_type"] = c2.selectbox("Тип выплат", dist_keys, index=dist_keys.index(current),
                                        format_func=lambda k: DIST[k], key=f"{prefix}_dist")
        out["dist_frequency"] = c3.text_input("Выплат в год", value=val("freq", "dist_frequency"),
                                              key=f"{prefix}_freq")
        out["expense_ratio_pct"] = c4.text_input("Комиссия фонда, % в год", value=val("expense", "expense_ratio_pct"),
                                                 key=f"{prefix}_ter")
    if a is not None or asset_class in ("metal", "other"):
        c1, c2, c3 = st.columns(3)
        out["valuation_adj_pct"] = c1.text_input(
            "Поправка к цене, %", value=str(a.valuation_adj_pct) if a else "0", key=f"{prefix}_adj",
            help="Для металлов: премия (+) или дисконт (−) к споту при продаже. Например −5.")
        out["manual_price"] = c2.text_input("Цена вручную", value=val("mp", "manual_price"), key=f"{prefix}_mp",
                                            help="Запасной вариант, если источник недоступен. Для «ручного» актива — основная цена.")
        out["manual_price_date"] = c3.date_input("Дата ручной цены", value=(a.manual_price_date if a else None) or today(),
                                                 key=f"{prefix}_mpd", format="DD.MM.YYYY")
    if a is not None and asset_class in ("stock", "etf"):
        c1, c2, c3, c4 = st.columns(4)
        out["next_ex_date"] = c1.date_input("Ближайший X-day (если знаете)", value=a.next_ex_date,
                                            key=f"{prefix}_ex", format="DD.MM.YYYY")
        out["next_pay_date"] = c2.date_input("Дата выплаты", value=a.next_pay_date, key=f"{prefix}_pay",
                                             format="DD.MM.YYYY")
        out["manual_yield_pct"] = c3.text_input("Доходность вручную, %", value=val("y", "manual_yield_pct"),
                                                key=f"{prefix}_yield", help="Используется, если истории выплат нет.")
        options = ["авто", "да", "нет"]
        current = "авто" if a.us_situs_override is None else ("да" if a.us_situs_override else "нет")
        out["us_situs"] = c4.selectbox("US-situs (наследство США)", options, index=options.index(current),
                                       key=f"{prefix}_situs",
                                       help="Авто: акции и ETF с домицилем US. Переопределите, если знаете точнее.")
    out["note"] = st.text_input("Заметка", value=val("note", "note"), key=f"{prefix}_note")
    return out


def _save_asset(a: Asset | None, asset_class: str, f: dict) -> None:
    symbol = f["symbol"].strip().upper()
    if not symbol:
        raise ValueError("Укажите символ")
    source = f["price_source"]
    source_id = f["source_id"].strip()
    if source in ("yfinance", "coingecko", "metal") and not source_id:
        raise ValueError("Укажите тикер / id в источнике цены")
    if source == "metal" and source_id.upper() not in METALS:
        raise ValueError("Для металла укажите код: " + ", ".join(METALS))
    with Session() as s:
        clash = s.query(Asset).filter(Asset.symbol == symbol).first()
        if clash is not None and (a is None or clash.id != a.id):
            raise ValueError(f"Актив с символом {symbol} уже есть")
        target = s.get(Asset, a.id) if a else Asset(asset_class=asset_class)
        target.symbol, target.name = symbol, f["name"].strip()
        target.currency = f["currency"].strip() or "USD"
        target.price_source = source
        target.source_id = source_id.upper() if source == "metal" else source_id
        target.history_ticker = f["history_ticker"].strip()
        target.note = f["note"].strip() or None
        if "domicile" in f:
            target.domicile = f["domicile"].strip().upper()
            target.dist_type = f["dist_type"]
            freq = f["dist_frequency"].strip()
            target.dist_frequency = int(freq) if freq else None
            target.expense_ratio_pct = parse_dec(f["expense_ratio_pct"], "Комиссия фонда", allow_empty=True)
        if "valuation_adj_pct" in f:
            raw = f["valuation_adj_pct"].strip().replace(",", ".") or "0"
            target.valuation_adj_pct = Decimal(raw)
            target.manual_price = parse_dec(f["manual_price"], "Цена вручную", allow_empty=True)
            target.manual_price_date = f["manual_price_date"] if target.manual_price is not None else None
        if "next_ex_date" in f:
            target.next_ex_date, target.next_pay_date = f["next_ex_date"], f["next_pay_date"]
            target.manual_yield_pct = parse_dec(f["manual_yield_pct"], "Доходность", allow_empty=True)
            target.us_situs_override = {"авто": None, "да": True, "нет": False}[f["us_situs"]]
        if a is None:
            s.add(target)
        s.commit()
        saved_id = target.id
    if not offline() and a is None:
        m = market()
        with Session() as s:
            fresh = s.get(Asset, saved_id)
            errors = m.refresh_quotes([fresh])
            if errors:
                flash("Цена не загрузилась: " + "; ".join(errors), "warning")


def tab_assets() -> None:
    asset_list = assets()
    with st.expander("➕ Новый актив", expanded=not asset_list):
        classes = list(ASSET_CLASSES)
        asset_class = st.radio("Класс актива", classes, format_func=lambda k: ASSET_CLASSES[k], horizontal=True,
                               key="na_class")
        _lookup_panel(asset_class)
        fields = _asset_fields(f"na_f_{asset_class}", None, asset_class)
        symbol = fields["symbol"].strip().upper()
        if symbol and (minkin.blacklist_hit(symbol, blacklist())
                       or minkin.blacklist_hit(fields["source_id"], blacklist())):
            st.error(f"⛔ {symbol} в чёрном списке «алгоритма Минкина».")
        if st.button("Создать актив", type="primary", key="na_submit"):
            try:
                _save_asset(None, asset_class, fields)
            except (ValueError, ArithmeticError) as exc:
                st.error(str(exc))
            else:
                _clear("na_")
                flash(f"Актив {symbol} создан")
                st.rerun()

    if not asset_list:
        return
    m = market()
    rows = []
    for a in asset_list:
        info = m.price_info(a)
        rows.append({
            "Символ": a.symbol, "Название": a.name, "Класс": ASSET_CLASSES.get(a.asset_class, a.asset_class),
            "Цена": f"{qty(info.price)} {info.currency}" if info else "нет цены",
            "На дату": info.as_of.strftime("%d.%m.%Y") if info else "—",
            "Источник": info.source if info else PRICE_SOURCES.get(a.price_source, a.price_source),
            "Домициль": a.domicile or "—", "Выплаты": DIST.get(a.dist_type, "—").split(" — ")[0],
            "US-situs": "да" if a.us_situs else "",
        })
    table(rows)
    st.caption("Цена металлов — за грамм чистого металла. Пенсы (GBp) и агороты (ILA) пересчитываются автоматически.")
    chosen = st.selectbox("Изменить актив", asset_list, format_func=lambda x: f"{x.symbol} — {x.name}",
                          key="ea_pick")
    with st.form(f"ea_form_{chosen.id}"):
        fields = _asset_fields(f"ea_{chosen.id}", chosen, chosen.asset_class)
        save = st.form_submit_button("Сохранить изменения")
    if save:
        try:
            _save_asset(chosen, chosen.asset_class, fields)
        except (ValueError, ArithmeticError) as exc:
            st.error(str(exc))
        else:
            flash(f"Актив {chosen.symbol} обновлён")
            st.rerun()
    with Session() as s:
        used = s.query(Lot).filter(Lot.asset_id == chosen.id).count()
        used += s.query(Dividend).filter(Dividend.asset_id == chosen.id).count()
        used += s.query(Transfer).filter(Transfer.asset_id == chosen.id).count()
    if used == 0 and st.button(f"Удалить актив {chosen.symbol}", key="ea_delete"):
        with Session() as s:
            s.delete(s.get(Asset, chosen.id))
            s.commit()
        flash("Актив удалён")
        st.rerun()


# ======================= журнал =======================

def tab_journal() -> None:
    with Session() as s:
        disposals = (s.query(Disposal).options(joinedload(Disposal.lot).joinedload(Lot.asset),
                                               joinedload(Disposal.lot).joinedload(Lot.custodian))
                     .order_by(Disposal.date.desc(), Disposal.id.desc()).limit(200).all())
        lots = (s.query(Lot).options(joinedload(Lot.asset), joinedload(Lot.custodian), joinedload(Lot.disposals))
                .order_by(Lot.buy_date.desc(), Lot.id.desc()).all())
    st.markdown("**Продажи и обмены**")
    groups: dict[str, list[Disposal]] = {}
    for d in disposals:
        groups.setdefault(d.group_id or f"single-{d.id}", []).append(d)
    table([{
        "Дата": ds[0].date.strftime("%d.%m.%Y"), "Актив": ds[0].lot.asset.symbol,
        "Место": ds[0].lot.custodian.name, "Тип": "обмен" if ds[0].kind == "swap" else "продажа",
        "Кол-во": qty(sum((d.qty for d in ds), ZERO)), "Цена": f"{qty(ds[0].price)} {ds[0].currency}",
        "Лотов": len(ds),
    } for ds in groups.values()])
    if groups:
        with st.form("undo_sale"):
            labels = {gid: f"{ds[0].date:%d.%m.%Y} · {ds[0].lot.asset.symbol} · "
                           f"{qty(sum((d.qty for d in ds), ZERO))}" for gid, ds in groups.items()}
            gid = st.selectbox("Отменить продажу / обмен", list(labels), format_func=lambda g: labels[g])
            st.caption("Количество вернётся в лоты. Лот, полученный при обмене, нужно удалить отдельно ниже.")
            if st.form_submit_button("Отменить"):
                with Session() as s:
                    if gid.startswith("single-"):
                        d = s.get(Disposal, int(gid.split("-")[1]))
                        lot = s.get(Lot, d.lot_id)
                        lot.qty_remaining = lot.qty_remaining + d.qty
                        s.delete(d)
                    else:
                        ledger.delete_disposal_group(s, gid)
                    s.commit()
                flash("Операция отменена")
                st.rerun()
    st.markdown("**Все лоты** (включая закрытые)")
    kinds = {"buy": "покупка", "transfer_in": "перевод", "swap_in": "обмен", "import": "импорт"}
    table([{
        "№": l.id, "Актив": l.asset.symbol, "Место": l.custodian.name, "Куплено": l.buy_date.strftime("%d.%m.%Y"),
        "Кол-во": qty(l.qty), "Остаток": qty(l.qty_remaining), "Цена": f"{qty(l.price)} {l.currency}",
        "Доп. затраты": qty(l.fees), "Курс $ при покупке": str(l.fx_usd_ils), "Откуда": kinds.get(l.kind, l.kind),
        "Заметка": l.note or "",
    } for l in lots], height=320)
    deletable = [l for l in lots if not l.disposals]
    if deletable:
        with st.form("del_lot"):
            lot_id = st.selectbox(
                "Удалить ошибочный лот", [l.id for l in deletable],
                format_func=lambda i: next(f"№{l.id} · {l.asset.symbol} · {l.buy_date:%d.%m.%Y} · {qty(l.qty)}"
                                           for l in deletable if l.id == i))
            if st.form_submit_button("Удалить лот"):
                try:
                    with Session() as s:
                        ledger.delete_lot(s, lot_id)
                        s.commit()
                    flash("Лот удалён")
                    st.rerun()
                except ledger.LedgerError as exc:
                    st.error(str(exc))


def render() -> None:
    st.title("Активы и операции")
    show_flash()
    tabs = st.tabs(["Купить", "Продать", "Перевести", "Обменять", "Активы", "Журнал"])
    for tab, fn in zip(tabs, (tab_buy, tab_sell, tab_transfer, tab_swap, tab_assets, tab_journal)):
        with tab:
            fn()
