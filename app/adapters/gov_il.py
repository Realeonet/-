"""Официальные данные Минфина (רשות שוק ההון) с data.gov.il: גמל-נט, פנסיה-נט, ביטוח-נט.

Даёт по каждому треку (מסלול) за каждый месяц: доходность, активы, экспозицию к акциям,
к зарубежным активам и к валюте. Запрос содержит только номер фонда — никаких личных данных.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.adapters.base import SourceError, http_get
from app.domain.money import HUNDRED, D

API = "https://data.gov.il/api/3/action/datastore_search"

# источник → ресурсы от новых к старым
RESOURCES: dict[str, tuple[str, ...]] = {
    "gemel": (
        "a30dcbea-a1d2-482c-ae29-8f781f5025fb",  # 2024 — сегодня
        "2016d770-f094-4a2e-983e-797c26479720",  # 2023
        "91c849ed-ddc4-472b-bd09-0f5486cea35c",  # 1999–2022
    ),
    "pensia": (
        "6d47d6b5-cb08-488b-b333-f1e717b1e1bd",
        "4694d5a7-5284-4f3d-a2cb-5887f43fb55e",
        "a66926f3-e396-4984-a4db-75486751c2f7",
    ),
    "bituach": (
        "c6c62cc7-fe02-4b18-8f3e-813abfbb4647",
        "672090ba-7893-4496-a07c-dc7e822cbf18",
        "584e6b69-174f-46c9-b8db-03925b4c68c6",
    ),
}
SOURCE_LABELS = {
    "gemel": "גמל-נט (קופות גמל, קרנות השתלמות)",
    "pensia": "פנסיה-נט (קרנות פנסיה)",
    "bituach": "ביטוח-נט (ביטוח מנהלים)",
}
# тип продукта → где искать
SOURCE_FOR_PRODUCT = {"pension": "pensia", "hishtalmut": "gemel", "gemel": "gemel", "menahalim": "bituach"}


@dataclass(frozen=True)
class FundRef:
    fund_id: int
    name: str
    company: str
    classification: str


@dataclass(frozen=True)
class OfficialRecord:
    period: str  # YYYY-MM
    return_pct: Decimal
    total_assets_m: Decimal | None  # млн ₪
    stock_pct: Decimal | None
    foreign_pct: Decimal | None
    fx_pct: Decimal | None
    management_fee_pct: Decimal | None


def _pct(part, total) -> Decimal | None:  # noqa: ANN001
    if part is None or total in (None, 0):
        return None
    return D(part) / D(total) * HUNDRED


def parse_record(rec: dict) -> OfficialRecord | None:
    period = rec.get("REPORT_PERIOD")
    if period is None or rec.get("MONTHLY_YIELD") is None:
        return None
    text = str(int(period))
    total = rec.get("TOTAL_ASSETS")
    fee = rec.get("AVG_ANNUAL_MANAGEMENT_FEE")
    return OfficialRecord(
        period=f"{text[:4]}-{text[4:6]}",
        return_pct=D(rec["MONTHLY_YIELD"]),
        total_assets_m=D(total) if total is not None else None,
        stock_pct=_pct(rec.get("STOCK_MARKET_EXPOSURE"), total),
        foreign_pct=_pct(rec.get("FOREIGN_EXPOSURE"), total),
        fx_pct=_pct(rec.get("FOREIGN_CURRENCY_EXPOSURE"), total),
        management_fee_pct=D(fee) if fee is not None else None,
    )


class GovIlSource:
    name = "data.gov.il"

    def _search(self, resource: str, params: dict) -> list[dict]:
        body = http_get(API, {"resource_id": resource, **params}).json()
        if not body.get("success"):
            raise SourceError("data.gov.il вернул ошибку")
        return body["result"]["records"]

    def search_funds(self, source: str, query: str, limit: int = 300) -> list[FundRef]:
        """Поиск трека по названию (иврит) или по номеру фонда (מספר אוצר)."""
        if source not in RESOURCES:
            raise SourceError(f"Неизвестный источник: {source}")
        query = query.strip()
        params: dict = {"limit": limit, "sort": "REPORT_PERIOD desc"}
        if query.isdigit():
            params["filters"] = f'{{"FUND_ID": {int(query)}}}'
            params["limit"] = 1
        elif query:
            params["q"] = query
        seen: dict[int, FundRef] = {}
        for rec in self._search(RESOURCES[source][0], params):
            fund_id = int(rec["FUND_ID"])
            if fund_id not in seen:
                seen[fund_id] = FundRef(
                    fund_id=fund_id,
                    name=rec.get("FUND_NAME") or "",
                    company=rec.get("MANAGING_CORPORATION") or rec.get("PARENT_COMPANY_NAME") or "",
                    classification=rec.get("FUND_CLASSIFICATION") or "",
                )
        return sorted(seen.values(), key=lambda f: f.name)

    def monthly(self, source: str, fund_id: int, from_period: str = "2024-01") -> list[OfficialRecord]:
        """Месячные записи трека начиная с from_period (YYYY-MM), по возрастанию."""
        if source not in RESOURCES:
            raise SourceError(f"Неизвестный источник: {source}")
        year = int(from_period[:4])
        resources = [RESOURCES[source][0]]
        if year <= 2023:
            resources.append(RESOURCES[source][1])
        if year <= 2022:
            resources.append(RESOURCES[source][2])
        out: dict[str, OfficialRecord] = {}
        for resource in resources:
            records = self._search(
                resource, {"filters": f'{{"FUND_ID": {int(fund_id)}}}', "limit": 500, "sort": "REPORT_PERIOD asc"}
            )
            for rec in records:
                parsed = parse_record(rec)
                if parsed and parsed.period >= from_period:
                    out[parsed.period] = parsed
        return [out[k] for k in sorted(out)]
