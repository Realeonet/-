"""Личный учёт инвестиций — интерфейс Streamlit (русский язык).

Запуск: см. README.md и start.bat. Данные лежат локально (DATA_DIR), наружу уходят только тикеры и даты.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

import plotly.graph_objects as go
import streamlit as st
from sqlalchemy.orm import selectinload

from app.adapters.prices import YFinancePriceSource
from app.backup import encrypt_session, restore_blob
from app.config import load_pension_config, load_settings
from app.db import (
    BalancePoint,
    OfficialMonthly,
    PensionAccount,
    get_engine,
    get_setting,
    init_db,
    make_session_factory,
    set_setting,
)
from app.domain.pension import CLASS_LABELS, CLASSES, PRODUCT_TYPES, validate_weights
from app.importers.pension_csv import parse_official_csv
from app.security import MIN_PASSWORD_LENGTH, hash_password, verify_password
from app.services.pension import (
    ESTIMATE_LABEL,
    AccountInputs,
    MissingProxyError,
    OfficialPoint,
    build_view,
    fetch_closes,
    format_money,
    format_pct,
)

FUND_SUGGESTIONS = "Migdal, Menora, Phoenix, Harel, Clal, Altshuler, More, Meitav"
OWNERS = ["я", "Люба"]
PAGES = [
    "Пенсионные счета",
    "Сводка",
    "Импорт официальных данных",
    "Бэкап и восстановление",
    "Настройки и тикеры",
]

st.set_page_config(page_title="Личный учёт инвестиций", page_icon="📊", layout="wide")


@st.cache_resource
def runtime():
    settings = load_settings()
    engine = get_engine(settings.db_path)
    init_db(engine)
    return settings, make_session_factory(engine), load_pension_config()


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_close(symbol: str, start: date, end: date):
    return YFinancePriceSource().history(symbol, start, end)


settings, Session, cfg = runtime()


def load_accounts() -> list[PensionAccount]:
    """Счета с уже загруженными связями: сессия закрывается сразу, поэтому ленивой загрузки быть не должно."""
    with Session() as s:
        return (
            s.query(PensionAccount)
            .options(selectinload(PensionAccount.official), selectinload(PensionAccount.balance_points))
            .order_by(PensionAccount.id)
            .all()
        )


# ---------- вход ----------

def login_gate() -> None:
    with Session() as s:
        stored = get_setting(s, "password_hash")
    if stored is None:
        st.title("Первый запуск: задайте пароль")
        st.caption(f"Пароль нужен для входа в приложение. Минимум {MIN_PASSWORD_LENGTH} символов.")
        with st.form("setup_pw"):
            p1 = st.text_input("Пароль", type="password")
            p2 = st.text_input("Повторите пароль", type="password")
            ok = st.form_submit_button("Сохранить")
        if ok:
            if p1 != p2:
                st.error("Пароли не совпадают")
            else:
                try:
                    digest = hash_password(p1)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    with Session() as s:
                        set_setting(s, "password_hash", digest)
                        s.commit()
                    st.session_state["auth"] = True
                    st.rerun()
        st.stop()
    if not st.session_state.get("auth"):
        st.title("Вход")
        with st.form("login"):
            pw = st.text_input("Пароль", type="password")
            if st.form_submit_button("Войти"):
                if verify_password(pw, stored):
                    st.session_state["auth"] = True
                    st.rerun()
                st.error("Неверный пароль")
        st.stop()


# ---------- расчёт ----------

def track_label(acc: PensionAccount) -> str:
    if acc.track_key == "other" and acc.track_custom:
        return acc.track_custom
    return cfg.tracks[acc.track_key].label


def compute(acc: PensionAccount, today: date):
    """Возвращает (view, error_text)."""
    weights = acc.weights()
    official = [
        OfficialPoint(o.period, o.return_pct, o.composition()) for o in acc.official
    ]
    starts = [acc.balance_date, date(today.year, 1, 1)]
    starts += [date.fromisoformat(f"{o.period}-01") for o in acc.official]
    start = min(starts) - timedelta(days=7)
    closes: dict = {}
    try:
        closes.update(fetch_closes(cfg, acc.track_key, weights, start, today, fetch_close))
        for comp in (p.composition for p in official if p.composition is not None):
            closes.update(fetch_closes(cfg, acc.track_key, comp, start, today, fetch_close))
        inputs = AccountInputs(
            balance=acc.balance_ils,
            balance_date=acc.balance_date,
            weights=weights,
            hedge_pct=acc.fx_hedge_pct,
            weights_as_of=acc.weights_as_of,
            manual_track=cfg.tracks[acc.track_key].preset is None,
        )
        view = build_view(inputs, closes, acc.fx_hedge_pct, today, official)
        return view, None
    except MissingProxyError as exc:
        return None, str(exc)
    except Exception as exc:  # сеть/yfinance: показываем, но не падаем
        return None, f"Не удалось получить котировки: {exc}"


def dec(value) -> Decimal:
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"Некорректное число: {value}") from exc


# ---------- форма счёта ----------

def _apply_preset(prefix: str) -> None:
    key = st.session_state[f"{prefix}_track"]
    preset = cfg.tracks[key].preset
    if preset:
        for c in CLASSES:
            st.session_state[f"{prefix}_w_{c}"] = float(preset[c])


def _seed(prefix: str, acc: PensionAccount | None) -> None:
    flag = f"{prefix}__seeded"
    if st.session_state.get(flag):
        return
    st.session_state[flag] = True
    if acc is None:
        st.session_state[f"{prefix}_owner"] = "я"
        st.session_state[f"{prefix}_product"] = "hishtalmut"
        st.session_state[f"{prefix}_fund"] = ""
        st.session_state[f"{prefix}_track"] = "general"
        st.session_state[f"{prefix}_custom"] = ""
        st.session_state[f"{prefix}_balance"] = 0.0
        st.session_state[f"{prefix}_bdate"] = date.today()
        st.session_state[f"{prefix}_hedge"] = 0.0
        st.session_state[f"{prefix}_wdate"] = date.today()
        st.session_state[f"{prefix}_notes"] = ""
        preset = cfg.tracks["general"].preset
        for c in CLASSES:
            st.session_state[f"{prefix}_w_{c}"] = float(preset[c]) if preset else 0.0
        return
    st.session_state[f"{prefix}_owner"] = acc.owner if acc.owner in OWNERS else "я"
    st.session_state[f"{prefix}_product"] = acc.product_type
    st.session_state[f"{prefix}_fund"] = acc.fund_name
    st.session_state[f"{prefix}_track"] = acc.track_key
    st.session_state[f"{prefix}_custom"] = acc.track_custom or ""
    st.session_state[f"{prefix}_balance"] = float(acc.balance_ils)
    st.session_state[f"{prefix}_bdate"] = acc.balance_date
    st.session_state[f"{prefix}_hedge"] = float(acc.fx_hedge_pct)
    st.session_state[f"{prefix}_wdate"] = acc.weights_as_of or date.today()
    st.session_state[f"{prefix}_notes"] = acc.notes or ""
    for c in CLASSES:
        st.session_state[f"{prefix}_w_{c}"] = float(acc.weights()[c])


def _clear(prefix: str) -> None:
    for k in list(st.session_state.keys()):
        if k.startswith(prefix):
            del st.session_state[k]


def account_editor(prefix: str, acc: PensionAccount | None) -> None:
    _seed(prefix, acc)
    track_keys = list(cfg.tracks)
    st.selectbox(
        "Трек",
        options=track_keys,
        format_func=lambda k: cfg.tracks[k].label,
        key=f"{prefix}_track",
        on_change=_apply_preset,
        args=(prefix,),
        help="Для трека с пресетом веса подставляются автоматически и остаются редактируемыми.",
    )
    with st.form(f"{prefix}_form"):
        c1, c2, c3 = st.columns(3)
        owner = c1.selectbox("Владелец", OWNERS, key=f"{prefix}_owner")
        product = c2.selectbox(
            "Тип продукта", list(PRODUCT_TYPES), format_func=lambda k: PRODUCT_TYPES[k], key=f"{prefix}_product"
        )
        fund = c3.text_input(
            "Управляющая компания", key=f"{prefix}_fund", help=f"Например: {FUND_SUGGESTIONS}"
        )
        custom = st.text_input(
            "Название трека (только для «אחר — другое»)", key=f"{prefix}_custom"
        )
        c4, c5, c6 = st.columns(3)
        balance = c4.number_input("Баланс, ₪", min_value=0.0, step=0.01, format="%.2f", key=f"{prefix}_balance")
        bdate = c5.date_input("Дата баланса", key=f"{prefix}_bdate")
        hedge = c6.number_input(
            "Хедж валюты для зарубежной части, %", min_value=0.0, max_value=100.0, step=1.0, key=f"{prefix}_hedge"
        )
        st.markdown("**Веса классов активов, %** (сумма должна быть ровно 100)")
        wcols = st.columns(5)
        weights_in = {}
        for col, c in zip(wcols, CLASSES):
            weights_in[c] = col.number_input(
                CLASS_LABELS[c], min_value=0.0, max_value=100.0, step=0.01, format="%.2f", key=f"{prefix}_w_{c}"
            )
        wdate = st.date_input("Веса актуальны на дату (для ручного трека)", key=f"{prefix}_wdate")
        notes = st.text_area("Заметка", key=f"{prefix}_notes")
        submitted = st.form_submit_button("Сохранить счёт")

    if not submitted:
        return
    try:
        track = st.session_state[f"{prefix}_track"]
        if track == "other" and not custom.strip():
            raise ValueError("Для трека «אחר» укажите название трека")
        if not fund.strip():
            raise ValueError("Укажите управляющую компанию")
        weights = {c: dec(weights_in[c]) for c in CLASSES}
        validate_weights(weights)
        hedge_val = dec(hedge)
        if not (Decimal(0) <= hedge_val <= Decimal(100)):
            raise ValueError("Хедж от 0 до 100%")
    except ValueError as exc:
        st.error(str(exc))
        return

    with Session() as s:
        target = s.get(PensionAccount, acc.id) if acc else PensionAccount()
        target.owner = owner
        target.product_type = product
        target.fund_name = fund.strip()
        target.track_key = track
        target.track_custom = (custom.strip() or None) if track == "other" else None
        target.balance_ils = dec(balance)
        target.balance_date = bdate
        target.set_weights(weights)
        target.weights_as_of = wdate
        target.fx_hedge_pct = hedge_val
        target.notes = notes or None
        if acc is None:
            s.add(target)
            s.flush()
            s.add(BalancePoint(account_id=target.id, date=bdate, balance_ils=dec(balance), source="manual"))
        s.commit()
    if acc is None:
        _clear(prefix)
    st.success("Сохранено")
    st.rerun()


# ---------- страницы ----------

def page_accounts(today: date) -> None:
    st.header("Пенсионные счета")
    st.caption(ESTIMATE_LABEL)
    accounts = load_accounts()
    for acc in accounts:
        render_card(acc, today)
    if not accounts:
        st.info("Счетов пока нет. Добавьте первый ниже.")
    with st.expander("➕ Добавить счёт", expanded=not accounts):
        account_editor("new", None)


def render_card(acc: PensionAccount, today: date) -> None:
    view, error = compute(acc, today)
    with st.container(border=True):
        title = PRODUCT_TYPES[acc.product_type].split(" — ")[0]
        st.subheader(f"{title} · {acc.fund_name} · {acc.owner}")
        st.caption(track_label(acc))
        if error:
            st.error(error)
        cols = st.columns(5)
        cols[0].metric("Баланс сейчас (оценка), ₪", format_money(view.balance_today if view else acc.balance_ils))
        cols[1].metric("За день, ₪", format_money(view.today_change_ils if view else None))
        cols[2].metric("За день, %", format_pct(view.today_change if view else None))
        cols[3].metric("С начала месяца, %", format_pct(view.month_return if view else None))
        cols[4].metric("С начала года, %", format_pct(view.ytd_return if view else None))
        st.caption(
            f"Баланс на {acc.balance_date:%d.%m.%Y}: {format_money(acc.balance_ils)} ₪ · "
            f"хедж {acc.fx_hedge_pct}% · {ESTIMATE_LABEL}"
        )
        if view:
            for w in view.warnings:
                st.warning(w)
        with st.expander("Подробнее: график, сверка, правка, баланс"):
            if view and view.path:
                fig = go.Figure(go.Scatter(x=[d for d, _ in view.path], y=[float(b) for _, b in view.path],
                                           mode="lines", name="оценка"))
                with Session() as s:
                    points = s.query(BalancePoint).filter_by(account_id=acc.id).all()
                if points:
                    fig.add_trace(go.Scatter(x=[p.date for p in points], y=[float(p.balance_ils) for p in points],
                                             mode="markers", name="официальные значения"))
                fig.update_layout(height=320, margin=dict(l=10, r=10, t=30, b=10), title=ESTIMATE_LABEL)
                st.plotly_chart(fig, use_container_width=True)
            if view and view.comparisons:
                st.markdown("**Сверка: оценка vs официальная доходность за месяц**")
                st.dataframe(
                    [
                        {
                            "Месяц": c.period,
                            "Официально, %": f"{c.official * 100:.2f}",
                            "Оценка, %": f"{c.estimate * 100:.2f}",
                            "Разница, п.п.": f"{c.diff_pp:+.2f}",
                            "Состав": "из импорта" if c.used_composition else "текущие веса",
                        }
                        for c in view.comparisons
                    ],
                    hide_index=True,
                )
            st.markdown("**Веса классов сейчас**: " + ", ".join(
                f"{CLASS_LABELS[c]} {acc.weights()[c]}%" for c in CLASSES))
            st.markdown("**Редактирование**")
            account_editor(f"acc{acc.id}", acc)
            st.markdown("**Обновить баланс** (из отчёта или личного кабинета)")
            with st.form(f"bal{acc.id}"):
                new_bal = st.number_input("Баланс, ₪", min_value=0.0, step=0.01, format="%.2f")
                new_date = st.date_input("Дата баланса", value=today)
                source = st.selectbox("Источник", ["report", "manual"],
                                      format_func=lambda x: {"report": "отчёт/кабинет", "manual": "вручную"}[x])
                if st.form_submit_button("Сохранить баланс"):
                    with Session() as s:
                        target = s.get(PensionAccount, acc.id)
                        target.balance_ils = dec(new_bal)
                        target.balance_date = new_date
                        s.add(BalancePoint(account_id=acc.id, date=new_date, balance_ils=dec(new_bal), source=source))
                        s.commit()
                    st.rerun()
            confirm = st.checkbox("Удалить этот счёт вместе с историей", key=f"del_ck{acc.id}")
            if st.button("Удалить счёт", key=f"del{acc.id}", disabled=not confirm):
                with Session() as s:
                    s.delete(s.get(PensionAccount, acc.id))
                    s.commit()
                st.rerun()


def page_summary(today: date) -> None:
    st.header("Сводка по пенсионным счетам")
    st.caption(ESTIMATE_LABEL)
    accounts = load_accounts()
    if not accounts:
        st.info("Счетов пока нет.")
        return
    total = Decimal(0)
    exposure = {c: Decimal(0) for c in CLASSES}
    fig = go.Figure()
    errors = []
    for acc in accounts:
        view, error = compute(acc, today)
        if error:
            errors.append(f"{acc.fund_name} ({acc.owner}): {error}")
            continue
        total += view.balance_today
        for c in CLASSES:
            exposure[c] += view.exposure_ils[c]
        if view.path:
            fig.add_trace(go.Scatter(x=[d for d, _ in view.path], y=[float(b) for _, b in view.path],
                                     mode="lines", name=f"{acc.fund_name} · {acc.owner}"))
    for e in errors:
        st.error(e)
    st.metric("Общий баланс (оценка), ₪", format_money(total))
    israel = exposure["israel_equity"] + exposure["israel_bonds"]
    abroad = exposure["foreign_equity"] + exposure["foreign_bonds"]
    bonds = exposure["israel_bonds"] + exposure["foreign_bonds"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("В Израиле (акции+облигации), ₪", format_money(israel))
    c2.metric("За границей (акции+облигации), ₪", format_money(abroad))
    c3.metric("Из них облигации, ₪", format_money(bonds))
    c4.metric("Кэш и прочее, ₪", format_money(exposure["cash_other"]))
    st.markdown("**Экспозиция по классам, ₪**")
    st.dataframe(
        [{"Класс": CLASS_LABELS[c], "₪": format_money(exposure[c])} for c in CLASSES],
        hide_index=True,
    )
    if fig.data:
        fig.update_layout(height=380, title=ESTIMATE_LABEL, margin=dict(l=10, r=10, t=40, b=10))
        st.plotly_chart(fig, use_container_width=True)


def page_import(today: date) -> None:
    st.header("Импорт официальных данных")
    st.caption(
        "CSV: колонки period (YYYY-MM), return_pct и, по желанию, состав из 5 колонок весов. "
        "Имена колонок меняются в config/pension.toml. Источник — פנסיה-נט / גמל-נט или отчёт фонда."
    )
    template = ",".join(["period", "return_pct"] + [cfg.columns[c] for c in CLASSES]) + "\n"
    st.download_button("Скачать шаблон CSV", template, file_name="pension_template.csv", mime="text/csv")
    accounts = load_accounts()
    if not accounts:
        st.info("Сначала добавьте счёт.")
        return
    labels = {a.id: f"{a.fund_name} · {a.owner} · {track_label(a)}" for a in accounts}
    acc_id = st.selectbox("Счёт", list(labels), format_func=lambda i: labels[i])
    upload = st.file_uploader("CSV-файл", type=["csv"])
    apply_weights = st.checkbox("Обновить веса по последнему месяцу с составом (только для ручных треков)", value=True)
    if upload is None:
        return
    text = upload.getvalue().decode("utf-8-sig")
    result = parse_official_csv(text, cfg.columns)
    for err in result.errors:
        st.error(err)
    if not result.rows:
        return
    st.dataframe(
        [
            {
                "Месяц": r.period,
                "Доходность, %": f"{r.return_pct}",
                "Состав": "есть" if r.composition else "нет",
            }
            for r in result.rows
        ],
        hide_index=True,
    )
    if st.button("Импортировать", disabled=bool(result.errors)):
        with Session() as s:
            acc = s.get(PensionAccount, acc_id)
            existing = {o.period: o for o in acc.official}
            for r in result.rows:
                row = existing.get(r.period) or OfficialMonthly(account_id=acc.id, period=r.period)
                row.return_pct = r.return_pct
                row.source = upload.name
                if r.composition:
                    for c in CLASSES:
                        setattr(row, f"w_{c}", r.composition[c])
                else:
                    for c in CLASSES:
                        setattr(row, f"w_{c}", None)
                s.add(row)
            s.flush()
            latest = [r for r in sorted(result.rows, key=lambda x: x.period) if r.composition]
            preset = cfg.tracks[acc.track_key].preset
            if apply_weights and latest and preset is None:
                last = latest[-1]
                acc.set_weights(last.composition)
                y, m = map(int, last.period.split("-"))
                next_month = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
                acc.weights_as_of = next_month - timedelta(days=1)
                st.info(f"Веса обновлены по составу за {last.period}")
            s.commit()
        st.success(f"Импортировано месяцев: {len(result.rows)}")
        st.rerun()


def page_backup() -> None:
    st.header("Бэкап и восстановление")
    st.caption(
        "Бэкап зашифрован парольной фразой. Без неё файл не открыть. Восстановление ЗАМЕНЯЕТ все данные."
    )
    phrase = st.text_input("Парольная фраза для бэкапа", type="password", key="bk_phrase")
    dest = settings.backup_dir or (settings.data_dir / "backups")
    st.write(f"Папка назначения: `{dest}` (задаётся BACKUP_DIR в .env)")
    if st.button("Создать бэкап"):
        if len(phrase) < MIN_PASSWORD_LENGTH:
            st.error(f"Фраза должна быть не короче {MIN_PASSWORD_LENGTH} символов")
        else:
            with Session() as s:
                blob = encrypt_session(s, phrase)
            dest.mkdir(parents=True, exist_ok=True)
            name = f"finance-backup-{datetime.now():%Y%m%d-%H%M%S}.fbk"
            path = dest / name
            path.write_bytes(blob)
            st.success(f"Сохранено: {path}")
            st.download_button("Скачать файл бэкапа", blob, file_name=name, mime="application/octet-stream")
    st.divider()
    st.subheader("Восстановление")
    up = st.file_uploader("Файл .fbk", type=["fbk"])
    confirm = st.checkbox("Я понимаю, что текущие данные будут заменены")
    if st.button("Восстановить", disabled=not (up and confirm and phrase)):
        try:
            with Session() as s:
                restore_blob(s, up.getvalue(), phrase)
                s.commit()
            st.success("Данные восстановлены")
        except ValueError as exc:
            st.error(str(exc))


def page_settings() -> None:
    st.header("Настройки и тикеры")
    st.write(f"Данные: `{settings.data_dir}` · База: `{settings.db_path}`")
    st.write(f"Курс USD/ILS: `{cfg.usd_ils}`")
    st.write("Общие прокси по классам:")
    st.json(cfg.proxies)
    st.write("Треки и их переопределения (правятся в config/pension.toml):")
    st.json({k: {"label": v.label, "proxies": v.proxies, "preset": {c: str(x) for c, x in (v.preset or {}).items()} or None}
             for k, v in cfg.tracks.items()})
    st.caption("Тикеры помечены «проверить» в config/pension.toml и должны быть подтверждены в yfinance.")
    if st.button("Выйти"):
        st.session_state["auth"] = False
        st.rerun()


# ---------- запуск ----------

login_gate()
today = date.today()
with st.sidebar:
    st.title("📊 Учёт инвестиций")
    page = st.radio("Раздел", PAGES, label_visibility="collapsed")
    st.caption("Данные только локально. Запросы — только за ценами (тикеры и даты).")

if page == PAGES[0]:
    page_accounts(today)
elif page == PAGES[1]:
    page_summary(today)
elif page == PAGES[2]:
    page_import(today)
elif page == PAGES[3]:
    page_backup()
else:
    page_settings()

st.caption(
    "Это инструмент для личного учёта, не инвестиционный совет. "
    "Налоговые расчёты ориентировочные — сверяйте с налоговым консультантом."
)
