"""Экран «Места хранения»: карточки, риски, журнал переводов."""

from __future__ import annotations

import streamlit as st
from sqlalchemy.orm import joinedload

from app.db import CUSTODIAN_KINDS, Custodian, Lot, Transfer
from app.domain.money import ZERO
from app.domain.risk import concentration
from app.services import ledger
from app.services.portfolio import group_positions, load_positions, totals
from app.ui import charts
from app.ui.common import (Session, cfg_dec, dec_input, flash, ils, market, owners, parse_dec, pct, qty,
                           show_flash, table, today, usd)

CURRENCIES = ["USD", "ILS", "EUR", "GBP", "CHF"]


def _form(prefix: str, c: Custodian | None) -> None:
    owner_list = owners()
    kinds = list(CUSTODIAN_KINDS)
    with st.form(f"{prefix}_form"):
        a, b, d = st.columns(3)
        name = a.text_input("Название", value=c.name if c else "", key=f"{prefix}_name")
        kind = b.selectbox("Тип", kinds, index=kinds.index(c.kind) if c else 0,
                           format_func=lambda k: CUSTODIAN_KINDS[k], key=f"{prefix}_kind")
        owner_idx = [o.id for o in owner_list].index(c.owner_id) if c else 0
        owner = d.selectbox("Владелец", owner_list, index=owner_idx, format_func=lambda o: o.name,
                            key=f"{prefix}_owner")
        a, b, d = st.columns(3)
        country = a.text_input("Страна", value=c.country if c else "", key=f"{prefix}_country")
        cur = c.currency if c else "USD"
        currency = b.selectbox("Валюта счёта", CURRENCIES, index=CURRENCIES.index(cur) if cur in CURRENCIES else 0,
                               key=f"{prefix}_ccy")
        withholds = d.checkbox(
            "Удерживает израильский налог сам", value=c.withholds_il_tax if c else False, key=f"{prefix}_wh",
            help="Израильский брокер или банк обычно удерживает налог у источника. "
                 "Иностранный (IBKR, Binance и т.п.) — нет, налог нужно декларировать самому.",
        )
        st.markdown("**Комиссии**")
        f1, f2, f3, f4 = st.columns(4)
        fee_ccy_cur = c.fee_currency if c else "USD"
        fee_ccy = f1.selectbox("Валюта комиссий", CURRENCIES,
                               index=CURRENCIES.index(fee_ccy_cur) if fee_ccy_cur in CURRENCIES else 0,
                               key=f"{prefix}_feeccy")
        fee_trade = dec_input("За сделку (фикс.)", f"{prefix}_ft", str(c.fee_trade) if c else "0", container=f2)
        fee_pct = dec_input("За сделку, % от суммы", f"{prefix}_fp", str(c.fee_trade_pct) if c else "0",
                            container=f3)
        fee_min = dec_input("Минимум за сделку", f"{prefix}_fm", str(c.fee_trade_min) if c else "0", container=f4)
        f1, f2, f3 = st.columns(3)
        free = f1.number_input("Бесплатных сделок в месяц", min_value=0, step=1,
                               value=c.free_trades_per_month if c else 0, key=f"{prefix}_free")
        fee_transfer = dec_input("За перевод / вывод", f"{prefix}_ftr", str(c.fee_transfer) if c else "0",
                                 container=f2)
        fee_custody = dec_input("За хранение, % в год", f"{prefix}_fc", str(c.fee_custody_pct) if c else "0",
                                container=f3)
        st.markdown("**Кошелёк / физическое хранение** (если применимо)")
        w1, w2, w3 = st.columns(3)
        wallet = w1.text_input("Тип кошелька", value=(c.wallet_type or "") if c else "", key=f"{prefix}_wal",
                               help="Например: аппаратный, мультисиг, бумажный.")
        network = w2.text_input("Сеть", value=(c.network or "") if c else "", key=f"{prefix}_net")
        address = w3.text_input(
            "Публичный адрес (необязательно)", value=(c.watch_address or "") if c else "", key=f"{prefix}_adr",
            help="Только публичный адрес для справки. Он хранится локально и никуда не отправляется.",
        )
        location = st.text_input("Где лежит физически (для семьи)", value=(c.location_note or "") if c else "",
                                 key=f"{prefix}_loc", help="Например: «сейф дома», «банковская ячейка». Без кодов и паролей.")
        note = st.text_area("Заметка", value=(c.note or "") if c else "", key=f"{prefix}_note", height=70)
        st.caption("🔒 Никогда не вводите сюда seed-фразы, приватные ключи, пароли и PIN-коды.")
        archived = st.checkbox("В архиве (скрыть из списков)", value=c.archived if c else False,
                               key=f"{prefix}_arch") if c else False
        submitted = st.form_submit_button("Сохранить")
    if not submitted:
        return
    try:
        if not name.strip():
            raise ValueError("Укажите название")
        values = dict(
            fee_trade=parse_dec(fee_trade, "За сделку"), fee_trade_pct=parse_dec(fee_pct, "% от суммы"),
            fee_trade_min=parse_dec(fee_min, "Минимум"), fee_transfer=parse_dec(fee_transfer, "За перевод"),
            fee_custody_pct=parse_dec(fee_custody, "За хранение"),
        )
    except ValueError as exc:
        st.error(str(exc))
        return
    with Session() as s:
        target = s.get(Custodian, c.id) if c else Custodian()
        target.name, target.kind, target.owner_id = name.strip(), kind, owner.id
        target.country, target.currency, target.withholds_il_tax = country.strip(), currency, withholds
        target.fee_currency, target.free_trades_per_month = fee_ccy, int(free)
        for key, value in values.items():
            setattr(target, key, value)
        target.wallet_type, target.network = wallet.strip() or None, network.strip() or None
        target.watch_address, target.location_note = address.strip() or None, location.strip() or None
        target.note, target.archived = note.strip() or None, bool(archived)
        if c is None:
            s.add(target)
        s.commit()
    flash(f"Место хранения «{name.strip()}» сохранено")
    st.rerun()


def _delete(c: Custodian) -> None:
    with Session() as s:
        used = s.query(Lot).filter(Lot.custodian_id == c.id).count()
        used += s.query(Transfer).filter(
            (Transfer.from_custodian_id == c.id) | (Transfer.to_custodian_id == c.id)).count()
    if used:
        st.caption("Удалить нельзя: есть лоты или переводы. Можно отправить в архив.")
        return
    if st.button("Удалить место хранения", key=f"del_c{c.id}"):
        with Session() as s:
            s.delete(s.get(Custodian, c.id))
            s.commit()
        flash("Удалено")
        st.rerun()


def render() -> None:
    st.title("Места хранения")
    show_flash()
    m, day = market(), today()
    with Session() as s:
        custodians = (s.query(Custodian).options(joinedload(Custodian.owner))
                      .order_by(Custodian.archived, Custodian.name).all())
        positions = load_positions(s, m, day)
        transfers = (s.query(Transfer).options(joinedload(Transfer.asset), joinedload(Transfer.from_custodian),
                                               joinedload(Transfer.to_custodian))
                     .order_by(Transfer.date.desc(), Transfer.id.desc()).all())
    total = totals(positions)
    by_cust = {g.name: g for g in group_positions(positions, lambda p: p.custodian)}

    tab_cards, tab_risk, tab_transfers, tab_new = st.tabs(
        ["Карточки", "Риски", f"Журнал переводов ({len(transfers)})", "➕ Новое место"])

    with tab_new:
        _form("new_c", None)

    with tab_cards:
        if not custodians:
            st.info("Мест хранения пока нет. Создайте первое на вкладке «Новое место».")
        threshold = cfg_dec("concentration_pct")
        for c in custodians:
            g = by_cust.get(c.name)
            value_usd = g.value_usd if g else ZERO
            share = value_usd / total.value_usd if total.value_usd else ZERO
            badge = "🔑 self-custody" if c.is_self_custody else "🏛 кастодиальное"
            title = f"{'🗄 ' if c.archived else ''}{c.name} · {CUSTODIAN_KINDS.get(c.kind, c.kind)} · {c.owner.name}"
            with st.container(border=True):
                st.subheader(title)
                k1, k2, k3, k4 = st.columns(4)
                k1.metric("Стоимость, $", usd(value_usd))
                k2.metric("Стоимость, ₪", ils(g.value_ils if g else ZERO))
                k3.metric("Доля капитала", pct(share))
                k4.metric("Прибыль, $", usd(g.gain_usd) if g else "—")
                tax = "удерживает налог сам" if c.withholds_il_tax else "налог декларировать самому"
                st.caption(f"{badge} · {c.country or 'страна не указана'} · счёт в {c.currency} · {tax}")
                if share * 100 > threshold:
                    st.warning(f"Концентрация: {pct(share)} капитала в одном месте (порог {threshold}%).")
                mine = [p for p in positions if p.custodian_id == c.id]
                with st.expander(f"Что здесь лежит ({len({p.symbol for p in mine})} активов), комиссии, правка"):
                    table([{
                        "Актив": a.name, "Кол-во": qty(a.qty), "Стоимость, $": usd(a.value_usd),
                        "Стоимость, ₪": ils(a.value_ils), "Прибыль, $": usd(a.gain_usd), "Лотов": a.lots,
                    } for a in group_positions(mine, lambda p: p.symbol)])
                    fee_bits = [f"сделка: {c.fee_trade} {c.fee_currency}"]
                    if c.fee_trade_pct:
                        fee_bits.append(f"+ {c.fee_trade_pct}% (мин. {c.fee_trade_min})")
                    if c.free_trades_per_month:
                        fee_bits.append(f"бесплатно до {c.free_trades_per_month} сделок в месяц")
                    fee_bits.append(f"перевод/вывод: {c.fee_transfer} {c.fee_currency}")
                    fee_bits.append(f"хранение: {c.fee_custody_pct}% в год")
                    st.markdown("**Комиссии:** " + " · ".join(fee_bits))
                    if c.fee_custody_pct and g:
                        st.caption(f"Хранение обходится примерно в {usd(g.value_usd * c.fee_custody_pct / 100)} в год.")
                    extra = [x for x in (c.wallet_type, c.network, c.location_note, c.note) if x]
                    if extra:
                        st.markdown("**Детали:** " + " · ".join(extra))
                    st.markdown("**Редактирование**")
                    _form(f"c{c.id}", c)
                    _delete(c)

    with tab_risk:
        threshold = cfg_dec("concentration_pct")
        shares = concentration({name: g.value_usd for name, g in by_cust.items()}, threshold)
        if not shares:
            st.info("Нет оценённых позиций.")
        else:
            st.plotly_chart(charts.hbar(
                [s_.name for s_ in shares], [float(s_.share * 100) for s_ in shares],
                f"Доля капитала по местам хранения (порог концентрации {threshold}%)", "%",
                [charts.CRITICAL if s_.over_threshold else charts.PALETTE[0] for s_ in shares],
                threshold=float(threshold)), width="stretch")
            for s_ in shares:
                if s_.over_threshold:
                    st.warning(f"⚠️ «{s_.name}»: {pct(s_.share)} капитала — выше порога {threshold}%.")
            self_usd = sum((p.value_usd or ZERO for p in positions if p.self_custody), ZERO)
            cust_usd = total.value_usd - self_usd
            a, b = st.columns(2)
            a.metric("🏛 У кастодианов (брокеры, банки, биржи)", usd(cust_usd),
                     pct(cust_usd / total.value_usd) if total.value_usd else None, delta_color="off", delta_arrow="off")
            b.metric("🔑 Self-custody и физическое хранение", usd(self_usd),
                     pct(self_usd / total.value_usd) if total.value_usd else None, delta_color="off", delta_arrow="off")
            st.caption("Порог концентрации меняется в «Настройках». Пенсионные накопления здесь не учитываются.")

    with tab_transfers:
        st.caption("Перевод между местами не считается продажей: дата и цена покупки лотов сохраняются. "
                   "Новый перевод создаётся в разделе «Активы и операции → Перевести».")
        table([{
            "Дата": t.date.strftime("%d.%m.%Y"), "Актив": t.asset.symbol, "Откуда": t.from_custodian.name,
            "Куда": t.to_custodian.name, "Отправлено": qty(t.qty_sent), "Получено": qty(t.qty_received),
            "Комиссия": f"{t.fee_amount} {t.fee_currency}" if t.fee_amount else "—",
            "Пометка": "⚠️ по сути обмен — проверить налог" if t.flagged_exchange else "",
            "Заметка": t.note or "", "№": t.id,
        } for t in transfers])
        if transfers:
            with st.form("undo_transfer"):
                tid = st.selectbox("Отменить перевод №", [t.id for t in transfers])
                if st.form_submit_button("Отменить перевод"):
                    try:
                        with Session() as s:
                            ledger.undo_transfer(s, tid)
                            s.commit()
                        flash("Перевод отменён, лоты возвращены")
                        st.rerun()
                    except ledger.LedgerError as exc:
                        st.error(str(exc))
