"""Общие типы адаптеров. Любой источник можно заменить, реализовав те же методы.

Приватность: адаптеры отправляют наружу только тикеры, идентификаторы монет/фондов и даты.
Количества, суммы, счета и адреса кошельков наружу не передаются никогда.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import requests

TIMEOUT = 25
USER_AGENT = "Mozilla/5.0 (personal-portfolio-app)"


class SourceError(Exception):
    """Источник недоступен или вернул неожиданный ответ."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class LiveQuote:
    price: Decimal
    currency: str
    as_of: datetime
    source: str


def http_get(url: str, params: dict | None = None, headers: dict | None = None) -> requests.Response:
    try:
        resp = requests.get(
            url, params=params, headers={"User-Agent": USER_AGENT, **(headers or {})}, timeout=TIMEOUT
        )
    except requests.RequestException as exc:
        raise SourceError(f"Нет связи с {url.split('/')[2]}: {exc.__class__.__name__}") from exc
    if resp.status_code != 200:
        raise SourceError(f"{url.split('/')[2]} ответил кодом {resp.status_code}", status=resp.status_code)
    return resp
