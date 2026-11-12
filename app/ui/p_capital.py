"""Экран «Общий капитал»."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pandas as pd
import streamlit as st

from app.domain.money import ZERO
from app.services.portfolio import group_positions, load_positions, totals, value_history
from app.ui import charts
from app.ui.common import Session, ils, market, owners, pct, qty, show_flash, table, today, usd
from app.ui.pension_data import totals_by_owner

PENSION_LABEL = "Пенсионные накопления"
VIEWS = {
    "По классам активов": lambda p: p.class_label,
    "По местам хранения": lambda p: p.custodian,
    "По владельцам": lambda p: p.owner,
    "По активам": lambda p: p.symbol,
}
PERIODS = {"1 мес": 30, "3 мес": 91, "1 год": 365, "3 года": 1095, "5 лет": 1826}


def render() -> None:
    st.title("Общий капитал")
    show_flash()
    m, day = market(), today()
    with Session() as s:
        positions = load_positions(s, m, day)

    c1, c2, c3 = st.columns([2, 2, 2])
    owner_names = ["Все"] + [o.name for o in owners()]
    owner = c1.selectbox("Владелец", owner_names)
    view_name = c2.selectbox("Разрез", list(VIEWS))
    with_pension = c3.toggle("Включая пенсионные накопления", value=True)
    if owner != "Все":
        positions = [p for p in positions if p.owner == owner]

    usd_ils = m.usd_ils(day)
    pension = totals_by_owner(m, day) if with_pension else {}
    pension_ils = sum((v for k, v in pension.items() if owner in ("Все", k)), ZERO)
    total = totals(positions)

    if not positions and pension_ils == 0:
        st.info("Пока пусто. Начните с раздела «Места хранения», затем добавьте активы и лоты "
                "в разделе «Активы и операции».")
        return
    if usd_ils is None:
        st.error("Нет курса USD/ILS. Нажмите «Обновить данные» в боковой панели или введите курс в «Настройках».")
        return

    grand_ils = total.value_ils + pension_ils
    grand_usd = total.value_usd + pension_ils / usd_ils
    a, b, c, d = st.columns(4)
    a.metric("Капитал, ₪", ils(grand_ils))
    b.metric("Капитал, $", usd(grand_usd))
    c.metric("Прибыль по лотам, $", usd(total.gain_usd), pct(total.gain_pct_usd, signed=True))
    d.metric("Прибыль по лотам, ₪", ils(total.gain_ils), pct(total.gain_pct_ils, signed=True))
    fx_day = m.fx_date("USD", day)
    st.caption(
        f"Курс Банка Израиля: {usd_ils} ₪ за $1 (на {fx_day:%d.%m.%Y}). Прибыль в ₪ считается по курсам "
        "дат покупки, поэтому отличается от прибыли в $." + (f" Из них пенсионные: {ils(pension_ils)}."
                                                              if pension_ils else "")
    )
    if total.unpriced:
        st.warning(f"У {total.unpriced} лот(ов) нет цены — они не входят в сумму. Обновите данные или задайте "
                   "цену вручную в карточке актива.")
    stale = sorted({p.symbol for p in positions if p.price is not None and p.price.stale})
    if stale:
        st.warning("Цены устарели (старше 5 дней): " + ", ".join(stale))

    # ----- разрез -----
    groups = group_positions(positions, VIEWS[view_name])
    rows, labels, shares, colors = [], [], [], []
    extra = []
    if pension_ils and view_name == "По классам активов":
        extra.append((PENSION_LABEL, pension_ils))
    elif pension_ils and view_name == "По владельцам":
        extra = []  # добавляется к владельцам ниже
    values = {g.name: g.value_ils for g in groups}
    if view_name == "По владельцам":
        for name, value in pension.items():
            if owner in ("Все", name):
                values[name] = values.get(name, ZERO) + value
    for name, value in extra:
        values[name] = value
    by_name = {g.name: g for g in groups}
    for name, value in sorted(values.items(), key=lambda x: -x[1]):
        g = by_name.get(name)
        share = value / grand_ils if grand_ils else ZERO
        labels.append(name)
        shares.append(float(share * 100))
        colors.append(charts.CLASS_COLORS.get(name, charts.PALETTE[0]))
        rows.append({
            view_name.replace("По ", "").capitalize(): name,
            "Стоимость, ₪": ils(value),
            "Стоимость, $": usd(value / usd_ils),
            "Доля": pct(share),
            "Прибыль, $": usd(g.gain_usd) if g else "—",
            "Прибыль, %": pct(g.gain_pct_usd, signed=True) if g else "—",
            "Лотов": g.lots if g else "—",
        })
    left, right = st.columns([3, 2])
    with left:
        table(rows)
        if pension_ils and view_name in ("По местам хранения", "По активам"):
            st.caption(f"Пенсионные накопления ({ils(pension_ils)}) в этом разрезе не показаны — "
                       "см. раздел «Пенсия».")
    with right:
        st.plotly_chart(charts.hbar(labels[:12], shares[:12], "Доли, %", "%", colors[:12]),
                        width="stretch")

    # ----- история -----
    st.subheader("Стоимость во времени")
    p1, p2 = st.columns([3, 1])
    period = p1.radio("Период", list(PERIODS), index=2, horizontal=True, label_visibility="collapsed")
    currency = p2.radio("Валюта", ["₪", "$"], horizontal=True, label_visibility="collapsed")
    with Session() as s:
        frame = value_history(s, m, day - timedelta(days=PERIODS[period]), day)
    if frame.empty or frame["total_ils"].abs().sum() == 0:
        st.caption("Истории пока нет: добавьте лоты и обновите данные.")
    else:
        if currency == "₪":
            classes = frame.drop(columns=["total_ils", "total_usd"])
            fig = charts.stacked_area(classes, None, " ₪", colors=charts.CLASS_COLORS)
        else:
            fig = charts.lines({"Портфель, $": frame["total_usd"]}, None, " $")
        st.plotly_chart(fig, width="stretch")
        st.caption("Восстановлено по лотам и истории цен (все владельцы, без пенсионных накоплений). "
                   "Пополнения и продажи видны как ступеньки. Для металлов история — по фьючерсам.")

    # ----- лоты -----
    with st.expander(f"Все лоты ({len(positions)})"):
        table([{
            "Актив": p.symbol,
            "Место": p.custodian,
            "Владелец": p.owner,
            "Куплено": p.buy_date.strftime("%d.%m.%Y"),
            "Кол-во": qty(p.qty),
            "Себестоимость/ед.": f"{qty(p.unit_cost.quantize(Decimal("0.0001")))} {p.lot_currency}",
            "Цена сейчас": f"{qty(p.price.price)} {p.price.currency}" if p.price else "нет цены",
            "Стоимость, $": usd(p.value_usd),
            "Прибыль, $": usd(p.gain_usd),
            "Прибыль, ₪": ils(p.gain_ils),
            "Лот №": p.lot_id,
        } for p in sorted(positions, key=lambda x: (x.symbol, x.buy_date))], height=360)
        if positions:
            frame = pd.DataFrame([{
                "symbol": p.symbol, "custodian": p.custodian, "owner": p.owner, "buy_date": p.buy_date,
                "qty": str(p.qty), "unit_cost": str(p.unit_cost), "currency": p.lot_currency,
                "value_usd": str(p.value_usd), "value_ils": str(p.value_ils), "cost_usd": str(p.cost_usd),
                "cost_ils": str(p.cost_ils),
            } for p in positions])
            st.download_button("Скачать лоты (CSV)", frame.to_csv(index=False).encode("utf-8-sig"),
                               "lots.csv", "text/csv")
