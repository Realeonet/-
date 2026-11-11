"""Импорт ежемесячной официальной доходности и состава трека из CSV.

Колонки задаются в config/pension.toml [columns]. Разделитель определяется автоматически
(, или ;). Числа: «1,23», «1.23», «1.23%» — все допустимы.
Состав необязателен: если все 5 колонок весов заполнены в строке — он сохранится и станет весами.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from app.domain.pension import CLASSES, validate_weights


@dataclass(frozen=True)
class OfficialRow:
    period: str  # YYYY-MM
    return_pct: Decimal
    composition: dict[str, Decimal] | None


@dataclass
class ImportResult:
    rows: list[OfficialRow] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and bool(self.rows)


def _num(raw: str | None) -> Decimal:
    text = (raw or "").strip().replace("%", "").replace(" ", "").replace(" ", "")
    if "," in text and "." not in text:
        text = text.replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"не число: «{raw}»") from exc


def normalize_period(raw: str | None) -> str:
    text = (raw or "").strip()
    m = re.fullmatch(r"(\d{4})-(\d{1,2})(?:-\d{1,2})?", text)
    if m:
        year, month = m.group(1), int(m.group(2))
    else:
        m = re.fullmatch(r"(\d{1,2})/(\d{4})", text)
        if not m:
            raise ValueError(f"непонятный период «{raw}» (ожидается YYYY-MM или MM/YYYY)")
        month, year = int(m.group(1)), m.group(2)
    if not 1 <= month <= 12:
        raise ValueError(f"месяц вне диапазона: «{raw}»")
    return f"{year}-{month:02d}"


def parse_official_csv(text: str, columns: Mapping[str, str]) -> ImportResult:
    first_line = text.splitlines()[0] if text.strip() else ""
    delimiter = ";" if first_line.count(";") > first_line.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    headers = {h.strip(): h for h in (reader.fieldnames or [])}

    missing = [columns[k] for k in ("period", "return_pct") if columns[k] not in headers]
    if missing:
        return ImportResult(errors=[f"В файле нет колонок: {', '.join(missing)}"])

    weight_names = [columns[c] for c in CLASSES]
    present = [name for name in weight_names if name in headers]
    if present and len(present) != len(weight_names):
        return ImportResult(
            errors=["Колонки состава заполнены не полностью: нужны все 5 или ни одной"]
        )
    has_composition = len(present) == len(weight_names)

    result = ImportResult()
    seen: set[str] = set()
    for line_no, rec in enumerate(reader, start=2):
        try:
            period = normalize_period(rec[headers[columns["period"]]])
            if period in seen:
                raise ValueError(f"период {period} повторяется в файле")
            seen.add(period)
            ret = _num(rec[headers[columns["return_pct"]]])
            composition = None
            if has_composition:
                raw = {c: rec[headers[columns[c]]] for c in CLASSES}
                if all((v or "").strip() == "" for v in raw.values()):
                    composition = None
                else:
                    composition = {c: _num(raw[c]) for c in CLASSES}
                    validate_weights(composition)
            result.rows.append(OfficialRow(period=period, return_pct=ret, composition=composition))
        except ValueError as exc:
            result.errors.append(f"Строка {line_no}: {exc}")
    return result
