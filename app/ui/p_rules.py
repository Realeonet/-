"""Экраны: «Наследство США», «Алгоритм Минкина», «Комиссии», «Долг vs инвестиции»."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal

import pandas as pd
import streamlit as st

from app.db import Asset, Disposal, Lot
from app.domain import minkin
from app.domain.money import HUNDRED, ZERO
from app.domain.risk import (FeeSchedule, cheapest_venues, consolidation_saving, debt_vs_invest, estate_status,
                             total_fees, trade_fee)
from app.services.dividends import forecast, upcoming_ex_dates
from app.services.portfolio import group_positions, load_positions, us_situs_by_owner
from app.ui import charts
from app.ui.common import (Session, assets, blacklist, cfg, cfg_dec, cfg_int, cfg_set, custodians, dec_input,
                           flash, ils, market, owners, parse_dec, pct, qty, show_flash, table, tax_rate, today,
                           usd)

WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")

# ======================= наследство США =======================


def render_estate() -> None:
    st.title("Монитор налога США на наследство")
    show_flash()
    st.caption("Для нерезидента США американские бумаги (US-situs: акции и ETF с домицилем в США) сверх "
               "порога облагаются налогом на наследство. Ирландские UCITS-фонды, криптовалюта и физические "
               "металлы в эту сумму не входят. Ориентировочно — сверить с консультантом.")
    m, day = market(), today()
    limit, warn = cfg_dec("estate_limit_usd"), cfg_dec("estate_warn_usd")
    with Session() as s:
        positions = load_positions(s, m, day)
    by_owner = us_situs_by_owner(positions)
    for owner in owners():
        status = estate_status(by_owner.get(owner.name, {}), limit, warn)
        with st.container(border=True):
            st.subheader(owner.name)
            a, b, c = st.columns(3)
            a.metric("US-situs бумаги, $", usd(status.total_usd))
            b.metric("До рабочего порога", usd(max(ZERO, warn - status.total_usd)))
            c.metric("До лимита", usd(max(ZERO, limit - status.total_usd)))
            if status.level == "over":
                st.error(f"🚨 Лимит {usd(limit)} превышен на {usd(status.over_limit_usd)}.")
            elif status.level == "warn":
                st.warning(f"⚠️ Рабочий порог {usd(warn)} превышен на {usd(status.over_warn_usd)}. "
                           f"До лимита {usd(limit)} осталось {usd(limit - status.total_usd)}.")
            else:
                st.success(f"✅ Ниже рабочего порога {usd(warn)}.")
            if status.by_custodian:
                names = sorted(status.by_custodian, key=lambda k: -status.by_custodian[k])
                st.plotly_chart(charts.hbar(
                    names, [float(status.by_custodian[n]) for n in names], "US-situs по местам хранения, $", " $"),
                    width="stretch")
                mine = [p for p in positions if p.owner == owner.name and p.us_situs]
                table([{"Бумага": g.name, "Стоимость, $": usd(g.value_usd), "Лотов": g.lots}
                       for g in group_positions(mine, lambda p: f"{p.symbol} · {p.custodian}")])
            if status.plan_to_warn:
                st.markdown("**Чтобы вернуться ниже рабочего порога, нужно убрать из US-situs:**")
                table([{"Место хранения": name, "Сумма, $": usd(amount)} for name, amount in status.plan_to_warn])
                st.caption("«Убрать» = продать и заменить не-американским аналогом (например, ирландским UCITS-"
                           "фондом) или передать другому владельцу. Продажа — это налог на прибыль: оцените его в "
                           "разделе «Налоги → Симулятор продажи». Простой перевод тех же бумаг к другому брокеру "
                           "сумму US-situs не уменьшает.")
    with st.expander("Пороги"):
        with st.form("estate_cfg"):
            a, b = st.columns(2)
            limit_raw = dec_input("Лимит, $", "est_limit", str(limit), container=a)
            warn_raw = dec_input("Рабочий порог предупреждения, $", "est_warn", str(warn), container=b)
            if st.form_submit_button("Сохранить"):
                try:
                    new_limit = parse_dec(limit_raw, "Лимит", positive=True)
                    new_warn = parse_dec(warn_raw, "Порог", positive=True)
                    if new_warn > new_limit:
                        raise ValueError("Рабочий порог не может быть выше лимита")
                    cfg_set(estate_limit_usd=new_limit, estate_warn_usd=new_warn)
                    flash("Пороги сохранены")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
    st.caption("Денежные остатки у американского брокера и другие виды имущества здесь не учитываются — проверить.")


# ======================= алгоритм Минкина =======================

def _sma_section(m, day) -> None:  # noqa: ANN001
    window = cfg_int("sma_window")
    st.subheader(f"Цена относительно SMA {window}")
    rows, histories = [], {}
    for a in assets():
        if a.asset_class not in ("stock", "etf", "crypto", "metal"):
            continue
        hist = m.asset_history(a, day - timedelta(days=window * 3))
        histories[a.symbol] = hist
        sig = minkin.sma_signal(list(hist.values), window) if len(hist) else None
        if sig is None:
            rows.append({"Актив": a.symbol, "Сигнал": "мало истории — обновите данные", "Цена": "—",
                         f"SMA {window}": "—", "Отклонение": "—"})
            continue
        rows.append({
            "Актив": a.symbol, "Сигнал": "🟢 над линией" if sig.above else "🔴 под линией",
            "Цена": qty(sig.price), f"SMA {window}": qty(sig.sma.quantize(Decimal("0.0001"))),
            "Отклонение": pct(sig.distance, signed=True),
        })
    table(rows)
    with_hist = [s for s, h in histories.items() if len(h) >= window]
    if with_hist:
        symbol = st.selectbox("График", with_hist, key="sma_chart")
        hist = histories[symbol]
        sma = hist.rolling(window).mean()
        tail = hist.index >= hist.index[-1] - pd.Timedelta(days=365)
        st.plotly_chart(charts.lines({symbol: hist[tail], f"SMA {window}": sma[tail]}, None, digits=4),
                        width="stretch")
        st.caption("Для металлов история — по фьючерсам, цена за грамм.")


def _window_section(day) -> None:  # noqa: ANN001
    after, until = cfg_int("buy_window_after"), cfg_int("buy_window_until")
    st.subheader("Окно покупки (рынок США)")
    w = minkin.buy_window(day, after, until)
    state = minkin.window_state(datetime.now(minkin.IL), w)
    text = {
        "open": f"🟢 Окно открыто сейчас: до {w.end_il:%H:%M}",
        "before": f"🕒 Сегодня окно: {w.start_il:%H:%M}–{w.end_il:%H:%M} по Израилю",
        "after": f"Сегодняшнее окно ({w.start_il:%H:%M}–{w.end_il:%H:%M}) уже прошло",
        "closed": "Сегодня выходной — рынок США закрыт",
    }[state]
    st.info(text + f". Открытие рынка — {w.open_il:%H:%M} по Израилю.")
    upcoming = []
    d = day
    while len(upcoming) < 5:
        d += timedelta(days=1)
        nxt = minkin.buy_window(d, after, until)
        if nxt.is_weekday:
            upcoming.append({"День": f"{WEEKDAYS[d.weekday()]} {d:%d.%m}", "Открытие рынка": nxt.open_il.strftime("%H:%M"),
                             "Окно покупки": f"{nxt.start_il:%H:%M}–{nxt.end_il:%H:%M}"})
    table(upcoming)
    st.caption(f"Окно: через {after}–{until} минут после открытия. Переход на летнее/зимнее время в США и в "
               "Израиле учитывается автоматически (в недели, когда страны переводят часы не одновременно, окно "
               "сдвигается на час). Праздники биржи не учитываются.")


def _rebalance_section(positions, m, day) -> None:  # noqa: ANN001
    st.subheader("Целевая пропорция и ребалансировка")
    try:
        targets = {k.upper(): Decimal(str(v)) for k, v in json.loads(cfg("rebalance_targets")).items()}
    except (ValueError, ArithmeticError):
        targets = {}
    values: dict[str, Decimal] = {}
    for p in positions:
        if p.symbol in targets and p.value_usd is not None:
            values[p.symbol] = values.get(p.symbol, ZERO) + p.value_usd
    cash_raw = dec_input("Новые деньги для покупки, $", "rb_cash", "0")
    try:
        cash = parse_dec(cash_raw, "Новые деньги") or ZERO
        rows = minkin.rebalance(values, targets, cash) if targets else []
    except ValueError as exc:
        st.warning(str(exc))
        rows = []
    if rows:
        table([{
            "Фонд": r.symbol, "Сейчас, $": usd(r.value), "Доля": pct(r.share), "Цель": pct(r.target),
            "Отклонение": pct(r.drift, signed=True), "Докупить из новых денег, $": usd(r.buy_with_new_cash),
            "Для точной цели, $": ("купить " if r.trade_to_target >= 0 else "продать ") + usd(abs(r.trade_to_target)),
        } for r in rows])
        st.caption("«Докупить из новых денег» выравнивает доли без продаж (продажа = налог 25% с прибыли). "
                   "«Для точной цели» — полный ребаланс с учётом новых денег.")
    with st.expander("Изменить целевые доли"):
        with st.form("rb_cfg"):
            text = st.text_input("Фонды и доли, %", value=", ".join(f"{k}={v}" for k, v in targets.items()),
                                 help="Формат: SPYI=50, JEPI=50. Сумма должна быть 100.")
            if st.form_submit_button("Сохранить"):
                try:
                    parsed = {}
                    for part in text.split(","):
                        if part.strip():
                            sym, _, val = part.partition("=")
                            parsed[sym.strip().upper()] = str(parse_dec(val, f"доля {sym.strip()}"))
                    if sum((Decimal(v) for v in parsed.values()), ZERO) != HUNDRED:
                        raise ValueError("Сумма долей должна быть ровно 100%")
                    cfg_set(rebalance_targets=json.dumps(parsed))
                    flash("Целевые доли сохранены")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))


def _xday_section(positions, m, day) -> None:  # noqa: ANN001
    days = cfg_int("xday_days")
    st.subheader("X-day: когда не покупать")
    with Session() as s:
        asset_map = {a.id: a for a in s.query(Asset).all()}
    rows = []
    for a in asset_map.values():
        if a.asset_class not in ("stock", "etf") or a.dist_type == "acc":
            continue
        ex = upcoming_ex_dates(a, m, day, 45)
        if not ex:
            continue
        left = (ex[0].day - day).days
        rows.append((left, {
            "Фонд": a.symbol, "Ближайший X-day": ex[0].day.strftime("%d.%m.%Y"), "Через, дн.": left,
            "Источник": "прогноз" if ex[0].estimated else "вручную",
            "Правило": "⛔ не покупать" if minkin.xday_warning(ex[0].day, day, days) else "✅ можно",
        }))
    table([r for _, r in sorted(rows, key=lambda x: x[0])])
    st.caption(f"Правило: не покупать за {days} дн. до X-day — иначе сразу придёт выплата, с которой удержат 25%, "
               "а цена пая упадёт на её размер.")


def _dca_section(m) -> None:  # noqa: ANN001
    st.subheader("Крипто-DCA с порогом по цене")
    full, reduced, threshold = cfg_dec("dca_full"), cfg_dec("dca_reduced"), cfg_dec("dca_threshold")
    symbol = cfg("dca_asset").upper()
    asset = next((a for a in assets() if a.symbol == symbol), None)
    info = m.price_info(asset) if asset else None
    if full == 0 or threshold == 0:
        st.info("Задайте свои суммы и порог ниже — приложение будет подсказывать размер ежемесячной покупки.")
    elif info is None:
        st.warning(f"Нет цены {symbol}. Создайте актив {symbol} и обновите данные.")
    else:
        amount = minkin.dca_amount(info.price, threshold, full, reduced)
        above = info.price > threshold
        st.metric(f"Покупка {symbol} в этом месяце", usd(amount),
                  f"{symbol} = {usd(info.price)} — {'выше' if above else 'не выше'} порога {usd(threshold)}",
                  delta_color="off", delta_arrow="off")
        st.caption("Выше порога — уменьшенная сумма; на пороге или ниже — полная. "
                   "Бэктест правила — в разделе «Песочница».")
    with st.expander("Настроить правило"):
        with st.form("dca_cfg"):
            a, b, c, d = st.columns(4)
            sym = a.text_input("Монета-ориентир", value=symbol)
            full_raw = dec_input("Полная сумма в месяц, $", "dca_full_in", str(full), container=b)
            red_raw = dec_input("Уменьшенная сумма, $", "dca_red_in", str(reduced), container=c)
            thr_raw = dec_input("Порог цены, $", "dca_thr_in", str(threshold), container=d)
            if st.form_submit_button("Сохранить"):
                try:
                    cfg_set(dca_asset=sym.strip().upper() or "BTC", dca_full=parse_dec(full_raw, "Полная сумма"),
                            dca_reduced=parse_dec(red_raw, "Уменьшенная сумма"),
                            dca_threshold=parse_dec(thr_raw, "Порог"))
                    flash("Правило сохранено")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))


def render_minkin() -> None:
    st.title("Алгоритм Минкина")
    show_flash()
    st.caption("Личные правила. Все параметры настраиваются. Это подсказки, а не сигналы к действию.")
    m, day = market(), today()
    with Session() as s:
        positions = load_positions(s, m, day)
    _window_section(day)
    _xday_section(positions, m, day)
    _sma_section(m, day)
    _rebalance_section(positions, m, day)
    _dca_section(m)
    st.subheader("Чёрный список")
    hits = sorted({p.symbol for p in positions if minkin.blacklist_hit(p.symbol, blacklist())})
    if hits:
        st.error("⛔ В портфеле есть бумаги из чёрного списка: " + ", ".join(hits))
    with st.form("bl_cfg"):
        text = st.text_input("Тикеры через запятую", value=", ".join(blacklist()),
                             help="При создании актива и при покупке появится предупреждение.")
        a, b, c, d = st.columns(4)
        sma = a.number_input("Окно SMA, дней", min_value=5, max_value=400, value=cfg_int("sma_window"))
        after = b.number_input("Окно покупки: от, мин", min_value=0, max_value=300, value=cfg_int("buy_window_after"))
        until = c.number_input("Окно покупки: до, мин", min_value=1, max_value=390, value=cfg_int("buy_window_until"))
        xd = d.number_input("Не покупать за N дней до X-day", min_value=0, max_value=10, value=cfg_int("xday_days"))
        if st.form_submit_button("Сохранить параметры"):
            if after >= until:
                st.error("Начало окна должно быть раньше конца")
            else:
                cfg_set(blacklist=text, sma_window=int(sma), buy_window_after=int(after),
                        buy_window_until=int(until), xday_days=int(xd))
                flash("Параметры сохранены")
                st.rerun()


# ======================= комиссии =======================

def _trades_this_month(day) -> dict[int, int]:  # noqa: ANN001
    start = day.replace(day=1)
    counts: dict[int, int] = {}
    with Session() as s:
        for (cid,) in s.query(Lot.custodian_id).filter(Lot.buy_date >= start, Lot.kind.in_(("buy", "import"))):
            counts[cid] = counts.get(cid, 0) + 1
        seen = set()
        for d in s.query(Disposal).filter(Disposal.date >= start).all():
            key = d.group_id or str(d.id)
            if key in seen:
                continue
            seen.add(key)
            cid = s.get(Lot, d.lot_id).custodian_id
            counts[cid] = counts.get(cid, 0) + 1
    return counts


def render_fees() -> None:
    st.title("Комиссии")
    show_flash()
    m, day = market(), today()
    cust_list = custodians()
    if not cust_list:
        st.info("Сначала добавьте места хранения и заполните их комиссии.")
        return
    used = _trades_this_month(day)
    with Session() as s:
        positions = load_positions(s, m, day)
    by_cust = {g.name: g for g in group_positions(positions, lambda p: p.custodian)}
    table([{
        "Место": c.name, "За сделку": f"{c.fee_trade} {c.fee_currency}",
        "% от суммы": f"{c.fee_trade_pct}% (мин. {c.fee_trade_min})" if c.fee_trade_pct else "—",
        "Бесплатных в месяц": c.free_trades_per_month or "—", "Сделок в этом месяце": used.get(c.id, 0),
        "Перевод / вывод": f"{c.fee_transfer} {c.fee_currency}",
        "Хранение": f"{c.fee_custody_pct}% в год" if c.fee_custody_pct else "—",
        "Хранение в год, $": usd(by_cust[c.name].value_usd * c.fee_custody_pct / 100)
        if c.fee_custody_pct and c.name in by_cust else "—",
    } for c in cust_list])
    st.caption("Комиссии редактируются в карточке места хранения.")

    st.subheader("Где выгоднее совершить сделку")
    a, b = st.columns(2)
    value_raw = dec_input("Сумма сделки, $", "fee_value", "1000", container=a)
    n = b.number_input("На сколько сделок разбить", min_value=1, max_value=50, value=1)
    usd_ils = m.usd_ils(day)
    try:
        value = parse_dec(value_raw, "Сумма сделки", positive=True)
    except ValueError as exc:
        st.warning(str(exc))
        return
    rows, schedules = [], []
    for c in cust_list:
        if c.kind in ("self_custody", "physical", "institution"):
            continue
        fx = m.fx(c.fee_currency, day)
        if fx is None or not usd_ils:
            continue
        to_usd = fx / usd_ils  # комиссии места → доллары
        sched = FeeSchedule(c.name, c.fee_trade * to_usd, c.fee_trade_pct, c.fee_trade_min * to_usd,
                            c.free_trades_per_month)
        schedules.append((sched, used.get(c.id, 0)))
        single = trade_fee(sched, value, used.get(c.id, 0))
        split = total_fees(sched, value / n, int(n), used.get(c.id, 0))
        free_left = max(0, c.free_trades_per_month - used.get(c.id, 0))
        rows.append((split, {
            "Место": c.name, "Одной сделкой, $": usd(single), f"За {int(n)} сделок, $": usd(split),
            "Доля от суммы": pct(split / value),
            "Экономия при объединении, $": usd(consolidation_saving(sched, value, int(n), used.get(c.id, 0))),
            "Осталось бесплатных": free_left if c.free_trades_per_month else "—",
        }))
    table([r for _, r in sorted(rows, key=lambda x: x[0])])
    ranked = cheapest_venues(schedules, value)
    if ranked:
        best = ranked[0]
        st.success(f"Дешевле всего: «{best[0]}» — {usd(best[1])} за сделку на {usd(value)}.")
        if n > 1:
            st.caption("Фиксированная комиссия платится за каждую сделку — выгоднее объединять покупки в одну. "
                       "Бесплатные сделки учитываются по числу операций в текущем месяце.")
    st.caption("Сравнение не учитывает спред, комиссию за конвертацию валюты и налоги.")


# ======================= долг vs инвестиции =======================

def render_debt() -> None:
    st.title("Долг vs инвестиции")
    show_flash()
    st.caption("Что выгоднее: гасить овердрафт/кредит или инвестировать. Сравнивается ставка долга и доходность "
               "после налога.")
    m, day, rate = market(), today(), tax_rate()
    with Session() as s:
        positions = load_positions(s, m, day)
        asset_map = {a.id: a for a in s.query(Asset).all()}
        fc = forecast(asset_map, positions, m, day)
    paying_ids = {f.asset_id for f in fc}
    paying_value = sum((p.value_usd or ZERO for p in positions if p.asset_id in paying_ids), ZERO)
    gross_year = sum((f.gross_year_usd for f in fc), ZERO)
    portfolio_yield = gross_year / paying_value * 100 if paying_value else ZERO

    a, b, c = st.columns(3)
    debt_raw = dec_input("Ставка долга, % годовых", "debt_rate", cfg("debt_rate_pct"), container=a)
    amount_raw = dec_input("Сумма, ₪", "debt_amount", "10000", container=b)
    default_yield = str(portfolio_yield.quantize(Decimal("0.01"))) if portfolio_yield else ""
    yield_raw = dec_input("Доходность инвестиций брутто, % годовых", "debt_yield", default_yield, container=c,
                          help="По умолчанию — дивидендная доходность ваших выплачивающих фондов за 12 месяцев.")
    growth_raw = dec_input("Ожидаемый рост цены, % годовых (не гарантирован)", "debt_growth", "0")
    try:
        debt = parse_dec(debt_raw, "Ставка долга")
        amount = parse_dec(amount_raw, "Сумма", positive=True)
        gross = parse_dec(yield_raw, "Доходность")
        growth = parse_dec(growth_raw, "Рост") or ZERO
    except ValueError as exc:
        st.warning(str(exc))
        return
    if str(debt) != cfg("debt_rate_pct"):
        cfg_set(debt_rate_pct=debt)
    v = debt_vs_invest(debt, gross, rate, amount, growth)
    k1, k2, k3 = st.columns(3)
    k1.metric("Долг стоит", pct(v.debt_rate), "гарантированно", delta_color="off", delta_arrow="off")
    k2.metric("Инвестиции приносят нетто", pct(v.net_yield), f"после налога {pct(rate)}", delta_color="off", delta_arrow="off")
    k3.metric(f"Разница в год на {ils(amount)}", ils(abs(v.annual_diff)))
    if v.better == "repay":
        st.error(f"Выгоднее гасить долг: он обходится в {pct(v.debt_rate)}, а инвестиции после налога дают "
                 f"{pct(v.net_yield)}. Каждые {ils(amount)}, направленные в долг, экономят {ils(-v.annual_diff)} в год.")
    elif v.better == "invest":
        st.success(f"Арифметически выгоднее инвестировать: нетто {pct(v.net_yield)} против {pct(v.debt_rate)} "
                   f"по долгу — разница {ils(v.annual_diff)} в год на {ils(amount)}.")
        st.caption("Но: проценты по долгу гарантированы, а доходность и цена — нет. Выплаты могут снизиться, "
                   "а цена пая упасть.")
    else:
        st.info("Ставка долга равна нетто-доходности — при равенстве надёжнее гасить долг.")
    if growth > 0:
        st.caption("В расчёт включён ожидаемый рост цены. Это предположение, а не факт: обнулите его, чтобы "
                   "увидеть сравнение только по фактическим выплатам.")
    st.plotly_chart(charts.hbar(
        ["Ставка долга", "Доходность брутто", "Доходность нетто"],
        [float(v.debt_rate * 100), float((gross + growth)), float(v.net_yield * 100)], None, "%",
        [charts.CRITICAL, charts.NEUTRAL, charts.PALETTE[0]]), width="stretch")
