"""Числовые утилиты: Decimal, валюты с «младшими» единицами, единицы веса металлов."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

ZERO = Decimal("0")
ONE = Decimal("1")
HUNDRED = Decimal("100")

GRAMS_PER_TROY_OZ = Decimal("31.1034768")
GRAMS_PER_LB = Decimal("453.59237")
GRAMS_PER_KG = Decimal("1000")

WEIGHT_UNITS: dict[str, Decimal] = {
    "g": ONE,
    "oz_t": GRAMS_PER_TROY_OZ,
    "kg": GRAMS_PER_KG,
}

# Котировки в «младших» единицах: пенсы Лондона и агороты Тель-Авива.
_MINOR: dict[str, tuple[str, Decimal]] = {
    "GBp": ("GBP", Decimal("0.01")),
    "GBX": ("GBP", Decimal("0.01")),
    "ILA": ("ILS", Decimal("0.01")),
    "ILa": ("ILS", Decimal("0.01")),
    "ZAc": ("ZAR", Decimal("0.01")),
}


def D(value) -> Decimal:  # noqa: ANN001, N802
    """Надёжное преобразование в Decimal (float идёт через repr, без двоичного мусора)."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(repr(value))
    try:
        return Decimal(str(value).strip().replace(",", "").replace(" ", ""))
    except InvalidOperation as exc:
        raise ValueError(f"Некорректное число: {value!r}") from exc


def from_market_float(value: float) -> Decimal:
    """Цена из рыночного источника (часто float32): 7 значащих цифр, без двоичного хвоста."""
    return Decimal(f"{float(value):.7g}")


def major_currency(code: str) -> tuple[str, Decimal]:
    """'GBp' → ('GBP', 0.01); 'USD' → ('USD', 1). Регистр важен только для пенсов/агорот."""
    if code in _MINOR:
        return _MINOR[code]
    return code.upper(), ONE


def fine_grams(weight: Decimal, unit: str, purity: Decimal) -> Decimal:
    """Чистый металл в граммах: вес × единица × проба (проба 0..1, напр. 0.999)."""
    if unit not in WEIGHT_UNITS:
        raise ValueError(f"Неизвестная единица веса: {unit}")
    if not (ZERO < purity <= ONE):
        raise ValueError("Проба должна быть в диапазоне (0; 1], например 0.999")
    if weight <= 0:
        raise ValueError("Вес должен быть больше нуля")
    return weight * WEIGHT_UNITS[unit] * purity


def q2(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def fmt_money(value: Decimal | None, symbol: str = "", digits: int = 2) -> str:
    """Округление только для показа: 1 234 567.89 ₪"""
    if value is None:
        return "—"
    quant = Decimal(1).scaleb(-digits)
    text = f"{value.quantize(quant, rounding=ROUND_HALF_UP):,.{digits}f}".replace(",", " ")
    return f"{text} {symbol}".strip()


def fmt_pct(value: Decimal | None, digits: int = 2, signed: bool = False) -> str:
    """value — доля (0.05 → 5.00%)."""
    if value is None:
        return "—"
    quant = Decimal(1).scaleb(-digits)
    pct = (value * HUNDRED).quantize(quant, rounding=ROUND_HALF_UP)
    return f"{pct:+.{digits}f}%" if signed else f"{pct:.{digits}f}%"


def fmt_qty(value: Decimal | None) -> str:
    if value is None:
        return "—"
    text = format(value.normalize(), "f") if value != 0 else "0"
    if "." in text:
        whole, frac = text.split(".")
        return f"{int(whole):,}".replace(",", " ") + "." + frac[:8]
    return f"{int(text):,}".replace(",", " ")
