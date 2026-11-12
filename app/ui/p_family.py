"""Экран «Для семьи»: что где лежит + экспорт в PDF."""

from __future__ import annotations

import streamlit as st
from sqlalchemy.orm import joinedload

from app.db import CUSTODIAN_KINDS, Custodian
from app.domain.money import ZERO
from app.domain.pension import PRODUCT_TYPES
from app.services.family_pdf import FamilyDoc, PdfUnavailable, PlaceBlock, build_pdf
from app.services.portfolio import group_positions, load_positions, totals
from app.ui.common import Session, cfg, cfg_set, flash, ils, market, owners, qty, runtime, show_flash, table, today, usd
from app.ui.pension_data import compute, load_accounts

INTRO = ("Список мест, где хранятся наши активы, и что в каждом лежит. Документ нужен, чтобы семья знала, "
         "куда обращаться. Паролей и ключей здесь нет.")


def _collect() -> FamilyDoc:
    m, day = market(), today()
    config = runtime()[2]
    with Session() as s:
        custodians = (s.query(Custodian).options(joinedload(Custodian.owner))
                      .filter(Custodian.archived.is_(False)).order_by(Custodian.name).all())
        positions = load_positions(s, m, day)
    blocks: dict[str, list[PlaceBlock]] = {o.name: [] for o in owners()}
    for c in custodians:
        mine = [p for p in positions if p.custodian_id == c.id]
        details = [f"Тип: {CUSTODIAN_KINDS.get(c.kind, c.kind)}" + (f" · страна: {c.country}" if c.country else "")]
        if c.wallet_type or c.network:
            details.append("Кошелёк: " + " · ".join(x for x in (c.wallet_type, c.network) if x))
        if c.location_note:
            details.append(f"Где лежит физически: {c.location_note}")
        if c.note:
            details.append(f"Заметка: {c.note}")
        unit = lambda p: " г" if p.asset_class == "metal" else ""  # noqa: E731
        holdings = []
        for g in group_positions(mine, lambda p: p.symbol):
            sample = next(p for p in mine if p.symbol == g.name)
            name = f"{g.name} — {sample.asset_name}" if sample.asset_name else g.name
            holdings.append((name, qty(g.qty) + unit(sample), usd(g.value_usd) if g.unpriced == 0 else "нет цены"))
        blocks.setdefault(c.owner.name, []).append(PlaceBlock(c.name, details, holdings))
    pensions = []
    pension_total = ZERO
    for acc in load_accounts():
        view, _ = compute(acc, m, day)
        balance = view.balance_today if view else acc.balance_ils
        pension_total += balance
        track = acc.track_custom if acc.track_key == "other" and acc.track_custom else config.tracks[acc.track_key].label
        pensions.append((acc.owner.name, PRODUCT_TYPES[acc.product_type].split(" — ")[0],
                         f"{acc.fund_name} · {track.split(' — ')[0]}", ils(balance)))
    total = totals(positions)
    summary = [("Инвестиции и накопления", f"{usd(total.value_usd)} ({ils(total.value_ils)})")]
    if pension_total:
        summary.append(("Пенсионные и накопительные счета", ils(pension_total)))
    return FamilyDoc(day, INTRO, blocks, pensions, summary, cfg("family_note"))


def render() -> None:
    st.title("Для семьи: что где лежит")
    show_flash()
    st.caption("Сводка для семейной экстренной папки. Без паролей, PIN-кодов, seed-фраз и приватных ключей — "
               "их здесь нет и вводить их не нужно.")
    doc = _collect()
    for label, value in doc.totals:
        st.metric(label, value)
    for owner, places in doc.owners.items():
        st.subheader(owner)
        if not places:
            st.caption("Мест хранения нет.")
        for place in places:
            with st.container(border=True):
                st.markdown(f"**{place.title}**")
                for line in place.details:
                    st.caption(line)
                table([{"Актив": a, "Количество": q, "Стоимость ≈": v} for a, q, v in place.holdings])
    if doc.pensions:
        st.subheader("Пенсионные и накопительные счета")
        table([{"Владелец": o, "Продукт": p, "Компания и трек": c, "Баланс ≈": b} for o, p, c, b in doc.pensions])

    with st.form("family_note"):
        note = st.text_area(
            "Заметки для семьи (попадут в PDF)", value=doc.note, height=120,
            help="Например: к кому обращаться, где лежат документы. Не пишите сюда пароли и ключи.")
        if st.form_submit_button("Сохранить заметки"):
            cfg_set(family_note=note)
            flash("Заметки сохранены")
            st.rerun()

    try:
        pdf = build_pdf(doc)
    except PdfUnavailable as exc:
        st.warning(str(exc))
    else:
        st.download_button("📄 Скачать PDF для семейной папки", pdf, f"chto-gde-lezhit-{doc.generated:%Y%m%d}.pdf",
                           "application/pdf", type="primary")
        st.caption("PDF не зашифрован: храните распечатку и файл там же, где остальные важные документы.")
