"""Импорт сделок и дивидендов из CSV.

  • parse_ibkr — Activity Statement Interactive Brokers (разделы Trades, Dividends, Withholding Tax).
  • parse_generic — любой CSV (Blink, IBI, Binance…) по настраиваемому соответствию колонок;
    шаблон соответствия сохраняется для каждого места хранения.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from app.domain.money import ZERO


@dataclass(frozen=True)
class ImportTrade:
    day: date
    symbol: str
    qty: Decimal  # > 0 покупка, < 0 продажа
    price: Decimal
    fee: Decimal  # ≥ 0
    currency: str
    category: str = ""

    def fingerprint(self, seq: int) -> str:
        raw = f"T|{self.day}|{self.symbol}|{self.qty}|{self.price}|{self.fee}|{self.currency}|{seq}"
        return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True)
class ImportDividend:
    day: date
    symbol: str
    gross: Decimal
    foreign_tax: Decimal
    currency: str

    def fingerprint(self, seq: int) -> str:
        raw = f"D|{self.day}|{self.symbol}|{self.gross}|{self.foreign_tax}|{self.currency}|{seq}"
        return hashlib.sha256(raw.encode()).hexdigest()


@dataclass
class ParsedImport:
    trades: list[ImportTrade] = field(default_factory=list)
    dividends: list[ImportDividend] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def to_decimal(raw: str | None, decimal_comma: bool = False) -> Decimal:
    text = (raw or "").strip().replace(" ", "").replace(" ", "")
    text = re.sub(r"[₪$€£%]", "", text)
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    if decimal_comma:
        text = text.replace(".", "").replace(",", ".")
    else:
        text = text.replace(",", "")
    if text in ("", "-", "--"):
        raise ValueError(f"не число: «{raw}»")
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"не число: «{raw}»") from exc


DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d.%m.%Y", "%m/%d/%Y", "%Y/%m/%d", "%d-%m-%Y", "%Y%m%d", "%d/%m/%y")


def to_date(raw: str | None, fmt: str = "auto") -> date:
    text = (raw or "").strip()
    text = re.split(r"[ T,;]", text, maxsplit=1)[0] if fmt == "auto" else text
    if not text:
        raise ValueError("пустая дата")
    formats = DATE_FORMATS if fmt == "auto" else (fmt,)
    for f in formats:
        try:
            return datetime.strptime(text, f).date()
        except ValueError:
            continue
    raise ValueError(f"непонятная дата «{raw}»")


def decode_bytes(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16", "cp1255", "cp1251", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeError:
            continue
    return data.decode("utf-8", errors="replace")


# ---------------- IBKR ----------------

_IBKR_SYMBOL = re.compile(r"^\s*([A-Za-z0-9.\- ]+?)\s*\(")
IBKR_CATEGORIES = ("Stocks",)


def parse_ibkr(text: str) -> ParsedImport:
    result = ParsedImport()
    headers: dict[str, list[str]] = {}
    withholding: dict[tuple[date, str, str], Decimal] = {}
    raw_dividends: list[tuple[date, str, Decimal, str]] = []
    skipped_categories: set[str] = set()
    for line_no, row in enumerate(csv.reader(io.StringIO(text)), start=1):
        if len(row) < 3:
            continue
        section, kind = row[0].strip().lstrip("﻿"), row[1].strip()
        if kind == "Header":
            headers[section] = [c.strip() for c in row]
            continue
        if kind != "Data" or section not in headers:
            continue
        rec = dict(zip(headers[section], row))
        try:
            if section == "Trades":
                if rec.get("DataDiscriminator", "Order") not in ("Order", "Trade"):
                    continue
                category = rec.get("Asset Category", "")
                if category not in IBKR_CATEGORIES:
                    skipped_categories.add(category)
                    continue
                qty = to_decimal(rec.get("Quantity"))
                if qty == 0:
                    continue
                result.trades.append(
                    ImportTrade(
                        day=to_date(rec.get("Date/Time")),
                        symbol=rec.get("Symbol", "").strip(),
                        qty=qty,
                        price=to_decimal(rec.get("T. Price")),
                        fee=abs(to_decimal(rec.get("Comm/Fee") or "0")),
                        currency=rec.get("Currency", "USD").strip(),
                        category=category,
                    )
                )
            elif section in ("Dividends", "Withholding Tax"):
                if (rec.get("Currency") or "").startswith("Total"):
                    continue
                m = _IBKR_SYMBOL.match(rec.get("Description", ""))
                if not m:
                    result.warnings.append(f"Строка {line_no}: не распознан тикер в «{rec.get('Description')}»")
                    continue
                day, symbol, ccy = to_date(rec.get("Date")), m.group(1).strip(), rec["Currency"].strip()
                amount = to_decimal(rec.get("Amount"))
                if section == "Dividends":
                    raw_dividends.append((day, symbol, amount, ccy))
                else:
                    key = (day, symbol, ccy)
                    withholding[key] = withholding.get(key, ZERO) - amount  # в файле налог отрицательный
        except ValueError as exc:
            result.errors.append(f"Строка {line_no} ({section}): {exc}")

    merged: dict[tuple[date, str, str], Decimal] = {}
    for day, symbol, amount, ccy in raw_dividends:
        merged[(day, symbol, ccy)] = merged.get((day, symbol, ccy), ZERO) + amount
    for (day, symbol, ccy), gross in sorted(merged.items()):
        if gross <= 0:
            result.warnings.append(f"{symbol} {day}: выплата {gross} ≤ 0 (сторно) — пропущена")
            continue
        tax = max(ZERO, withholding.pop((day, symbol, ccy), ZERO))
        result.dividends.append(ImportDividend(day, symbol, gross, min(tax, gross), ccy))
    for (day, symbol, _ccy), tax in withholding.items():
        if tax != 0:
            result.warnings.append(f"{symbol} {day}: налог {tax} без парной выплаты — проверьте вручную")
    if skipped_categories - {""}:
        result.warnings.append("Пропущены категории: " + ", ".join(sorted(skipped_categories - {""})))
    if not headers:
        result.errors.append("Файл не похож на Activity Statement IBKR (нет разделов с заголовками)")
    result.trades.sort(key=lambda t: t.day)
    return result


# ---------------- гибкий CSV ----------------

GENERIC_FIELDS = {
    "date_col": "Дата",
    "symbol_col": "Тикер / актив",
    "qty_col": "Количество",
    "price_col": "Цена за единицу",
    "fee_col": "Комиссия (необязательно)",
    "currency_col": "Валюта (необязательно)",
    "side_col": "Тип операции (необязательно)",
    "amount_col": "Сумма выплаты — для дивидендов (необязательно)",
    "tax_col": "Удержанный налог — для дивидендов (необязательно)",
}
DEFAULT_MAPPING = {
    "delimiter": "auto",
    "date_format": "auto",
    "decimal_comma": False,
    "default_currency": "USD",
    "buy_values": "buy, bought, קניה, קנייה, покупка",
    "sell_values": "sell, sold, מכירה, продажа",
    "dividend_values": "dividend, div, דיבידנד, дивиденд",
}


def sniff_delimiter(text: str) -> str:
    first = text.splitlines()[0] if text.strip() else ""
    counts = {d: first.count(d) for d in (",", ";", "\t", "|")}
    return max(counts, key=counts.get) if any(counts.values()) else ","


def csv_headers(text: str, delimiter: str = "auto") -> list[str]:
    delimiter = sniff_delimiter(text) if delimiter == "auto" else delimiter
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    return [h.strip() for h in next(reader, [])]


def _words(raw: str) -> set[str]:
    return {w.strip().lower() for w in raw.split(",") if w.strip()}


def parse_generic(text: str, mapping: dict) -> ParsedImport:
    cfg = {**DEFAULT_MAPPING, **mapping}
    result = ParsedImport()
    delimiter = sniff_delimiter(text) if cfg["delimiter"] == "auto" else cfg["delimiter"]
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    headers = [h.strip() for h in (reader.fieldnames or [])]
    reader.fieldnames = headers
    for key in ("date_col", "symbol_col"):
        if not cfg.get(key) or cfg[key] not in headers:
            result.errors.append(f"Не выбрана колонка: {GENERIC_FIELDS[key]}")
    if result.errors:
        return result
    buy, sell, div = _words(cfg["buy_values"]), _words(cfg["sell_values"]), _words(cfg["dividend_values"])
    comma = bool(cfg["decimal_comma"])

    def col(rec: dict, key: str) -> str | None:
        name = cfg.get(key)
        return rec.get(name) if name else None

    for line_no, rec in enumerate(reader, start=2):
        if not any((v or "").strip() for v in rec.values()):
            continue
        try:
            day = to_date(col(rec, "date_col"), cfg["date_format"])
            symbol = (col(rec, "symbol_col") or "").strip()
            if not symbol:
                raise ValueError("пустой тикер")
            currency = (col(rec, "currency_col") or cfg["default_currency"]).strip() or cfg["default_currency"]
            side = (col(rec, "side_col") or "").strip().lower()
            if side and side in div:
                gross = to_decimal(col(rec, "amount_col"), comma)
                tax_raw = col(rec, "tax_col")
                tax = abs(to_decimal(tax_raw, comma)) if (tax_raw or "").strip() else ZERO
                result.dividends.append(ImportDividend(day, symbol, abs(gross), tax, currency))
                continue
            if side and side not in buy and side not in sell:
                result.warnings.append(f"Строка {line_no}: операция «{side}» пропущена")
                continue
            qty = to_decimal(col(rec, "qty_col"), comma)
            price = to_decimal(col(rec, "price_col"), comma)
            fee_raw = col(rec, "fee_col")
            fee = abs(to_decimal(fee_raw, comma)) if (fee_raw or "").strip() else ZERO
            if side in sell:
                qty = -abs(qty)
            elif side in buy:
                qty = abs(qty)
            if qty == 0:
                continue
            result.trades.append(ImportTrade(day, symbol, qty, abs(price), fee, currency))
        except ValueError as exc:
            result.errors.append(f"Строка {line_no}: {exc}")
    result.trades.sort(key=lambda t: t.day)
    return result
