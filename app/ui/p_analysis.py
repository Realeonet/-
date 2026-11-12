"""Экраны «Сравнение активов» и «Песочница» (сценарии «а что если»). Реальные данные не меняются."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal

import pandas as pd
import streamlit as st

from app.adapters.base import SourceError
from app.adapters.metals import METALS
from app.db import Asset, Custodian, Scenario
from app.domain import minkin
from app.domain.money import HUNDRED, ZERO, D
from app.domain.returns import cagr, dca_backtest, dca_projection, total_return, trailing_yield
from app.domain.risk import FeeSchedule, trade_fee
from app.domain.tax_il import net_of_tax, simulate_sale
from app.services import ledger
from app.services.portfolio import load_positions, totals
from app.ui import charts
from app.ui.common import (Session, assets, cfg, custodians, dec_input, flash, ils, market, offline, parse_dec,
                           pct, qty, show_flash, table, tax_method, tax_note, tax_rate, today, usd)

OTHER = "Другой тикер Yahoo…"


def yahoo_ticker(asset: Asset) -> str | None:
    if asset.price_source == "yfinance":
        return asset.source_id
    if asset.price_source == "coingecko":
        return asset.history_ticker or None
    if asset.price_source == "metal":
        return METALS.get(asset.source_id, (None, None, None))[2]
    return None


def load_series(ticker: str, since: date, adjusted: bool) -> pd.Series:
    """История тикера из кэша; если её не хватает и есть сеть — докачивает."""
    m = market()
    key = ("adj:" if adjusted else "") + f"yfinance:{ticker}"
    series = m.history(key, since)
    fresh = len(series) and (today() - series.index[-1].date()).days <= 4
    covers = len(series) and series.index[0].date() <= since + timedelta(days=10)
    if not (fresh and covers) and not offline():
        try:
            m.refresh_ticker_history(ticker, since, adjusted)
            series = m.history(key, since)
        except SourceError as exc:
            st.warning(str(exc))
    return series


def pick_instrument(label: str, key: str, default_ticker: str = "", position: int = 0) -> tuple[str, str, Asset | None] | None:
    """Выбор из своих активов или произвольный тикер Yahoo. Возвращает (подпись, тикер, актив)."""
    own = [a for a in assets() if yahoo_ticker(a)]
    options = [a.symbol for a in own] + [OTHER]
    index = len(own) if default_ticker or not own else min(position, len(own) - 1)
    choice = st.selectbox(label, options, index=index, key=f"{key}_sel")
    if choice == OTHER:
        ticker = st.text_input("Тикер Yahoo", value=default_ticker, key=f"{key}_tkr",
                               help="Примеры: SPYI, CSPX.L, JEIP.L, ^GSPC, BTC-USD, SOL-USD, GC=F (золото), SI=F (серебро)").strip()
        return (ticker, ticker, None) if ticker else None
    asset = next(a for a in own if a.symbol == choice)
    return asset.symbol, yahoo_ticker(asset), asset


# ======================= сравнение =======================

def render_compare() -> None:
    st.title("Сравнение активов")
    show_flash()
    st.caption("Две-три позиции рядом: полная доходность (с реинвестированием выплат), текущая доходность по "
               "выплатам, комиссия фонда и доход после налога. Примеры: SPYI vs CSPX.L vs JEIP.L; BTC-USD vs ^GSPC; "
               "SOL-USD vs ETH-USD; SI=F vs GC=F.")
    cols = st.columns(3)
    defaults = ["", "", ""]
    picks = []
    for i, col in enumerate(cols):
        with col:
            enabled = st.checkbox(f"Позиция {i + 1}", value=i < 2, key=f"cmp_on_{i}")
            if enabled:
                picked = pick_instrument("Актив", f"cmp_{i}", defaults[i], position=i)
                if picked:
                    picks.append(picked)
    if len(picks) < 2:
        st.info("Выберите хотя бы две позиции.")
        return
    if len({p[1] for p in picks}) < len(picks):
        st.warning("Выбраны одинаковые позиции.")
        return
    day, rate, m = today(), tax_rate(), market()
    since = date(day.year - 5, day.month, 1) - timedelta(days=40)
    rows, norm = [], {}
    for label, ticker, asset in picks:
        adj = load_series(ticker, since, adjusted=True)
        if adj.empty:
            rows.append({"Позиция": label, "Статус": "нет данных — проверьте тикер"})
            continue
        divs = pd.Series(dtype=float)
        profile: dict = {}
        cache_key = f"cmp_meta_{ticker}"
        if cache_key not in st.session_state and not offline():
            try:
                st.session_state[cache_key] = (m.sources.yahoo.dividends(ticker), m.sources.yahoo.profile(ticker))
            except SourceError:
                st.session_state[cache_key] = (pd.Series(dtype=float), {})
        if cache_key in st.session_state:
            divs, profile = st.session_state[cache_key]
        raw = load_series(ticker, day - timedelta(days=30), adjusted=False)
        price = D(float(raw.iloc[-1])) if len(raw) else D(float(adj.iloc[-1]))
        y = trailing_yield(divs, price, day)
        tr = {n: total_return(adj, n, day) for n in (1, 3, 5)}
        expense = asset.expense_ratio_pct if asset and asset.expense_ratio_pct is not None \
            else profile.get("expense_ratio_pct")
        dist = asset.dist_type if asset else profile.get("dist_type", "na")
        rows.append({
            "Позиция": label, "Название": (asset.name if asset else profile.get("name", "")) or "—",
            "Доходность 1 год": pct(tr[1], signed=True), "3 года": pct(tr[3], signed=True),
            "5 лет": pct(tr[5], signed=True),
            "В среднем за год (5 лет)": pct(cagr(tr[5], 5), signed=True),
            "Выплаты за 12 мес": pct(y), f"Выплаты нетто (−{pct(rate)})": pct(net_of_tax(y, rate) if y else y),
            "Комиссия фонда": f"{expense}%" if expense is not None else "—",
            "Выплаты": {"acc": "Acc — накопительный", "dist": "Dist — выплачивает"}.get(dist, "—"),
            "Домициль": (asset.domicile if asset else "") or "—",
            "US-situs": "да" if asset and asset.us_situs else ("—" if asset is None else "нет"),
        })
        norm[label] = adj
    table(rows)
    if len(norm) >= 2:
        start = max(s.index[0] for s in norm.values())
        st.plotly_chart(charts.lines(
            {k: s[s.index >= start] / float(s[s.index >= start].iloc[0]) * 100 for k, s in norm.items()},
            "Рост 100 единиц с учётом реинвестирования выплат (с общей даты начала)"), width="stretch")
    st.caption("Полная доходность — в валюте котировки, до налогов. «—» означает, что истории не хватает. "
               "У накопительных фондов выплат нет: налог возникает только при продаже. Домициль и US-situs "
               "показываются для активов из вашего справочника.")
    tax_note()


# ======================= песочница =======================

def _save_scenario_ui(kind: str, default_name: str, params: dict, summary: dict, series: dict[str, pd.Series]) -> None:
    with st.form(f"save_{kind}"):
        name = st.text_input("Название сценария", value=default_name)
        if st.form_submit_button("💾 Сохранить сценарий"):
            payload = {
                "summary": summary,
                "series": {k: [[ts.strftime("%Y-%m-%d"), float(v)] for ts, v in s.items()] for k, s in series.items()},
            }
            with Session() as s:
                s.add(Scenario(name=name.strip() or default_name, kind=kind, params=json.dumps(params, default=str),
                               result=json.dumps(payload, ensure_ascii=False), created_at=datetime.now()))
                s.commit()
            flash("Сценарий сохранён — см. вкладку «Сохранённые»")
            st.rerun()


def _tab_price() -> None:
    st.markdown("**Если цена актива станет X** — стоимость, прибыль, налог при продаже всего и сумма на руки.")
    m, day, rate = market(), today(), tax_rate()
    held = []
    with Session() as s:
        for a in s.query(Asset).order_by(Asset.symbol).all():
            if ledger.open_lots(s, a.id):
                held.append(a)
    if not held:
        st.info("Нет открытых лотов. Сценарий считается по вашим реальным позициям.")
        return
    a, b = st.columns(2)
    asset = a.selectbox("Актив", held, format_func=lambda x: f"{x.symbol} — {x.name}", key="wi_asset")
    info = m.price_info(asset)
    currency = info.currency if info else asset.currency
    unit = " за грамм" if asset.asset_class == "metal" else ""
    target_raw = dec_input(f"Цена станет, {currency}{unit}", f"wi_price_{asset.id}", container=b,
                           help=f"Сейчас: {qty(info.price)} {currency}" if info else None)
    fx_ccy, fx_usd = m.fx(currency, day), m.usd_ils(day)
    try:
        target = parse_dec(target_raw, "Цена", positive=True)
        if not fx_ccy or not fx_usd:
            raise ValueError("Нет курса валюты — обновите данные")
    except ValueError as exc:
        st.warning(str(exc))
        return
    adj = (HUNDRED + (asset.valuation_adj_pct or ZERO)) / HUNDRED
    with Session() as s:
        lots = ledger.open_lots(s, asset.id)
        alloc = [(ledger.lot_state(lot), lot.qty_remaining) for lot in lots]
        positions = load_positions(s, m, day)
    sim = simulate_sale(alloc, target * adj, ZERO, currency, fx_ccy, fx_usd, rate, tax_method())
    total_qty = sum((q for _, q in alloc), ZERO)
    now_total = totals(positions)
    now_asset = sum((p.value_usd or ZERO for p in positions if p.asset_id == asset.id), ZERO)
    new_total = now_total.value_usd - now_asset + sim.proceeds_usd
    k1, k2, k3, k4 = st.columns(4)
    k1.metric(f"Позиция ({qty(total_qty)} {asset.symbol})", usd(sim.proceeds_usd), ils(sim.proceeds_ils),
              delta_color="off", delta_arrow="off")
    k2.metric("Прибыль", usd(sum((g.gain_usd for g in sim.gains), ZERO)), ils(sim.taxable_ils), delta_color="off", delta_arrow="off")
    k3.metric(f"Налог {pct(rate)} при продаже", ils(sim.tax_ils), usd(sim.tax_ils / fx_usd), delta_color="off", delta_arrow="off")
    k4.metric("На руки", usd(sim.net_usd), ils(sim.net_ils), delta_color="off", delta_arrow="off")
    st.metric("Весь портфель при этой цене", usd(new_total),
              f"{usd(new_total - now_total.value_usd)} к текущим {usd(now_total.value_usd)}")
    st.caption("Курс валюты принят сегодняшним; при другом курсе на дату продажи налог в шекелях изменится. "
               + ("Учтена поправка к споту из карточки актива. " if asset.valuation_adj_pct else ""))
    tax_note()
    summary = {"Актив": asset.symbol, "Цена": f"{target} {currency}", "Стоимость": usd(sim.proceeds_usd),
               "Налог": ils(sim.tax_ils), "На руки": usd(sim.net_usd)}
    _save_scenario_ui("price", f"{asset.symbol} = {target}", {"asset": asset.symbol, "target": str(target)},
                      summary, {})


def _period_inputs(key: str) -> tuple[date, date]:
    day = today()
    years = st.slider("Период бэктеста, лет", 1, 10, 3, key=f"{key}_years")
    return date(day.year - years, day.month, 1), day


def _tab_dca() -> None:
    st.markdown("**Симулятор DCA** — регулярная покупка на фиксированную сумму: что было бы на исторических ценах "
                "и прогноз при заданном росте.")
    picked = pick_instrument("Актив", "dca", "BTC-USD")
    if not picked:
        return
    label, ticker, _asset = picked
    a, b = st.columns(2)
    amount_raw = dec_input("Сумма в месяц (в валюте актива)", "dca_amount", "500", container=a)
    growth_raw = dec_input("Прогноз: рост цены, % годовых", "dca_growth", "8", container=b)
    start, end = _period_inputs("dca")
    try:
        amount = parse_dec(amount_raw, "Сумма в месяц", positive=True)
        growth = Decimal((growth_raw or "0").strip().replace(",", "."))
    except (ValueError, ArithmeticError):
        st.warning("Проверьте сумму и процент роста.")
        return
    series = load_series(ticker, start - timedelta(days=10), adjusted=True)
    if series.empty:
        st.warning("Нет истории цен для этого актива.")
        return
    result = dca_backtest(series, start, end, amount)
    if not result.points:
        st.warning("В выбранном периоде нет торговых дней.")
        return
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Вложено", usd(result.invested))
    k2.metric("Стоило бы сейчас", usd(result.value))
    k3.metric("Результат", usd(result.gain), pct(result.gain_pct, signed=True))
    k4.metric("Покупок", len(result.points))
    idx = pd.to_datetime([p.day for p in result.points])
    held = pd.Series([float(p.units) for p in result.points], index=idx)
    daily = series[series.index >= idx[0]]
    value_line = held.reindex(daily.index, method="ffill") * daily
    invested_line = pd.Series([float(p.invested) for p in result.points], index=idx).reindex(daily.index,
                                                                                            method="ffill")
    st.plotly_chart(charts.lines({"Стоимость": value_line, "Вложено": invested_line},
                                 f"Бэктест: {label}, {amount} в месяц"), width="stretch")
    months = len(result.points)
    proj = dca_projection(amount, months, growth)
    proj_idx = pd.date_range(today(), periods=months, freq="MS")
    proj_value = pd.Series([float(v) for _, _, v in proj], index=proj_idx)
    proj_inv = pd.Series([float(i) for _, i, _ in proj], index=proj_idx)
    st.plotly_chart(charts.lines({"Прогноз стоимости": proj_value, "Вложено": proj_inv},
                                 f"Прогноз на {months} мес. при росте {growth}% в год"), width="stretch")
    st.caption(f"Прогноз: через {months} мес. вложено {usd(proj[-1][1])}, стоимость {usd(proj[-1][2])}. "
               "Постоянный рост — допущение; реальная цена так не движется. Суммы до налогов и комиссий.")
    _save_scenario_ui("dca", f"DCA {label} {amount}/мес", {"ticker": ticker, "amount": str(amount)},
                      {"Актив": label, "Вложено": usd(result.invested), "Стоимость": usd(result.value),
                       "Результат": pct(result.gain_pct, signed=True)},
                      {f"DCA {label}": value_line})


def _tab_rule() -> None:
    st.markdown("**Крипто-правило с порогом** — ежемесячная покупка: выше порога цены покупаю меньше, "
                "на пороге или ниже — полную сумму. Сравнение с покупкой всегда на полную сумму.")
    picked = pick_instrument("Монета", "rule", "BTC-USD")
    if not picked:
        return
    label, ticker, _asset = picked
    a, b, c = st.columns(3)
    full_raw = dec_input("Полная сумма в месяц, $", "rule_full", cfg("dca_full") or "0", container=a)
    red_raw = dec_input("Уменьшенная сумма, $", "rule_red", cfg("dca_reduced") or "0", container=b)
    thr_raw = dec_input("Порог цены, $", "rule_thr", cfg("dca_threshold") or "0", container=c)
    start, end = _period_inputs("rule")
    try:
        full = parse_dec(full_raw, "Полная сумма", positive=True)
        reduced = parse_dec(red_raw, "Уменьшенная сумма")
        threshold = parse_dec(thr_raw, "Порог", positive=True)
    except ValueError as exc:
        st.info(f"{exc}. Значения по умолчанию берутся из «Алгоритма Минкина».")
        return
    series = load_series(ticker, start - timedelta(days=10), adjusted=False)
    if series.empty:
        st.warning("Нет истории цен.")
        return
    ruled = dca_backtest(series, start, end, lambda _d, price: minkin.dca_amount(price, threshold, full, reduced))
    flat = dca_backtest(series, start, end, full)
    if not ruled.points:
        st.warning("В выбранном периоде нет торговых дней.")
        return
    rows = []
    for name, r in (("С правилом", ruled), ("Всегда полная сумма", flat)):
        rows.append({"Вариант": name, "Вложено": usd(r.invested), "Куплено монет": qty(r.units.quantize(Decimal("0.00000001"))),
                     "Стоит сейчас": usd(r.value), "Результат": usd(r.gain),
                     "Результат, %": pct(r.gain_pct, signed=True),
                     "Средняя цена покупки": usd(r.invested / r.units) if r.units else "—"})
    table(rows)
    reduced_months = sum(1 for p in ruled.points if p.amount == reduced)
    st.caption(f"Месяцев с уменьшенной покупкой: {reduced_months} из {len(ruled.points)}. "
               f"Правило сберегло {usd(flat.invested - ruled.invested)} наличных, которые можно было направить иначе.")
    idx = pd.to_datetime([p.day for p in ruled.points])
    daily = series[series.index >= idx[0]]
    lines = {}
    for name, r in (("С правилом", ruled), ("Всегда полная сумма", flat)):
        units = pd.Series([float(p.units) for p in r.points], index=pd.to_datetime([p.day for p in r.points]))
        lines[name] = units.reindex(daily.index, method="ffill") * daily
    st.plotly_chart(charts.lines(lines, f"Стоимость накопленного: {label}", " $"), width="stretch")
    _save_scenario_ui("rule", f"Правило {label} порог {threshold}",
                      {"ticker": ticker, "full": str(full), "reduced": str(reduced), "threshold": str(threshold)},
                      {"Вложено": usd(ruled.invested), "Стоимость": usd(ruled.value),
                       "Результат": pct(ruled.gain_pct, signed=True)},
                      {f"Правило {label}": lines["С правилом"]})


def _tab_realloc() -> None:
    st.markdown("**Перераспределение** — продать долю одного актива и купить другой: налог, комиссии, "
                "новые доли и ожидаемый доход.")
    m, day, rate = market(), today(), tax_rate()
    with Session() as s:
        positions = load_positions(s, m, day)
        asset_map = {a.id: a for a in s.query(Asset).all()}
        cust_map = {c.id: c for c in s.query(Custodian).all()}
    priced = [p for p in positions if p.value_usd is not None]
    if not priced:
        st.info("Нет оценённых позиций.")
        return
    pairs = sorted({(p.asset_id, p.custodian_id) for p in priced},
                   key=lambda k: (asset_map[k[0]].symbol, cust_map[k[1]].name))
    a, b = st.columns(2)
    src = a.selectbox("Продаю", pairs, key="ra_src",
                      format_func=lambda k: f"{asset_map[k[0]].symbol} · {cust_map[k[1]].name}")
    share = b.slider("Какую долю позиции", 5, 100, 50, step=5, key="ra_share")
    targets = [x for x in asset_map.values() if x.id != src[0] and m.price_info(x) is not None]
    if not targets:
        st.info("Нужен второй актив с ценой.")
        return
    c, d = st.columns(2)
    dst = c.selectbox("Покупаю", targets, format_func=lambda x: f"{x.symbol} — {x.name}", key="ra_dst")
    venues = [x for x in custodians() if x.kind not in ("institution",)]
    dst_cust = d.selectbox("Где покупаю", venues, format_func=lambda x: x.name, key="ra_cust")

    asset = asset_map[src[0]]
    info = m.price_info(asset)
    fx_ccy, fx_usd = m.fx(info.currency, day), m.usd_ils(day)
    with Session() as s:
        lots = ledger.open_lots(s, asset.id, src[1])
        states = [ledger.lot_state(lot) for lot in lots]
    available = sum((x.remaining for x in states), ZERO)
    sell_qty = available * share / 100
    adj = (HUNDRED + (asset.valuation_adj_pct or ZERO)) / HUNDRED

    def fee_usd(cust: Custodian, value: Decimal) -> Decimal:
        fx = m.fx(cust.fee_currency, day) or ZERO
        k = fx / fx_usd
        return trade_fee(FeeSchedule(cust.name, cust.fee_trade * k, cust.fee_trade_pct, cust.fee_trade_min * k), value)

    gross_usd = sell_qty * info.price * adj * fx_ccy / fx_usd
    sell_fee = fee_usd(cust_map[src[1]], gross_usd)
    sim = simulate_sale(ledger.fifo_allocate(states, sell_qty), info.price * adj, sell_fee * fx_usd / fx_ccy,
                        info.currency, fx_ccy, fx_usd, rate, tax_method())
    after_tax_usd = sim.net_usd
    buy_fee = fee_usd(dst_cust, after_tax_usd)
    invest_usd = after_tax_usd - buy_fee
    dst_info = m.price_info(dst)
    dst_fx = m.fx(dst_info.currency, day)
    dst_adj = (HUNDRED + (dst.valuation_adj_pct or ZERO)) / HUNDRED
    dst_units = invest_usd * fx_usd / (dst_info.price * dst_adj * dst_fx) if dst_fx else ZERO

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Продаю", usd(gross_usd), f"{qty(sell_qty)} {asset.symbol}", delta_color="off", delta_arrow="off")
    k2.metric(f"Налог {pct(rate)}", usd(sim.tax_ils / fx_usd), ils(sim.tax_ils), delta_color="off", delta_arrow="off")
    k3.metric("Комиссии", usd(sell_fee + buy_fee))
    k4.metric("Покупаю", usd(invest_usd), f"{qty(dst_units.quantize(Decimal('0.0001')))} {dst.symbol}",
              delta_color="off", delta_arrow="off")
    lost = gross_usd - invest_usd
    st.caption(f"Потери на перекладке: {usd(lost)} ({pct(lost / gross_usd) if gross_usd else '—'} от суммы) — "
               "налог и комиссии. Продажа считается по FIFO.")

    def yield_of(x: Asset) -> Decimal:
        price = m.price_info(x)
        if x.dist_type == "acc" or price is None:
            return ZERO
        y = trailing_yield(m.dividends(x), price.price, day) or ZERO
        if y == 0 and x.manual_yield_pct:
            y = x.manual_yield_pct / 100
        return y

    total = totals(priced).value_usd
    before = {}
    for p in priced:
        before[p.symbol] = before.get(p.symbol, ZERO) + p.value_usd
    after = dict(before)
    after[asset.symbol] = after[asset.symbol] - gross_usd
    after[dst.symbol] = after.get(dst.symbol, ZERO) + invest_usd
    new_total = sum(after.values(), ZERO)
    names = sorted(after, key=lambda k: -after[k])
    table([{"Актив": n, "Доля до": pct(before.get(n, ZERO) / total) if total else "—",
            "Доля после": pct(after[n] / new_total) if new_total else "—",
            "Стоимость после, $": usd(after[n])} for n in names])
    income_before = gross_usd * yield_of(asset)
    income_after = invest_usd * yield_of(dst)
    i1, i2, i3 = st.columns(3)
    i1.metric("Доход с этой суммы до, нетто/год", usd(net_of_tax(income_before, rate)))
    i2.metric("После, нетто/год", usd(net_of_tax(income_after, rate)))
    i3.metric("Изменение дохода", usd(net_of_tax(income_after - income_before, rate)))
    st.caption("Доход — по выплатам за последние 12 месяцев (или по ручной доходности); у накопительных фондов "
               "и крипты выплат нет. Реальные данные не меняются.")
    tax_note()
    _save_scenario_ui("realloc", f"{share}% {asset.symbol} → {dst.symbol}",
                      {"from": asset.symbol, "to": dst.symbol, "share": share},
                      {"Продаю": usd(gross_usd), "Налог": usd(sim.tax_ils / fx_usd), "Комиссии": usd(sell_fee + buy_fee),
                       "Покупаю": usd(invest_usd), "Потери": usd(lost)}, {})


def _tab_saved() -> None:
    with Session() as s:
        scenarios = s.query(Scenario).order_by(Scenario.created_at.desc()).all()
    if not scenarios:
        st.info("Сохранённых сценариев нет. Сохраните расчёт на любой вкладке — он появится здесь.")
        return
    kinds = {"price": "Цена X", "dca": "DCA", "rule": "Крипто-правило", "realloc": "Перераспределение"}
    table([{"Название": sc.name, "Тип": kinds.get(sc.kind, sc.kind),
            "Создан": sc.created_at.strftime("%d.%m.%Y %H:%M"),
            **json.loads(sc.result).get("summary", {})} for sc in scenarios])
    with_series = [sc for sc in scenarios if json.loads(sc.result).get("series")]
    if with_series:
        chosen = st.multiselect("Сравнить на одном графике", with_series, default=with_series[:3],
                                format_func=lambda sc: sc.name, max_selections=6)
        lines = {}
        for sc in chosen:
            for name, points in json.loads(sc.result)["series"].items():
                lines[f"{sc.name}"] = pd.Series([p[1] for p in points], index=pd.to_datetime([p[0] for p in points]))
        if lines:
            st.plotly_chart(charts.lines(lines, "Стоимость накопленного по сценариям"), width="stretch")
    with st.form("sc_delete"):
        victim = st.selectbox("Удалить сценарий", scenarios, format_func=lambda sc: sc.name)
        if st.form_submit_button("Удалить"):
            with Session() as s:
                s.delete(s.get(Scenario, victim.id))
                s.commit()
            flash("Сценарий удалён")
            st.rerun()


def render_sandbox() -> None:
    st.title("Песочница: «а что если»")
    show_flash()
    st.caption("Сценарии не меняют реальные данные. Любой расчёт можно сохранить и вернуться к нему позже.")
    tabs = st.tabs(["Если цена станет X", "DCA", "Крипто-правило", "Перераспределение", "Сохранённые"])
    for tab, fn in zip(tabs, (_tab_price, _tab_dca, _tab_rule, _tab_realloc, _tab_saved)):
        with tab:
            fn()
