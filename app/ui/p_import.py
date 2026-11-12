"""Экран «Импорт CSV»: отчёт IBKR или любой CSV с настраиваемым соответствием колонок."""

from __future__ import annotations

import json

import streamlit as st

from app.db import Asset, CsvTemplate, Custodian
from app.importers.trades_csv import (DEFAULT_MAPPING, GENERIC_FIELDS, ParsedImport, csv_headers, decode_bytes,
                                      parse_generic, parse_ibkr)
from app.services.importing import apply_import, unknown_symbols
from app.ui.common import Session, custodian_label, custodians, ensure_fx, flash, market, qty, show_flash, table

NONE = "— нет —"


def _mapping_ui(cust_id: int, text: str) -> dict:
    with Session() as s:
        tpl = s.query(CsvTemplate).filter(CsvTemplate.custodian_id == cust_id).first()
    saved = json.loads(tpl.mapping) if tpl else {}
    cfg = {**DEFAULT_MAPPING, **saved}
    headers = csv_headers(text, cfg["delimiter"])
    st.markdown("**Соответствие колонок**" + (" (загружен сохранённый шаблон этого места)" if tpl else ""))
    options = [NONE] + headers
    mapping: dict = {}
    cols = st.columns(3)
    for i, (key, label) in enumerate(GENERIC_FIELDS.items()):
        current = cfg.get(key) if cfg.get(key) in headers else NONE
        choice = cols[i % 3].selectbox(label, options, index=options.index(current), key=f"map_{cust_id}_{key}")
        mapping[key] = None if choice == NONE else choice
    a, b, c = st.columns(3)
    formats = ["auto", "%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d", "%d.%m.%Y", "%d/%m/%y"]
    fmt = cfg["date_format"] if cfg["date_format"] in formats else "auto"
    mapping["date_format"] = a.selectbox("Формат даты", formats, index=formats.index(fmt), key=f"map_{cust_id}_fmt",
                                         help="auto — определить; если день и месяц путаются, выберите явно.")
    mapping["decimal_comma"] = b.checkbox("Десятичная запятая (1.234,56)", value=bool(cfg["decimal_comma"]),
                                          key=f"map_{cust_id}_comma")
    mapping["default_currency"] = c.text_input("Валюта по умолчанию", value=cfg["default_currency"],
                                               key=f"map_{cust_id}_ccy")
    with st.expander("Слова в колонке «Тип операции»"):
        mapping["buy_values"] = st.text_input("Покупка", value=cfg["buy_values"], key=f"map_{cust_id}_buy")
        mapping["sell_values"] = st.text_input("Продажа", value=cfg["sell_values"], key=f"map_{cust_id}_sell")
        mapping["dividend_values"] = st.text_input("Дивиденд", value=cfg["dividend_values"], key=f"map_{cust_id}_div")
        st.caption("Если колонки типа нет: положительное количество — покупка, отрицательное — продажа.")
    mapping["delimiter"] = cfg["delimiter"]
    if st.button("💾 Сохранить шаблон для этого места", key=f"map_{cust_id}_save"):
        with Session() as s:
            row = s.query(CsvTemplate).filter(CsvTemplate.custodian_id == cust_id).first()
            if row is None:
                s.add(CsvTemplate(custodian_id=cust_id, mapping=json.dumps(mapping, ensure_ascii=False)))
            else:
                row.mapping = json.dumps(mapping, ensure_ascii=False)
            s.commit()
        st.success("Шаблон сохранён — в следующий раз колонки подставятся сами.")
    return mapping


def _preview(parsed: ParsedImport) -> None:
    for e in parsed.errors:
        st.error(e)
    for w in parsed.warnings:
        st.warning(w)
    a, b = st.columns(2)
    a.metric("Сделок", len(parsed.trades))
    b.metric("Выплат", len(parsed.dividends))
    if parsed.trades:
        table([{
            "Дата": t.day.strftime("%d.%m.%Y"), "Актив": t.symbol, "Операция": "покупка" if t.qty > 0 else "продажа",
            "Кол-во": qty(abs(t.qty)), "Цена": qty(t.price), "Комиссия": qty(t.fee), "Валюта": t.currency,
        } for t in parsed.trades], height=260)
    if parsed.dividends:
        table([{
            "Дата": d.day.strftime("%d.%m.%Y"), "Актив": d.symbol, "Брутто": qty(d.gross),
            "Удержано за рубежом": qty(d.foreign_tax), "Валюта": d.currency,
        } for d in parsed.dividends], height=200)


def render() -> None:
    st.title("Импорт CSV")
    show_flash()
    st.caption("Покупки станут лотами, продажи спишутся по FIFO, дивиденды попадут в «Фактически получено». "
               "Повторная загрузка того же файла безопасна: уже импортированные строки пропускаются. "
               "Файл обрабатывается только на вашем компьютере.")
    cust_list = custodians()
    if not cust_list:
        st.info("Сначала создайте место хранения, в которое импортируете.")
        return
    a, b = st.columns(2)
    cust = a.selectbox("Место хранения", cust_list, format_func=custodian_label)
    fmt = b.radio("Формат файла", ["IBKR Activity Statement", "Другой CSV (Blink, IBI, Binance…)"], horizontal=True)
    if fmt.startswith("IBKR"):
        st.caption("В IBKR: Performance & Reports → Statements → Activity → формат CSV.")
    upload = st.file_uploader("CSV-файл", type=["csv", "txt"])
    if upload is None:
        return
    text = decode_bytes(upload.getvalue())
    parsed = parse_ibkr(text) if fmt.startswith("IBKR") else parse_generic(text, _mapping_ui(cust.id, text))
    _preview(parsed)
    if not parsed.trades and not parsed.dividends:
        return

    with Session() as s:
        missing = unknown_symbols(s, parsed)
    if missing:
        st.warning("Этих активов нет в справочнике: " + ", ".join(missing))
        create = st.multiselect("Создать автоматически (как ETF/акции с тикером Yahoo = символ)", missing,
                                default=missing)
        st.caption("После импорта проверьте у созданных активов домициль, тип выплат и тикер "
                   "(раздел «Активы и операции → Активы»). Лондонским бумагам нужен суффикс .L.")
    else:
        create = []

    if not st.button("Импортировать", type="primary", disabled=bool(parsed.errors)):
        if parsed.errors:
            st.caption("Исправьте ошибки в файле или в соответствии колонок — тогда импорт станет доступен.")
        return
    days = [t.day for t in parsed.trades] + [d.day for d in parsed.dividends]
    currencies = {t.currency for t in parsed.trades} | {d.currency for d in parsed.dividends} | {"USD"}
    error = ensure_fx(min(days), currencies)
    if error:
        st.warning(f"Курсы Банка Израиля не обновились: {error}")
    with Session() as s:
        currency_of = {t.symbol: t.currency for t in parsed.trades}
        for symbol in create:
            s.add(Asset(symbol=symbol.upper(), name="", asset_class="etf", currency=currency_of.get(symbol, "USD"),
                        price_source="yfinance", source_id=symbol,
                        domicile="US" if "." not in symbol else ""))
        s.flush()
        report = apply_import(s, market(), s.get(Custodian, cust.id), parsed)
        s.commit()
    message = (f"Импорт завершён: лотов {report.lots}, продаж {report.sales}, выплат {report.dividends}, "
               f"пропущено как уже загруженные {report.skipped}.")
    if report.errors:
        st.warning(message)
        st.error("Не импортировано:\n\n" + "\n\n".join(f"• {e}" for e in report.errors))
        st.caption("Исправьте причину и загрузите файл ещё раз — уже импортированные строки не задвоятся.")
    else:
        flash(message + " Нажмите «Обновить данные», чтобы подтянуть цены.")
        st.rerun()
