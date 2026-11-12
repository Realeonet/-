"""Экран «Пенсионные накопления»: карточки счетов, сводка экспозиции, сверка с официальными данными."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pandas as pd
import streamlit as st

from app.adapters.base import SourceError
from app.adapters.gov_il import SOURCE_FOR_PRODUCT, SOURCE_LABELS, GovIlSource
from app.db import BalancePoint, OfficialMonthly, PensionAccount
from app.domain.money import ZERO
from app.domain.pension import CLASS_LABELS, CLASSES, PRODUCT_TYPES, validate_weights
from app.importers.pension_csv import parse_official_csv
from app.services.pension import ESTIMATE_LABEL, apply_official_records
from app.ui import charts
from app.ui.common import (Session, flash, ils, market, offline, owners, parse_dec, pct, refresh_pension_proxies,
                           runtime, show_flash, table, today)
from app.ui.pension_data import compute, load_accounts

FUNDS_HINT = "Migdal, Menora, Phoenix, Harel, Clal, Altshuler, More, Meitav…"


def _cfg():
    return runtime()[2]


def track_label(acc: PensionAccount) -> str:
    if acc.track_key == "other" and acc.track_custom:
        return acc.track_custom
    return _cfg().tracks[acc.track_key].label


# ---------- форма счёта ----------

def _apply_preset(prefix: str) -> None:
    preset = _cfg().tracks[st.session_state[f"{prefix}_track"]].preset
    if preset:
        for c in CLASSES:
            st.session_state[f"{prefix}_w_{c}"] = str(preset[c])


def _seed(prefix: str, acc: PensionAccount | None) -> None:
    if st.session_state.get(f"{prefix}__seeded"):
        return
    ss = st.session_state
    ss[f"{prefix}__seeded"] = True
    owner_ids = [o.id for o in owners()]
    ss[f"{prefix}_owner"] = acc.owner_id if acc else owner_ids[0]
    ss[f"{prefix}_product"] = acc.product_type if acc else "hishtalmut"
    ss[f"{prefix}_fund"] = acc.fund_name if acc else ""
    ss[f"{prefix}_track"] = acc.track_key if acc else "general"
    ss[f"{prefix}_custom"] = (acc.track_custom or "") if acc else ""
    ss[f"{prefix}_balance"] = str(acc.balance_ils) if acc else ""
    ss[f"{prefix}_bdate"] = acc.balance_date if acc else today()
    ss[f"{prefix}_hedge"] = str(acc.fx_hedge_pct) if acc else "0"
    ss[f"{prefix}_wdate"] = (acc.weights_as_of if acc else None) or today()
    ss[f"{prefix}_notes"] = (acc.notes or "") if acc else ""
    for c in CLASSES:
        ss[f"{prefix}_w_{c}"] = str(acc.weights()[c]) if acc else "0"


def _clear(prefix: str) -> None:
    for key in [k for k in st.session_state if k.startswith(prefix)]:
        del st.session_state[key]


def account_editor(prefix: str, acc: PensionAccount | None) -> None:
    _seed(prefix, acc)
    config = _cfg()
    owner_list = owners()
    st.selectbox("Трек (מסלול)", list(config.tracks), format_func=lambda k: config.tracks[k].label,
                 key=f"{prefix}_track", on_change=_apply_preset, args=(prefix,),
                 help="У трека S&P 500 веса подставляются автоматически и остаются редактируемыми. "
                      "У остальных — вводятся вручную или берутся из официальных данных.")
    with st.form(f"{prefix}_form"):
        c1, c2, c3 = st.columns(3)
        owner_id = c1.selectbox("Владелец", [o.id for o in owner_list], key=f"{prefix}_owner",
                                format_func=lambda i: next(o.name for o in owner_list if o.id == i))
        product = c2.selectbox("Тип продукта", list(PRODUCT_TYPES), format_func=lambda k: PRODUCT_TYPES[k],
                               key=f"{prefix}_product")
        fund = c3.text_input("Управляющая компания", key=f"{prefix}_fund", help=f"Например: {FUNDS_HINT}")
        custom = st.text_input("Название трека (только для «אחר — другое»)", key=f"{prefix}_custom")
        c4, c5, c6 = st.columns(3)
        balance_raw = c4.text_input("Баланс, ₪", key=f"{prefix}_balance", placeholder="0.00")
        bdate = c5.date_input("Дата баланса", key=f"{prefix}_bdate", max_value=today(), format="DD.MM.YYYY")
        hedge_raw = c6.text_input("Хедж валюты для зарубежной части, %", key=f"{prefix}_hedge",
                                  help="0 — валютный риск полностью; 100 — полностью захеджировано.")
        st.markdown("**Веса классов активов, %** — сумма ровно 100")
        cols = st.columns(5)
        weights_raw = {c: col.text_input(CLASS_LABELS[c], key=f"{prefix}_w_{c}") for col, c in zip(cols, CLASSES)}
        wdate = st.date_input("Веса актуальны на дату", key=f"{prefix}_wdate", format="DD.MM.YYYY")
        notes = st.text_area("Заметка", key=f"{prefix}_notes", height=70)
        submitted = st.form_submit_button("Сохранить счёт")
    if not submitted:
        return
    try:
        track = st.session_state[f"{prefix}_track"]
        if track == "other" and not custom.strip():
            raise ValueError("Для трека «אחר» укажите название трека")
        if not fund.strip():
            raise ValueError("Укажите управляющую компанию")
        balance = parse_dec(balance_raw, "Баланс")
        hedge = parse_dec(hedge_raw, "Хедж") or ZERO
        if hedge > 100:
            raise ValueError("Хедж — от 0 до 100%")
        weights = {c: parse_dec(weights_raw[c], CLASS_LABELS[c]) or ZERO for c in CLASSES}
        validate_weights(weights)
    except ValueError as exc:
        st.error(str(exc))
        return
    with Session() as s:
        target = s.get(PensionAccount, acc.id) if acc else PensionAccount()
        target.owner_id, target.product_type, target.fund_name = owner_id, product, fund.strip()
        target.track_key = track
        target.track_custom = (custom.strip() or None) if track == "other" else None
        target.balance_ils, target.balance_date = balance, bdate
        target.set_weights(weights)
        target.weights_as_of, target.fx_hedge_pct, target.notes = wdate, hedge, notes.strip() or None
        if acc is None:
            s.add(target)
            s.flush()
            s.add(BalancePoint(account_id=target.id, date=bdate, balance_ils=balance, source="manual"))
        s.commit()
    if not offline():
        refresh_pension_proxies(market())
    _clear(prefix)
    flash("Счёт сохранён")
    st.rerun()


# ---------- официальные данные ----------

def _official_block(acc: PensionAccount) -> None:
    st.markdown("**Официальные данные Минфина** (גמל-נט / פנסיה-נט / ביטוח-נט, открытые данные data.gov.il)")
    manual_track = _cfg().tracks[acc.track_key].preset is None
    gov = GovIlSource()
    sources = list(SOURCE_LABELS)
    default_source = acc.official_source or SOURCE_FOR_PRODUCT.get(acc.product_type, "gemel")
    if acc.official_fund_id:
        st.caption(f"Привязан трек № {acc.official_fund_id}: {acc.official_fund_name} "
                   f"({SOURCE_LABELS.get(acc.official_source, acc.official_source)})")
    update_weights = st.checkbox(
        "Обновлять веса и хедж счёта по официальным экспозициям", value=manual_track, key=f"ow_{acc.id}",
        help="Официальные данные дают долю акций, зарубежных активов и валютную экспозицию. Разбивка «акции "
             "Израиль / за рубежом» оценивается приближённо — см. README.")

    def load(source: str, fund_id: int, fund_name: str) -> None:
        start = min(acc.balance_date, date(today().year - 1, 1, 1))
        records = gov.monthly(source, fund_id, f"{start:%Y-%m}")
        with Session() as s:
            target = s.get(PensionAccount, acc.id)
            target.official_source, target.official_fund_id, target.official_fund_name = source, fund_id, fund_name
            count, updated = apply_official_records(target, records, "data.gov.il", update_weights)
            s.commit()
        if not offline():
            refresh_pension_proxies(market())
        flash(f"Загружено месяцев: {count}" + (". Веса и хедж обновлены по последнему месяцу" if updated else ""))
        st.rerun()

    if acc.official_fund_id and st.button("Обновить официальные данные", key=f"oup_{acc.id}", disabled=offline()):
        try:
            load(acc.official_source, acc.official_fund_id, acc.official_fund_name or "")
        except SourceError as exc:
            st.error(str(exc))
    with st.expander("Найти и привязать трек" if not acc.official_fund_id else "Привязать другой трек"):
        a, b = st.columns([1, 2])
        source = a.selectbox("Источник", sources, index=sources.index(default_source),
                             format_func=lambda k: SOURCE_LABELS[k], key=f"os_{acc.id}")
        query = b.text_input("Название трека на иврите или номер (מספר אוצר / מ״ה)", key=f"oq_{acc.id}",
                             help="Номер трека есть в годовом отчёте фонда. Поиск по названию: например «מיטב» или «S&P».")
        if st.button("Найти", key=f"of_{acc.id}", disabled=offline() or not query.strip()):
            try:
                st.session_state[f"ores_{acc.id}"] = gov.search_funds(source, query)
            except SourceError as exc:
                st.error(str(exc))
        results = st.session_state.get(f"ores_{acc.id}")
        if results is not None and not results:
            st.warning("Ничего не найдено. Попробуйте другое слово или номер трека.")
        if results:
            fund = st.selectbox("Результаты", results, key=f"op_{acc.id}",
                                format_func=lambda f: f"{f.fund_id} · {f.name} · {f.company}")
            if st.button("Привязать и загрузить", key=f"ol_{acc.id}"):
                try:
                    load(source, fund.fund_id, fund.name)
                except SourceError as exc:
                    st.error(str(exc))
    with st.expander("Импорт из CSV (если API недоступен)"):
        columns = _cfg().columns
        template = ",".join(["period", "return_pct"] + [columns[c] for c in CLASSES]) + "\n2026-01,1.23,40,30,20,5,5\n"
        st.download_button("Скачать шаблон CSV", template, "pension_template.csv", "text/csv", key=f"tpl_{acc.id}")
        upload = st.file_uploader("CSV-файл", type=["csv"], key=f"csv_{acc.id}")
        if upload is not None:
            result = parse_official_csv(upload.getvalue().decode("utf-8-sig"), columns)
            for err in result.errors:
                st.error(err)
            if result.rows and st.button("Импортировать", key=f"csvgo_{acc.id}", disabled=bool(result.errors)):
                with Session() as s:
                    target = s.get(PensionAccount, acc.id)
                    existing = {o.period: o for o in target.official}
                    for r in result.rows:
                        row = existing.get(r.period)
                        if row is None:
                            row = OfficialMonthly(period=r.period)
                            target.official.append(row)
                        row.return_pct, row.source = r.return_pct, upload.name
                        for c in CLASSES:
                            setattr(row, f"w_{c}", r.composition[c] if r.composition else None)
                    latest = [r for r in sorted(result.rows, key=lambda x: x.period) if r.composition]
                    if update_weights and latest:
                        last = latest[-1]
                        target.set_weights(last.composition)
                        y, mth = map(int, last.period.split("-"))
                        nxt = date(y + 1, 1, 1) if mth == 12 else date(y, mth + 1, 1)
                        target.weights_as_of = nxt - timedelta(days=1)
                    s.commit()
                flash(f"Импортировано месяцев: {len(result.rows)}")
                st.rerun()


# ---------- карточка ----------

def render_card(acc: PensionAccount) -> None:
    m, day = market(), today()
    view, error = compute(acc, m, day)
    with st.container(border=True):
        product = PRODUCT_TYPES[acc.product_type].split(" — ")[0]
        st.subheader(f"{product} · {acc.fund_name} · {acc.owner.name}")
        st.caption(track_label(acc))
        if error:
            st.error(error)
        cols = st.columns([1.7, 1.3, 1, 1, 1])
        cols[0].metric("Баланс сейчас (оценка)", ils(view.balance_today if view else acc.balance_ils))
        cols[1].metric("За день, ₪", ils(view.day_change_ils) if view else "—")
        cols[2].metric("За день, %", pct(view.day_change, signed=True) if view else "—")
        cols[3].metric("С начала месяца", pct(view.month_return, signed=True) if view else "—")
        cols[4].metric("С начала года", pct(view.ytd_return, signed=True) if view else "—")
        last = f" · оценка по {view.last_day:%d.%m.%Y}" if view and view.last_day else ""
        st.caption(f"⚠️ {ESTIMATE_LABEL}. Баланс на {acc.balance_date:%d.%m.%Y}: {ils(acc.balance_ils)} · "
                   f"хедж {acc.fx_hedge_pct.quantize(Decimal('0.01'))}%{last}")
        if view:
            for w in view.warnings:
                st.warning(w)
        with st.expander("График, сверка, официальные данные, правка"):
            if view and view.path:
                est = pd.Series([float(b) for _, b in view.path], index=pd.to_datetime([d for d, _ in view.path]))
                marks = {}
                if acc.balance_points:
                    marks["Официальный баланс"] = pd.Series(
                        [float(p.balance_ils) for p in acc.balance_points],
                        index=pd.to_datetime([p.date for p in acc.balance_points]))
                st.plotly_chart(charts.lines({"Оценка баланса": est}, ESTIMATE_LABEL, " ₪", markers=marks),
                                width="stretch")
            if view and view.comparisons:
                st.markdown("**Сверка: оценка против официальной доходности по месяцам**")
                table([{
                    "Месяц": c.period, "Официально": pct(c.official, signed=True),
                    "Оценка": pct(c.estimate, signed=True), "Разница, п.п.": f"{c.diff_pp:+.2f}",
                    "Веса для оценки": "официальные экспозиции месяца" if c.used_composition else "текущие веса",
                } for c in reversed(view.comparisons)])
                if view.tracking_error_pp is not None:
                    st.caption(f"Tracking error (стандартное отклонение разницы): {view.tracking_error_pp:.2f} п.п. "
                               "в месяц. Разница возникает из-за комиссий фонда, неторгуемых активов и "
                               "отличий реального состава от прокси-индексов.")
            st.markdown("**Веса сейчас:** " + " · ".join(
                f"{CLASS_LABELS[c]} {acc.weights()[c].quantize(Decimal('0.01'))}%" for c in CLASSES)
                + (f" (на {acc.weights_as_of:%d.%m.%Y})" if acc.weights_as_of else ""))
            _official_block(acc)
            st.markdown("**Обновить баланс** — когда пришёл דוח שנתי или отчёт из личного кабинета")
            with st.form(f"bal_{acc.id}"):
                a, b, c = st.columns(3)
                new_raw = a.text_input("Баланс, ₪", placeholder="0.00")
                new_date = b.date_input("Дата баланса", value=day, max_value=day, format="DD.MM.YYYY")
                source = c.selectbox("Источник", ["report", "manual"],
                                     format_func=lambda x: {"report": "отчёт / кабинет", "manual": "вручную"}[x])
                if st.form_submit_button("Сохранить баланс"):
                    try:
                        new_balance = parse_dec(new_raw, "Баланс")
                        with Session() as s:
                            target = s.get(PensionAccount, acc.id)
                            if new_date >= target.balance_date:
                                target.balance_ils, target.balance_date = new_balance, new_date
                            s.add(BalancePoint(account_id=acc.id, date=new_date, balance_ils=new_balance,
                                               source=source))
                            s.commit()
                        flash("Баланс обновлён: оценка теперь считается от новой даты")
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))
            st.markdown("**Редактирование счёта**")
            account_editor(f"pa{acc.id}", acc)
            confirm = st.checkbox("Удалить этот счёт вместе с историей", key=f"pdel_ck{acc.id}")
            if st.button("Удалить счёт", key=f"pdel{acc.id}", disabled=not confirm):
                with Session() as s:
                    s.delete(s.get(PensionAccount, acc.id))
                    s.commit()
                flash("Счёт удалён")
                st.rerun()


def _summary(accounts: list[PensionAccount]) -> None:
    m, day = market(), today()
    total = ZERO
    exposure = {c: ZERO for c in CLASSES}
    lines = {}
    for acc in accounts:
        view, _ = compute(acc, m, day)
        balance = view.balance_today if view else acc.balance_ils
        total += balance
        for c in CLASSES:
            exposure[c] += balance * acc.weights()[c] / 100
        if view and view.path:
            name = f"{acc.fund_name} · {acc.owner.name}"
            lines[name] = pd.Series([float(b) for _, b in view.path], index=pd.to_datetime([d for d, _ in view.path]))
    israel = exposure["israel_equity"] + exposure["israel_bonds"]
    abroad = exposure["foreign_equity"] + exposure["foreign_bonds"]
    bonds = exposure["israel_bonds"] + exposure["foreign_bonds"]
    a, b, c, d = st.columns(4)
    a.metric("Общий баланс (оценка)", ils(total))
    b.metric("В Израиле", ils(israel), pct(israel / total) if total else None, delta_color="off", delta_arrow="off")
    c.metric("За границей", ils(abroad), pct(abroad / total) if total else None, delta_color="off", delta_arrow="off")
    d.metric("В облигациях", ils(bonds), pct(bonds / total) if total else None, delta_color="off", delta_arrow="off")
    labels = [CLASS_LABELS[c] for c in CLASSES if exposure[c] > 0]
    values = [float(exposure[c]) for c in CLASSES if exposure[c] > 0]
    left, right = st.columns(2)
    if labels:
        left.plotly_chart(charts.hbar(labels, values, "Экспозиция по классам, ₪", " ₪"), width="stretch")
    if lines:
        right.plotly_chart(charts.lines(lines, "Оценка баланса по счетам", " ₪"), width="stretch")


def render() -> None:
    st.title("Пенсионные накопления")
    show_flash()
    st.caption(f"⚠️ {ESTIMATE_LABEL}. Оценка считается по индексам-прокси и весам классов активов; "
               "официальные цифры — только в отчётах фонда.")
    accounts = load_accounts()
    if accounts:
        _summary(accounts)
        for acc in accounts:
            render_card(acc)
    else:
        st.info("Счетов пока нет. Добавьте первый ниже: קרן פנסיה, קרן השתלמות, קופת גמל или ביטוח מנהלים.")
    with st.expander("➕ Добавить счёт", expanded=not accounts):
        account_editor("pnew", None)
