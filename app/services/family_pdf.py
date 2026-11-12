"""PDF «Что где лежит» для семейной экстренной папки. Без паролей, ключей и seed-фраз."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

FONT_CANDIDATES: list[tuple[str, str]] = [
    # (обычный, жирный) — нужен шрифт с кириллицей и ивритом
    (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf"),
    (r"C:\Windows\Fonts\tahoma.ttf", r"C:\Windows\Fonts\tahomabd.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/Library/Fonts/Arial Unicode.ttf", "/Library/Fonts/Arial Unicode.ttf"),
    ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
]


class PdfUnavailable(RuntimeError):
    pass


@dataclass
class PlaceBlock:
    title: str
    details: list[str]
    holdings: list[tuple[str, str, str]] = field(default_factory=list)  # (актив, количество, стоимость)


@dataclass
class FamilyDoc:
    generated: date
    intro: str
    owners: dict[str, list[PlaceBlock]]  # владелец → места
    pensions: list[tuple[str, str, str, str]]  # (владелец, продукт, компания/трек, баланс)
    totals: list[tuple[str, str]]  # (подпись, значение)
    note: str = ""


def find_fonts() -> tuple[str, str]:
    custom = os.getenv("PDF_FONT")
    if custom and Path(custom).exists():
        return custom, custom
    for regular, bold in FONT_CANDIDATES:
        if Path(regular).exists():
            return regular, bold if Path(bold).exists() else regular
    raise PdfUnavailable(
        "Не найден шрифт с кириллицей и ивритом. Укажите путь к TTF-файлу в переменной PDF_FONT файла .env."
    )


def build_pdf(doc: FamilyDoc) -> bytes:
    try:
        from fpdf import FPDF
    except ImportError as exc:  # pragma: no cover
        raise PdfUnavailable("Не установлена библиотека fpdf2 (запустите install.bat)") from exc
    regular, bold = find_fonts()
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_font("body", "", regular)
    pdf.add_font("body", "B", bold)
    try:
        pdf.set_text_shaping(True)  # корректный порядок букв для иврита
    except Exception:  # noqa: BLE001 — без uharfbuzz документ всё равно создаётся
        pass
    pdf.add_page()
    width = pdf.epw

    def text(value: str, size: int = 10, style: str = "", gap: float = 1.5) -> None:
        pdf.set_font("body", style, size)
        pdf.multi_cell(width, size * 0.5, value, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(gap)

    text("Что где лежит", 20, "B")
    text(f"Сводка для семьи · составлено {doc.generated:%d.%m.%Y}", 10)
    text(doc.intro, 9)
    pdf.ln(2)

    if doc.totals:
        text("Итого (ориентировочно)", 13, "B")
        for label, value in doc.totals:
            text(f"{label}: {value}", 10, gap=0.5)
        pdf.ln(3)

    for owner, places in doc.owners.items():
        text(f"Владелец: {owner}", 14, "B")
        if not places:
            text("Мест хранения нет.", 10)
        for place in places:
            pdf.set_fill_color(235, 235, 235)
            pdf.set_font("body", "B", 11)
            pdf.multi_cell(width, 7, place.title, fill=True, new_x="LMARGIN", new_y="NEXT")
            for line in place.details:
                text(line, 9, gap=0.3)
            if place.holdings:
                pdf.set_font("body", "", 9)
                with pdf.table(col_widths=(46, 27, 27), text_align=("LEFT", "RIGHT", "RIGHT"),
                               line_height=5.5, first_row_as_headings=True) as table:
                    header = table.row()
                    for cell in ("Актив", "Количество", "Стоимость ≈"):
                        header.cell(cell)
                    for row_values in place.holdings:
                        row = table.row()
                        for cell in row_values:
                            row.cell(cell)
            else:
                text("Сейчас пусто.", 9)
            pdf.ln(3)

    if doc.pensions:
        text("Пенсионные и накопительные счета", 14, "B")
        pdf.set_font("body", "", 9)
        with pdf.table(col_widths=(18, 30, 34, 18), text_align=("LEFT", "LEFT", "LEFT", "RIGHT"), line_height=5.5,
                       first_row_as_headings=True) as table:
            header = table.row()
            for cell in ("Владелец", "Продукт", "Компания и трек", "Баланс ≈"):
                header.cell(cell)
            for row_values in doc.pensions:
                row = table.row()
                for cell in row_values:
                    row.cell(cell)
        pdf.ln(3)

    if doc.note.strip():
        text("Заметки для семьи", 14, "B")
        text(doc.note.strip(), 10)

    text("В этом документе намеренно нет паролей, PIN-кодов, seed-фраз и приватных ключей. "
         "Они хранятся отдельно. Суммы ориентировочные, на дату составления.", 8)
    return bytes(pdf.output())
