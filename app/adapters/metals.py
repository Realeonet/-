"""Спот-цены металлов: gold-api.com (без ключа). Запасной вариант и история — фьючерсы Yahoo.

Все цены приводятся к USD за 1 грамм чистого металла.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pandas as pd

from app.adapters.base import LiveQuote, SourceError, http_get
from app.domain.money import GRAMS_PER_LB, GRAMS_PER_TROY_OZ, D

# код → (название, граммов в единице котировки, фьючерс Yahoo)
METALS: dict[str, tuple[str, Decimal, str]] = {
    "XAU": ("Золото", GRAMS_PER_TROY_OZ, "GC=F"),
    "XAG": ("Серебро", GRAMS_PER_TROY_OZ, "SI=F"),
    "XPT": ("Платина", GRAMS_PER_TROY_OZ, "PL=F"),
    "XPD": ("Палладий", GRAMS_PER_TROY_OZ, "PA=F"),
    "HG": ("Медь", GRAMS_PER_LB, "HG=F"),
}


def per_gram(code: str, unit_price: Decimal) -> Decimal:
    if code not in METALS:
        raise SourceError(f"Неизвестный металл: {code}")
    return unit_price / METALS[code][1]


class MetalsSource:
    name = "metal"

    def __init__(self, yahoo=None) -> None:  # noqa: ANN001
        self._yahoo = yahoo

    def quote(self, code: str) -> LiveQuote:
        """Спот, USD за грамм. Если спот недоступен — ближайший фьючерс (помечается в source)."""
        code = code.upper()
        if code not in METALS:
            raise SourceError(f"Неизвестный металл: {code}")
        try:
            body = http_get(f"https://api.gold-api.com/price/{code}").json()
            price = D(body["price"])
            stamp = body.get("updatedAt")
            as_of = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ") if stamp else datetime.utcnow()
            return LiveQuote(per_gram(code, price), "USD", as_of, "спот gold-api.com")
        except (SourceError, KeyError, ValueError) as spot_error:
            if self._yahoo is None:
                raise SourceError(f"Спот {code} недоступен: {spot_error}") from spot_error
            fut = self._yahoo.quote(METALS[code][2])
            return LiveQuote(per_gram(code, fut.price), "USD", fut.as_of, f"фьючерс {METALS[code][2]} (не спот)")

    def history(self, code: str, start: date, end: date) -> pd.Series:
        """История по фьючерсу (не спот), USD за грамм."""
        code = code.upper()
        if code not in METALS or self._yahoo is None:
            raise SourceError(f"Нет истории для металла {code}")
        series = self._yahoo.history(METALS[code][2], start, end)
        return series / float(METALS[code][1])
