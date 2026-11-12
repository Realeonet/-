"""Применение разобранного CSV к базе: покупки → лоты, продажи → FIFO, дивиденды → выплаты.

Повторный импорт того же файла безопасен: уже загруженные строки пропускаются по отпечатку.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.db import Asset, Custodian, ImportedRow
from app.importers.trades_csv import ParsedImport
from app.services import ledger
from app.services.market import MarketData


@dataclass
class ImportReport:
    lots: int = 0
    sales: int = 0
    dividends: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


def asset_index(s: Session) -> dict[str, Asset]:
    """Поиск актива по символу или по тикеру источника (без учёта регистра)."""
    index: dict[str, Asset] = {}
    for a in s.query(Asset).all():
        index[a.symbol.upper()] = a
        if a.source_id:
            index.setdefault(a.source_id.upper(), a)
            index.setdefault(a.source_id.upper().split(".")[0], a)
    return index


def unknown_symbols(s: Session, parsed: ParsedImport) -> list[str]:
    index = asset_index(s)
    symbols = {t.symbol for t in parsed.trades} | {d.symbol for d in parsed.dividends}
    return sorted(sym for sym in symbols if sym.upper() not in index)


def apply_import(s: Session, market: MarketData, custodian: Custodian, parsed: ParsedImport) -> ImportReport:
    """Строки с ошибкой (нет актива, нет курса, не хватает количества) пропускаются и попадают в отчёт;
    остальные применяются. Вызывающий код делает commit."""
    report = ImportReport()
    index = asset_index(s)
    seen = {fp for (fp,) in s.query(ImportedRow.fingerprint).filter(ImportedRow.custodian_id == custodian.id)}
    counters: dict[str, int] = {}

    def next_fp(item) -> str:  # noqa: ANN001
        base = item.fingerprint(0)
        seq = counters.get(base, 0)
        counters[base] = seq + 1
        return item.fingerprint(seq)

    for t in parsed.trades:
        fp = next_fp(t)
        if fp in seen:
            report.skipped += 1
            continue
        asset = index.get(t.symbol.upper())
        if asset is None:
            report.errors.append(f"{t.day} {t.symbol}: актив не найден — сначала создайте его")
            continue
        try:
            with s.begin_nested():
                if t.qty > 0:
                    ledger.add_lot(s, market, asset, custodian, t.day, t.qty, t.price, fees=t.fee,
                                   currency=t.currency, kind="import")
                    report.lots += 1
                else:
                    ledger.sell(s, market, asset, custodian.id, t.day, t.price, qty=-t.qty, fee=t.fee,
                                currency=t.currency, note="импорт CSV")
                    report.sales += 1
                s.add(ImportedRow(custodian_id=custodian.id, fingerprint=fp))
        except ledger.LedgerError as exc:
            report.errors.append(f"{t.day} {t.symbol} {t.qty}: {exc}")

    for d in parsed.dividends:
        fp = next_fp(d)
        if fp in seen:
            report.skipped += 1
            continue
        asset = index.get(d.symbol.upper())
        if asset is None:
            report.errors.append(f"{d.day} {d.symbol}: актив не найден — сначала создайте его")
            continue
        try:
            with s.begin_nested():
                ledger.add_dividend(s, market, asset, custodian, d.day, d.gross, foreign_tax=d.foreign_tax,
                                    currency=d.currency, note="импорт CSV")
                s.add(ImportedRow(custodian_id=custodian.id, fingerprint=fp))
                report.dividends += 1
        except ledger.LedgerError as exc:
            report.errors.append(f"{d.day} {d.symbol} дивиденд: {exc}")
    s.flush()
    return report
