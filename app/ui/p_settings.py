"""Экран «Настройки»: параметры расчётов, владельцы, курсы, бэкап, пароль."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from app.backup import restore_blob, write_backup_file
from app.db import FxPoint, Owner, get_setting, set_setting
from app.security import MIN_PASSWORD_LENGTH, hash_password, verify_password
from app.ui.common import (Session, cfg, cfg_set, dec_input, flash, market, offline, parse_dec, runtime, show_flash,
                           table, today)


def backup_dir() -> Path:
    settings = runtime()[0]
    return settings.backup_dir or (settings.data_dir / "backups")


def _tab_params() -> None:
    with st.form("params"):
        st.markdown("**Налоги** (ориентировочный расчёт)")
        a, b = st.columns(2)
        rate_raw = dec_input("Ставка налога на прибыль и дивиденды, %", "p_rate", cfg("tax_rate_pct"), container=a)
        surtax_raw = dec_input("מס יסף — доплата, % (0 = не учитывать)", "p_surtax", cfg("surtax_pct"), container=b,
                               help="Доплата для высоких доходов. Процент и применимость — проверить с консультантом.")
        methods = {"nominal": "Номинальный: ₪ по курсам дат покупки и продажи (курсовая разница облагается)",
                   "linked": "С привязкой к валюте: прибыль в валюте × курс дня продажи"}
        keys = list(methods)
        method = st.radio("Метод расчёта прибыли в шекелях", keys, index=keys.index(cfg("tax_method") or "nominal"),
                          format_func=lambda k: methods[k])
        st.caption("Оба значения всегда показываются рядом в таблицах; метод определяет, какое идёт в налог.")
        st.markdown("**Риски**")
        conc_raw = dec_input("Порог концентрации в одном месте хранения, %", "p_conc", cfg("concentration_pct"))
        st.markdown("**Данные**")
        auto_refresh = st.checkbox("Автоматически обновлять котировки при открытии (раз в час)",
                                   value=cfg("auto_refresh") == "1")
        auto_backup = st.checkbox("Автобэкап раз в день при входе (шифруется паролем входа)",
                                  value=cfg("auto_backup") == "1")
        if st.form_submit_button("Сохранить"):
            try:
                rate = parse_dec(rate_raw, "Ставка налога")
                surtax = parse_dec(surtax_raw, "מס יסף")
                conc = parse_dec(conc_raw, "Порог концентрации", positive=True)
                if rate + surtax > 100 or conc > 100:
                    raise ValueError("Проценты не могут быть больше 100")
                cfg_set(tax_rate_pct=rate, surtax_pct=surtax, tax_method=method, concentration_pct=conc,
                        auto_refresh="1" if auto_refresh else "0", auto_backup="1" if auto_backup else "0")
                flash("Настройки сохранены")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    st.caption("Пороги наследства США — в разделе «Наследство США». Правила Минкина — в разделе «Алгоритм Минкина».")


def _tab_owners() -> None:
    with Session() as s:
        owner_list = s.query(Owner).order_by(Owner.id).all()
    with st.form("owners"):
        names = [st.text_input(f"Владелец {i + 1}", value=o.name, key=f"own_{o.id}") for i, o in enumerate(owner_list)]
        new = st.text_input("Добавить владельца", key="own_new")
        if st.form_submit_button("Сохранить"):
            cleaned = [n.strip() for n in names]
            if any(not n for n in cleaned) or len(set(cleaned + ([new.strip()] if new.strip() else []))) != len(
                    cleaned) + (1 if new.strip() else 0):
                st.error("Имена должны быть непустыми и разными")
            else:
                with Session() as s:
                    for o, name in zip(owner_list, cleaned):
                        s.get(Owner, o.id).name = name
                    if new.strip():
                        s.add(Owner(name=new.strip()))
                    s.commit()
                flash("Владельцы сохранены")
                st.rerun()


def _tab_data() -> None:
    m = market()
    last = m.last_refresh("quotes")
    st.write("Последнее обновление котировок: " + (last.strftime("%d.%m.%Y %H:%M") if last else "ещё не было"))
    errors = st.session_state.get("refresh_errors") or []
    if errors:
        st.warning("При последнем обновлении:\n\n" + "\n\n".join(f"• {e}" for e in errors))
    with Session() as s:
        rows = s.query(FxPoint).order_by(FxPoint.date.desc()).limit(400).all()
    latest: dict[str, FxPoint] = {}
    for r in rows:
        latest.setdefault(r.currency, r)
    table([{"Валюта": c, "₪ за 1 единицу": str(r.rate), "Дата": r.date.strftime("%d.%m.%Y"),
            "Источник": "Банк Израиля" if r.source == "boi" else "вручную"} for c, r in sorted(latest.items())])
    st.markdown("**Ввести курс вручную** (если Банк Израиля недоступен)")
    with st.form("fx_manual"):
        a, b, c = st.columns(3)
        currency = a.text_input("Валюта", value="USD")
        day = b.date_input("Дата", value=today(), max_value=today(), format="DD.MM.YYYY")
        rate_raw = dec_input("₪ за 1 единицу", "fx_manual_rate", container=c)
        if st.form_submit_button("Сохранить курс"):
            try:
                m.set_manual_fx(currency.strip().upper(), day, parse_dec(rate_raw, "Курс", positive=True))
                flash("Курс сохранён")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    st.caption("Источники: курсы — Банк Израиля (שער יציג); акции, ETF, индексы — Yahoo Finance; крипта — CoinGecko; "
               "металлы — спот gold-api.com (история — фьючерсы Yahoo); пенсия — data.gov.il. Наружу уходят только "
               "тикеры и даты. Ваши суммы, количества и адреса не передаются.")
    if offline():
        st.info("Режим без сети (FINANCE_OFFLINE=1): обновление отключено.")


def _tab_backup() -> None:
    dest = backup_dir()
    st.markdown("Бэкап — один зашифрованный файл `.fbk` со всеми вашими данными. Без парольной фразы его не открыть.")
    st.write(f"Папка для бэкапов: `{dest}`")
    st.caption("Задаётся параметром BACKUP_DIR в файле .env. Укажите папку, которую синхронизирует Google Drive — "
               "тогда зашифрованная копия сама уходит в облако. Саму базу в синхронизируемую папку класть нельзя.")
    autos = sorted(dest.glob("finance-backup-auto-*.fbk")) if dest.exists() else []
    if autos:
        st.caption(f"Автобэкапов в папке: {len(autos)}, последний — {autos[-1].name}. "
                   "Они зашифрованы паролем входа, который действовал в день создания.")
    phrase = st.text_input("Парольная фраза", type="password", key="bk_phrase",
                           help="Для ручного бэкапа — любая фраза от 8 символов. Запомните её: восстановить без неё нельзя.")
    if st.button("Создать бэкап сейчас"):
        if len(phrase) < MIN_PASSWORD_LENGTH:
            st.error(f"Фраза должна быть не короче {MIN_PASSWORD_LENGTH} символов")
        else:
            try:
                with Session() as s:
                    path = write_backup_file(s, phrase, dest)
                st.success(f"Сохранено: {path}")
                st.download_button("Скачать файл", path.read_bytes(), path.name, "application/octet-stream")
            except OSError as exc:
                st.error(f"Не удалось записать в папку бэкапов: {exc}")
    st.divider()
    st.markdown("**Восстановление** — заменяет все текущие данные данными из файла. Пароль входа остаётся текущим.")
    upload = st.file_uploader("Файл .fbk", type=["fbk"])
    confirm = st.checkbox("Я понимаю, что текущие данные будут заменены")
    if st.button("Восстановить", disabled=not (upload and confirm and phrase)):
        try:
            with Session() as s:
                try:
                    write_backup_file(s, phrase, dest, "before-restore-")
                except OSError:
                    pass
                restore_blob(s, upload.getvalue(), phrase)
                s.commit()
            flash("Данные восстановлены. Перед этим текущее состояние сохранено в файл before-restore.")
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))


def _tab_password() -> None:
    with st.form("pw"):
        old = st.text_input("Текущий пароль", type="password")
        new1 = st.text_input("Новый пароль", type="password")
        new2 = st.text_input("Повторите новый пароль", type="password")
        if st.form_submit_button("Сменить пароль"):
            with Session() as s:
                stored = get_setting(s, "password_hash")
            if not stored or not verify_password(old, stored):
                st.error("Текущий пароль неверен")
            elif new1 != new2:
                st.error("Новые пароли не совпадают")
            else:
                try:
                    digest = hash_password(new1)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    with Session() as s:
                        set_setting(s, "password_hash", digest)
                        s.commit()
                    st.session_state["_pw"] = new1
                    flash("Пароль изменён. Новые автобэкапы будут зашифрованы новым паролем.")
                    st.rerun()
    st.caption("Пароль хранится только как хэш (scrypt). Если забудете его — данные можно вернуть из ручного бэкапа "
               "с известной парольной фразой.")


def render() -> None:
    st.title("Настройки")
    show_flash()
    settings = runtime()[0]
    tabs = st.tabs(["Параметры", "Владельцы", "Курсы и данные", "Бэкап", "Пароль"])
    for tab, fn in zip(tabs, (_tab_params, _tab_owners, _tab_data, _tab_backup, _tab_password)):
        with tab:
            fn()
    st.caption(f"База данных: `{settings.db_path}`")
