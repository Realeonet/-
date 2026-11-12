"""CoinGecko, публичный API без ключа: любая монета из его списка.

Ограничение бесплатного API: история не длиннее 365 дней. Для длинной истории (бэктесты)
у актива задаётся тикер Yahoo (например BTC-USD).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from app.adapters.base import LiveQuote, SourceError, http_get
from app.domain.money import D

BASE = "https://api.coingecko.com/api/v3"
MAX_FREE_DAYS = 365


class CoinGeckoSource:
    name = "coingecko"

    def search(self, query: str) -> list[dict]:
        """Поиск монеты: [{'id': 'cosmos', 'symbol': 'ATOM', 'name': 'Cosmos Hub', 'rank': 84}]"""
        data = http_get(f"{BASE}/search", {"query": query}).json()
        return [
            {"id": c["id"], "symbol": (c.get("symbol") or "").upper(), "name": c.get("name") or "",
             "rank": c.get("market_cap_rank")}
            for c in data.get("coins", [])
        ]

    def quotes(self, coin_ids: list[str]) -> dict[str, LiveQuote]:
        if not coin_ids:
            return {}
        data = http_get(
            f"{BASE}/simple/price",
            {"ids": ",".join(sorted(set(coin_ids))), "vs_currencies": "usd", "include_last_updated_at": "true"},
        ).json()
        out = {}
        for coin_id, body in data.items():
            if "usd" not in body:
                continue
            ts = body.get("last_updated_at")
            as_of = datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None) if ts else datetime.utcnow()
            out[coin_id] = LiveQuote(price=D(body["usd"]), currency="USD", as_of=as_of, source="CoinGecko")
        return out

    def history(self, coin_id: str, days: int = MAX_FREE_DAYS) -> pd.Series:
        days = max(1, min(days, MAX_FREE_DAYS))
        data = http_get(
            f"{BASE}/coins/{coin_id}/market_chart", {"vs_currency": "usd", "days": days, "interval": "daily"}
        ).json()
        prices = data.get("prices") or []
        if not prices:
            raise SourceError(f"CoinGecko: нет истории для {coin_id}")
        idx = pd.to_datetime([datetime.fromtimestamp(p[0] / 1000, tz=timezone.utc).date() for p in prices])
        series = pd.Series([float(p[1]) for p in prices], index=idx)
        return series.groupby(level=0).last().sort_index()
