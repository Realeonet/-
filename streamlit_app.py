"""Личный учёт инвестиций — точка входа Streamlit.

Запуск: start.bat (Windows) или `streamlit run streamlit_app.py`. Подробности — README.md.
Данные хранятся локально; наружу уходят только запросы цен (тикеры и даты).
"""

from __future__ import annotations

import time

import streamlit as st

st.set_page_config(page_title="Мой капитал", page_icon="📊", layout="wide", initial_sidebar_state="auto")

from app.backup import auto_backup  # noqa: E402
from app.db import get_setting, set_setting  # noqa: E402
from app.security import MIN_PASSWORD_LENGTH, hash_password, verify_password  # noqa: E402
from app.ui import (p_analysis, p_capital, p_custodians, p_family, p_import, p_income, p_ops, p_pension,  # noqa: E402
                    p_rules, p_settings)
from app.ui.common import Session, auto_refresh, cfg, market, offline, refresh_data  # noqa: E402


def _after_login(password: str) -> None:
    st.session_state["auth"] = True
    st.session_state["_pw"] = password
    if cfg("auto_backup") == "1":
        try:
            with Session() as s:
                path = auto_backup(s, password, p_settings.backup_dir())
            if path is not None:
                st.session_state["_auto_backup"] = path.name
        except OSError as exc:
            st.session_state["_auto_backup_error"] = str(exc)


def login_gate() -> None:
    with Session() as s:
        stored = get_setting(s, "password_hash")
    if stored is None:
        st.title("Первый запуск")
        st.write("Задайте пароль для входа в приложение. Он нужен, чтобы данные не увидел случайный человек "
                 "в домашней сети.")
        with st.form("setup_pw"):
            p1 = st.text_input(f"Пароль (не короче {MIN_PASSWORD_LENGTH} символов)", type="password")
            p2 = st.text_input("Повторите пароль", type="password")
            ok = st.form_submit_button("Сохранить и войти")
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
                    _after_login(p1)
                    st.rerun()
        st.caption("Приложение никогда не спрашивает пароли от брокеров, seed-фразы и приватные ключи.")
        st.stop()
    if not st.session_state.get("auth"):
        st.title("📊 Мой капитал")
        with st.form("login"):
            pw = st.text_input("Пароль", type="password")
            if st.form_submit_button("Войти"):
                if verify_password(pw, stored):
                    st.session_state["_fails"] = 0
                    _after_login(pw)
                    st.rerun()
                fails = st.session_state.get("_fails", 0) + 1
                st.session_state["_fails"] = fails
                time.sleep(min(2 ** fails, 30) / 2)  # замедление подбора
                st.error("Неверный пароль")
        st.stop()


def sidebar() -> None:
    with st.sidebar:
        m = market()
        last = m.last_refresh("quotes")
        if st.button("🔄 Обновить данные", width="stretch", disabled=offline(),
                     help="Курсы Банка Израиля, котировки, история, дивиденды"):
            with st.spinner("Обновляю…"):
                errors = refresh_data()
            if errors:
                st.warning(f"Обновлено с замечаниями ({len(errors)}) — см. «Настройки → Курсы и данные».")
            st.rerun()
        st.caption("Обновлено: " + (last.strftime("%d.%m %H:%M") if last else "ещё не обновлялось"))
        if st.session_state.pop("_auto_backup", None):
            st.caption("✅ Автобэкап за сегодня создан")
        error = st.session_state.pop("_auto_backup_error", None)
        if error:
            st.warning(f"Автобэкап не удался: {error}")
        if st.button("Выйти", width="stretch"):
            st.session_state.clear()
            st.rerun()
        st.caption("Данные хранятся только на этом компьютере.")


PAGES = {
    "Обзор": [
        st.Page(p_capital.render, title="Общий капитал", icon="🏠", url_path="capital", default=True),
        st.Page(p_custodians.render, title="Места хранения", icon="🏦", url_path="custodians"),
        st.Page(p_ops.render, title="Активы и операции", icon="📒", url_path="operations"),
        st.Page(p_pension.render, title="Пенсия", icon="🏛", url_path="pension"),
    ],
    "Доход и налоги": [
        st.Page(p_income.render_dividends, title="Дивиденды", icon="💸", url_path="dividends"),
        st.Page(p_income.render_taxes, title="Налоги (Израиль)", icon="🧾", url_path="taxes"),
        st.Page(p_rules.render_estate, title="Наследство США", icon="🇺🇸", url_path="estate"),
    ],
    "Решения": [
        st.Page(p_rules.render_minkin, title="Алгоритм Минкина", icon="🧭", url_path="minkin"),
        st.Page(p_analysis.render_compare, title="Сравнение активов", icon="⚖️", url_path="compare"),
        st.Page(p_rules.render_fees, title="Комиссии", icon="🪙", url_path="fees"),
        st.Page(p_rules.render_debt, title="Долг vs инвестиции", icon="📉", url_path="debt"),
        st.Page(p_analysis.render_sandbox, title="Песочница", icon="🧪", url_path="sandbox"),
    ],
    "Сервис": [
        st.Page(p_family.render, title="Для семьи", icon="👨‍👩‍👧", url_path="family"),
        st.Page(p_import.render, title="Импорт CSV", icon="⬆️", url_path="import"),
        st.Page(p_settings.render, title="Настройки", icon="⚙️", url_path="settings"),
    ],
}

login_gate()
page = st.navigation(PAGES, expanded=True)
sidebar()
auto_refresh()
page.run()
st.divider()
st.caption("Это личный инструмент учёта, а не инвестиционный совет. Налоговые расчёты ориентировочные — "
           "сверяйте с налоговым консультантом.")
