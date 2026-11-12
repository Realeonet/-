"""Банк Израиля: официальный представительный курс (שער יציג), ₪ за 1 единицу валюты.

Источник: открытый сервис статистики БИ (SDMX, формат CSV), серии RER_<ВАЛЮТА>_ILS.
В выходные и праздники курс не публикуется — расчёты берут последний опубликованный курс
на дату или раньше.
"""

from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal

from app.adapters.base import SourceError, http_get
from app.domain.money import D

BASE = "https://edge.boi.gov.il/FusionEdgeServer/sdmx/v2/data/dataflow/BOI.STATISTICS/EXR/1.0"
# Валюты, которые БИ публикует за несколько единиц.
UNIT: dict[str, Decimal] = {"JPY": Decimal(100), "LBP": Decimal(10)}
SUPPORTED = ("USD", "EUR", "GBP", "CHF", "CAD", "AUD", "JPY", "SEK", "NOK", "DKK", "ZAR")


class BoiSource:
    name = "boi"

    def rates(self, currencies: list[str], start: date, end: date) -> dict[str, dict[date, Decimal]]:
        wanted = sorted({c.upper() for c in currencies if c.upper() != "ILS"})
        unknown = [c for c in wanted if c not in SUPPORTED]
        if unknown:
            raise SourceError(f"Банк Израиля не публикует курс для: {', '.join(unknown)}")
        if not wanted:
            return {}
        series = ",".join(f"RER_{c}_ILS" for c in wanted)
        out: dict[str, dict[date, Decimal]] = {c: {} for c in wanted}
        try:
            resp = http_get(
                f"{BASE}/{series}",
                {"startperiod": start.isoformat(), "endperiod": end.isoformat(), "format": "csv"},
            )
        except SourceError as exc:
            if exc.status == 404:  # в диапазоне нет опубликованных курсов (выходные, праздники)
                return out
            raise
        reader = csv.DictReader(io.StringIO(resp.text))
        if not reader.fieldnames or "OBS_VALUE" not in reader.fieldnames:
            if not resp.text.strip():
                return out  # в диапазоне нет рабочих дней
            raise SourceError("Банк Израиля вернул неожиданный формат")
        for row in reader:
            ccy = (row.get("BASE_CURRENCY") or "").upper()
            if ccy not in out or not row.get("OBS_VALUE"):
                continue
            out[ccy][date.fromisoformat(row["TIME_PERIOD"])] = D(row["OBS_VALUE"]) / UNIT.get(ccy, Decimal(1))
        return out
