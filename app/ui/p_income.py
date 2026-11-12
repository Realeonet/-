"""Экраны «Дивиденды» и «Израильские налоги»."""

from __future__ import annotations


import streamlit as st

from app.db import Asset, Custodian
from app.domain.money import ZERO
from app.domain.tax_il import net_of_tax, rank_lots_for_sale, simulate_sale
from app.services import ledger, taxes
from app.services.dividends import forecast, received, upcoming_ex_dates
from app.services.portfolio import load_positions
from app.ui import charts
from app.ui.common import (Session, assets, cfg_int, custodian_label, custodians, dec_input, ensure_fx, flash,
                           ils, market, owners, parse_dec, pct, qty, show_flash, table, tax_method, tax_note,
                           tax_rate, today, usd)


# ======================= дивиденды =======================

def render_dividends() -> None:
    st.title("Дивиденды")
    show_flash()
    m, day, rate = market(), today(), tax_rate()
    with Session() as s:
        positions = load_positions(s, m, day)
        asset_map = {a.id: a for a in s.query(Asset).all()}
        fc = forecast(asset_map, positions, m, day)
        got = received(s, rate)
    owner = st.selectbox("Владелец", ["Все"] + [o.name for o in owners()], key="div_owner")
    if owner != "Все":
        fc = [f for f in fc if f.owner == owner]
        got = [g for g in got if g.owner == owner]

    tab_fc, tab_cal, tab_got = st.tabs(["Прогноз дохода", "Календарь X-day", f"Фактически получено ({len(got)})"])

    with tab_fc:
        if not fc:
            st.info("Нет выплачивающих позиций с историей выплат. Добавьте лоты и обновите данные; "
                    "для фондов без истории задайте доходность вручную в карточке актива.")
        else:
            year_usd = sum((f.gross_year_usd for f in fc), ZERO)
            year_ils = sum((f.gross_year_ils for f in fc), ZERO)
            month_usd = sum((f.gross_month_usd for f in fc), ZERO)
            st.markdown(f"**Брутто → минус {pct(rate)} → нетто**")
            a, b, c, d = st.columns(4)
            a.metric("Год, нетто $", usd(net_of_tax(year_usd, rate)), f"брутто {usd(year_usd)}", delta_color="off", delta_arrow="off")
            b.metric("Год, нетто ₪", ils(net_of_tax(year_ils, rate)), f"брутто {ils(year_ils)}", delta_color="off", delta_arrow="off")
            c.metric("Ближайшие 30 дней, нетто $", usd(net_of_tax(month_usd, rate)),
                     f"брутто {usd(month_usd)}", delta_color="off", delta_arrow="off")
            d.metric("В среднем в месяц, нетто ₪", ils(net_of_tax(year_ils, rate) / 12))
            table([{
                "Фонд": f.symbol, "Место": f.custodian, "Владелец": f.owner, "Кол-во": qty(f.qty),
                "Выплата на пай в год": f"{qty(f.per_unit_year)} {f.currency}",
                "Год брутто, $": usd(f.gross_year_usd), "Налог, $": usd(f.gross_year_usd * rate),
                "Год нетто, $": usd(net_of_tax(f.gross_year_usd, rate)),
                "Год нетто, ₪": ils(net_of_tax(f.gross_year_ils, rate)),
                "Ближайший X-day": (f"{f.next_ex.day:%d.%m.%Y}" + (" (прогноз)" if f.next_ex.estimated else ""))
                if f.next_ex else "—",
                "Основание": f.basis,
            } for f in fc])
            by_cust: dict[str, object] = {}
            for f in fc:
                by_cust[f.custodian] = by_cust.get(f.custodian, ZERO) + net_of_tax(f.gross_year_usd, rate)
            names = sorted(by_cust, key=lambda k: -by_cust[k])
            st.plotly_chart(charts.hbar(names, [float(by_cust[n]) for n in names],
                                        "Нетто-доход в год по местам хранения, $", " $"),
                            width="stretch")
            st.caption("Прогноз = выплаты за последние 12 месяцев × текущее количество. Будущие выплаты могут "
                       "отличаться. Налог 25% — итоговая ставка: удержанное в США засчитывается.")
            tax_note()

    with tab_cal:
        horizon = st.slider("Горизонт, дней", 30, 365, 90, step=30)
        held_qty: dict[int, object] = {}
        for p in positions:
            if owner in ("Все", p.owner):
                held_qty[p.asset_id] = held_qty.get(p.asset_id, ZERO) + p.qty
        rows = []
        xdays = cfg_int("xday_days")
        for asset_id, quantity in held_qty.items():
            asset = asset_map[asset_id]
            if asset.dist_type == "acc" or asset.asset_class not in ("stock", "etf"):
                continue
            info = m.price_info(asset)
            for e in upcoming_ex_dates(asset, m, day, horizon):
                gross = quantity * e.amount_per_unit if e.amount_per_unit else None
                fx = m.fx(info.currency, day) if info else None
                usd_ils = m.usd_ils(day)
                gross_usd = gross * fx / usd_ils if gross is not None and fx and usd_ils else None
                left = (e.day - day).days
                rows.append((e.day, {
                    "X-day": e.day.strftime("%d.%m.%Y"), "Через, дн.": left, "Фонд": asset.symbol,
                    "Источник даты": "прогноз по истории" if e.estimated else "введено вручную",
                    "Выплата": e.pay_date.strftime("%d.%m.%Y") if e.pay_date else "—",
                    "На пай (последняя)": qty(e.amount_per_unit) if e.amount_per_unit else "—",
                    "Ожидается брутто, $": usd(gross_usd),
                    "Нетто, $": usd(net_of_tax(gross_usd, rate)) if gross_usd is not None else "—",
                    "Покупать?": f"⛔ не покупать ({xdays} дн. до X-day)" if 0 <= left <= xdays else "",
                }))
        table([r for _, r in sorted(rows, key=lambda x: x[0])])
        st.caption("Даты «прогноз по истории» рассчитаны по интервалу прошлых выплат и могут отличаться на "
                   "несколько дней. Точную дату X-day и дату выплаты можно вписать в карточке актива.")

    with tab_got:
        with st.expander("➕ Записать полученную выплату"):
            asset_list = [a for a in assets() if a.asset_class in ("stock", "etf")]
            cust_list = custodians()
            if not asset_list or not cust_list:
                st.info("Нужен хотя бы один фонд и одно место хранения.")
            else:
                with st.form("div_add"):
                    a, b, c = st.columns(3)
                    asset = a.selectbox("Фонд", asset_list, format_func=lambda x: x.symbol)
                    cust = b.selectbox("Место хранения", cust_list, format_func=custodian_label)
                    pay = c.date_input("Дата выплаты", value=day, max_value=day, format="DD.MM.YYYY")
                    a, b, c = st.columns(3)
                    gross_raw = dec_input("Сумма брутто", "div_gross", container=a)
                    foreign_raw = dec_input("Удержано за рубежом", "div_foreign", "0", container=b,
                                            help="Например 25% в США. В валюте выплаты.")
                    il_raw = dec_input("Удержано в Израиле", "div_il", "0", container=c)
                    if st.form_submit_button("Записать"):
                        try:
                            gross = parse_dec(gross_raw, "Сумма брутто", positive=True)
                            foreign = parse_dec(foreign_raw, "Удержано за рубежом") or ZERO
                            il_tax = parse_dec(il_raw, "Удержано в Израиле") or ZERO
                            ensure_fx(pay, {asset.currency, "USD"})
                            with Session() as s:
                                ledger.add_dividend(s, market(), s.get(Asset, asset.id), s.get(Custodian, cust.id),
                                                    pay, gross, foreign, il_tax)
                                s.commit()
                            flash("Выплата записана")
                            st.rerun()
                        except (ValueError, ledger.LedgerError) as exc:
                            st.error(str(exc))
        years = sorted({g.row.pay_date.year for g in got}, reverse=True)
        if not years:
            st.info("Выплат пока нет. Их можно вводить вручную или импортировать из отчёта брокера.")
        else:
            year = st.selectbox("Год", years, key="div_year")
            rows = [g for g in got if g.row.pay_date.year == year]
            gross = sum((g.gross_ils for g in rows), ZERO)
            to_pay = sum((g.to_pay_ils for g in rows), ZERO)
            net = sum((g.net_ils for g in rows), ZERO)
            a, b, c = st.columns(3)
            a.metric("Получено брутто, ₪", ils(gross))
            b.metric("Нетто после налога, ₪", ils(net))
            c.metric("Доплатить в Израиле, ₪", ils(to_pay))
            table([{
                "Дата": g.row.pay_date.strftime("%d.%m.%Y"), "Фонд": g.symbol, "Место": g.custodian,
                "Владелец": g.owner, "Брутто": f"{qty(g.row.gross)} {g.row.currency}",
                "Удержано за рубежом": qty(g.row.foreign_tax), "Удержано в Израиле": qty(g.row.il_tax_withheld),
                "Доплатить, ₪": ils(g.to_pay_ils), "Нетто, ₪": ils(g.net_ils), "Статус": g.status,
                "Курс": str(g.row.fx_ccy_ils),
            } for g in rows])
            tax_note()


# ======================= налоги =======================

METHOD_LABELS = {
    "nominal": "номинальный: выручка в ₪ по курсу дня продажи − себестоимость в ₪ по курсу дня покупки",
    "linked": "с привязкой к валюте: прибыль в валюте × курс дня продажи",
}


def render_taxes() -> None:
    st.title("Израильские налоги")
    show_flash()
    st.warning("Ориентировочный расчёт. Перед подачей декларации сверьте с налоговым консультантом (יועץ מס).")
    m, day, rate, method = market(), today(), tax_rate(), tax_method()
    st.caption(f"Ставка {pct(rate)} · метод — {METHOD_LABELS[method]}. Меняется в «Настройках».")

    tab_year, tab_sim, tab_rules = st.tabs(["Итоги года", "Симулятор продажи", "Правила и допущения"])

    with tab_year:
        with Session() as s:
            years = taxes.years_with_activity(s) or [day.year]
            year = st.selectbox("Налоговый год", years, key="tax_year")
            rows = taxes.realized(s, rate, method, year)
            divs = received(s, rate, year)
        summary = taxes.summary_by_owner(rows, rate)
        if not rows and not divs:
            st.info("В этом году продаж, обменов и выплат нет.")
        for owner_name, sm in summary.items():
            st.subheader(f"{owner_name}: продажи и обмены")
            a, b, c, d = st.columns(4)
            a.metric("Прибыль, ₪", ils(sm.gains_ils))
            b.metric("Убытки, ₪", ils(sm.losses_ils))
            c.metric(f"Налог {pct(rate)} с результата года", ils(sm.tax_ils))
            d.metric("Осталось задекларировать", ils(sm.to_declare_ils),
                     f"удержано брокерами {ils(sm.withheld_ils)}", delta_color="off", delta_arrow="off")
            if sm.loss_carry_ils > 0:
                st.info(f"Убыток года {ils(sm.loss_carry_ils)} — можно заявить для переноса на будущие годы "
                        "(условия зачёта — проверить).")
        if rows:
            table([{
                "Дата": r.disposal.date.strftime("%d.%m.%Y"), "Владелец": r.owner, "Актив": r.symbol,
                "Место": r.custodian, "Тип": "обмен" if r.disposal.kind == "swap" else "продажа",
                "Кол-во": qty(r.gain.qty), "Куплено": r.buy_date.strftime("%d.%m.%Y"),
                "Прибыль, $": usd(r.gain.gain_usd), "Прибыль, ₪ (номин.)": ils(r.gain.gain_ils_nominal),
                "Прибыль, ₪ (привязка)": ils(r.gain.gain_ils_linked), "Налог, ₪": ils(r.gain.tax_ils),
                "Удержано, ₪": ils(r.withheld_ils), "Статус": r.status, "Лот №": r.gain.lot_id,
            } for r in rows])
            st.caption("Налог в строке — если бы эта продажа была единственной. Итог года учитывает зачёт убытков.")
        div_due = [g for g in divs if g.to_pay_ils > 0]
        if divs:
            st.subheader("Дивиденды")
            total_due = sum((g.to_pay_ils for g in div_due), ZERO)
            st.metric("Налог с дивидендов к доплате / декларированию, ₪", ils(total_due))
            if div_due:
                by_cust: dict[str, object] = {}
                for g in div_due:
                    key = f"{g.custodian} ({g.owner})"
                    by_cust[key] = by_cust.get(key, ZERO) + g.to_pay_ils
                table([{"Место хранения": k, "К доплате, ₪": ils(v)} for k, v in by_cust.items()])
            else:
                st.success("По всем выплатам года налог удержан полностью.")
        with Session() as s:
            custs = s.query(Custodian).filter(Custodian.archived.is_(False)).order_by(Custodian.name).all()
        if custs:
            st.subheader("Кто удерживает налог")
            table([{
                "Место хранения": c.name,
                "Налог": "✅ удерживает сам (израильский брокер/банк)" if c.withholds_il_tax
                else "📝 не удерживает — декларировать самому",
            } for c in custs])

    with tab_sim:
        st.markdown("**Если продам X — сколько налога и что останется на руки**")
        held = []
        for a in assets():
            with Session() as s:
                if ledger.open_lots(s, a.id):
                    held.append(a)
        if not held:
            st.info("Нет открытых лотов.")
        else:
            c1, c2 = st.columns(2)
            asset = c1.selectbox("Актив", held, format_func=lambda x: f"{x.symbol} — {x.name}", key="sim_asset")
            with Session() as s:
                lots = ledger.open_lots(s, asset.id)
                cust_names = {c.id: c.name for c in s.query(Custodian).all()}
            cust_ids = sorted({lot.custodian_id for lot in lots}, key=lambda i: cust_names[i])
            cust_id = c2.selectbox("Место хранения", cust_ids, format_func=lambda i: cust_names[i],
                                   key=f"sim_cust_{asset.id}")
            lots = [lot for lot in lots if lot.custodian_id == cust_id]
            info = m.price_info(asset)
            currency = info.currency if info else asset.currency
            available = sum((lot.qty_remaining for lot in lots), ZERO)
            c1, c2, c3 = st.columns(3)
            qty_raw = dec_input(f"Продать (доступно {qty(available)})", f"sim_qty_{asset.id}", str(available),
                                container=c1)
            price_raw = dec_input(f"По цене, {currency}", f"sim_price_{asset.id}", str(info.price) if info else "",
                                  container=c2)
            fee_raw = dec_input("Комиссия", "sim_fee", "0", container=c3)
            fx_ccy, fx_usd = m.fx(currency, day), m.usd_ils(day)
            try:
                quantity = parse_dec(qty_raw, "Количество", positive=True)
                price = parse_dec(price_raw, "Цена")
                fee = parse_dec(fee_raw, "Комиссия") or ZERO
                if not fx_ccy or not fx_usd:
                    raise ValueError("Нет курса валюты — обновите данные")
                states = [ledger.lot_state(lot) for lot in lots]
                fifo = simulate_sale(ledger.fifo_allocate(states, quantity), price, fee, currency, fx_ccy, fx_usd,
                                     rate, method)
                ranked = rank_lots_for_sale(states, price, currency, fx_ccy, fx_usd, rate, method)
                left, smart_alloc = quantity, []
                for state, _gain in ranked:
                    take = min(state.remaining, left)
                    if take > 0:
                        smart_alloc.append((state, take))
                        left -= take
                smart = simulate_sale(smart_alloc, price, fee, currency, fx_ccy, fx_usd, rate, method)
            except ValueError as exc:
                st.warning(str(exc))
            else:
                a, b = st.columns(2)
                for col, title, sim in ((a, "FIFO (старые лоты первыми)", fifo),
                                        (b, "Сначала лоты с наименьшей прибылью", smart)):
                    with col.container(border=True):
                        st.markdown(f"**{title}**")
                        st.metric("Налог, ₪", ils(sim.tax_ils))
                        st.metric("На руки, ₪", ils(sim.net_ils), usd(sim.net_usd), delta_color="off", delta_arrow="off")
                        st.caption(f"Выручка {usd(sim.proceeds_usd)} · налогооблагаемая прибыль {ils(sim.taxable_ils)}")
                saving = fifo.tax_ils - smart.tax_ils
                if saving > 0:
                    st.success(f"Выбор лотов вручную экономит {ils(saving)} налога сейчас "
                               "(налог не исчезает, а откладывается на оставшиеся лоты).")
                st.markdown("**Какие лоты выгоднее продавать первыми**")
                table([{
                    "Очередь": i + 1, "Лот №": state.id, "Куплен": state.buy_date.strftime("%d.%m.%Y"),
                    "Остаток": qty(state.remaining), "Прибыль на весь лот, $": usd(g.gain_usd),
                    "Налогооблагаемая, ₪": ils(g.taxable_ils), "Налог, ₪": ils(g.tax_ils),
                    "Налог на единицу, ₪": ils(g.tax_ils / g.qty),
                } for i, (state, g) in enumerate(ranked)])
                st.caption("Израильский брокер обычно сам применяет FIFO. Выбор лота вручную возможен не везде — "
                           "проверьте у своего брокера и консультанта.")
                tax_note()

    with tab_rules:
        st.markdown(f"""
**Что заложено в расчёт**

- **Продажа ценных бумаг** — {pct(rate)} с прибыли, по каждому лоту. Показываются оба варианта: прибыль в $ и в ₪.
- **Прибыль в шекелях** — выбранный метод: *{METHOD_LABELS[method]}*.
  Альтернативный метод показан рядом в таблицах. Какой применим к вам — **проверить** с консультантом.
- **Курс** — официальный курс Банка Израиля (שער יציג) на дату; в выходные берётся последний опубликованный.
- **Дивиденды иностранных фондов** — итог {pct(rate)} от брутто. Налог, удержанный в США, засчитывается;
  по фондам без удержания (ирландские UCITS) налог нужно задекларировать самому.
- **Израильский брокер / банк** обычно удерживает налог сам; иностранный (IBKR, Binance) — нет.
- **Убытки** уменьшают прибыль того же года. Перенос и зачёт против дивидендов — **проверить**.

**Криптовалюта**

- Продажа и **обмен одной монеты на другую** считаются реализацией: налог с прибыли по каждому лоту.
- Перевод между своими кошельками и биржами — не реализация.
- Комиссия сети, уплаченная самой монетой, добавляется к себестоимости — **проверить**.

**Физические металлы**

- Налог с прибыли при продаже; НДС, уплаченный при покупке, включён в себестоимость.
- Ставки, освобождения и правила по НДС для конкретных монет и слитков — **проверить**, в приложении они не зашиты.

**Чего в расчёте нет**

- מס יסף включается только если вы сами задали его процент в «Настройках».
- Пенсионные продукты (קרן השתלמות, קופת גמל, פנסיה) имеют свои правила и здесь не считаются.
- Налог США на наследство — отдельный раздел.
""")
