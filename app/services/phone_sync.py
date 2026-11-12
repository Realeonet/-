"""Обмен данными с приложением на телефоне.

Компьютер → телефон: зашифрованный файл со снимком портфеля (места, активы с ценами, лоты, курсы,
пенсия, настройки правил). Телефон → компьютер: зашифрованный файл с покупками, записанными на ходу.

Шифрование: AES-256-GCM, ключ — PBKDF2-HMAC-SHA256 из парольной фразы. Эти алгоритмы есть в браузере
телефона (WebCrypto), поэтому телефон расшифровывает файл сам, без сторонних сервисов.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.orm import Session, joinedload

from app.db import Asset, Custodian, ImportedRow, Lot, Owner, get_setting
from app.domain import minkin
from app.domain.money import ZERO, D
from app.domain.returns import trailing_yield
from app.services import ledger
from app.services.dividends import upcoming_ex_dates
from app.services.market import MarketData

FORMAT = "fin-phone"
VERSION = 1
ITERATIONS = 310_000
MIN_PASSPHRASE = 8
PHONE_FINGERPRINT_PREFIX = "phone:"


# ---------- шифрование ----------

def _key(passphrase: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, iterations, dklen=32)


def encrypt_json(payload: dict, passphrase: str) -> bytes:
    if len(passphrase) < MIN_PASSPHRASE:
        raise ValueError(f"Парольная фраза должна быть не короче {MIN_PASSPHRASE} символов")
    salt, iv = os.urandom(16), os.urandom(12)
    plain = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    sealed = AESGCM(_key(passphrase, salt, ITERATIONS)).encrypt(iv, plain, None)
    envelope = {
        "format": FORMAT,
        "v": VERSION,
        "iter": ITERATIONS,
        "salt": base64.b64encode(salt).decode(),
        "iv": base64.b64encode(iv).decode(),
        "data": base64.b64encode(sealed).decode(),
    }
    return json.dumps(envelope).encode("utf-8")


def decrypt_json(blob: bytes, passphrase: str) -> dict:
    try:
        envelope = json.loads(blob.decode("utf-8"))
        if envelope.get("format") != FORMAT:
            raise ValueError
        salt = base64.b64decode(envelope["salt"])
        iv = base64.b64decode(envelope["iv"])
        sealed = base64.b64decode(envelope["data"])
        iterations = int(envelope["iter"])
    except (ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
        raise ValueError("Это не файл обмена с телефоном или он повреждён") from exc
    if not 10_000 <= iterations <= 5_000_000:
        raise ValueError("Файл повреждён: недопустимые параметры шифрования")
    try:
        plain = AESGCM(_key(passphrase, salt, iterations)).decrypt(iv, sealed, None)
    except InvalidTag as exc:
        raise ValueError("Неверная парольная фраза или файл повреждён") from exc
    return json.loads(plain.decode("utf-8"))


# ---------- компьютер → телефон ----------

def _num(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def build_snapshot(
    s: Session,
    market: MarketData,
    today: date,
    settings: dict,
    pension: list[dict],
    dividends: dict,
) -> dict:
    """Снимок портфеля для телефона. settings, pension и dividends готовит вызывающий код."""
    owners = s.query(Owner).order_by(Owner.id).all()
    custodians = s.query(Custodian).filter(Custodian.archived.is_(False)).order_by(Custodian.name).all()
    lots = [lot for lot in s.query(Lot).options(joinedload(Lot.asset)).all() if lot.qty_remaining > 0]
    asset_ids = {lot.asset_id for lot in lots}
    assets = s.query(Asset).order_by(Asset.symbol).all()

    window = int(settings.get("sma_window", minkin.DEFAULT_SMA_WINDOW))
    asset_rows = []
    currencies = {"USD"}
    for a in assets:
        info = market.price_info(a)
        currency = info.currency if info else a.currency
        currencies.add(currency)
        row = {
            "id": a.id, "symbol": a.symbol, "name": a.name, "cls": a.asset_class, "currency": currency,
            "priceSource": a.price_source, "sourceId": a.source_id, "usSitus": a.us_situs,
            "adjPct": _num(a.valuation_adj_pct or ZERO), "distType": a.dist_type,
            "price": _num(info.price) if info else None,
            "priceAsOf": info.as_of.isoformat() if info else None,
            "priceSrc": info.source if info else None,
            "held": a.id in asset_ids,
        }
        if a.asset_class in ("stock", "etf", "crypto", "metal"):
            hist = market.asset_history(a, today - timedelta(days=window * 3))
            sig = minkin.sma_signal(list(hist.values), window) if len(hist) else None
            if sig:
                row["sma"] = {"above": sig.above, "distance": _num(sig.distance), "window": window,
                              "asOf": hist.index[-1].date().isoformat()}
        if a.asset_class in ("stock", "etf") and a.dist_type != "acc":
            ex = upcoming_ex_dates(a, market, today, 90)
            if ex:
                row["nextEx"] = ex[0].day.isoformat()
                row["nextExEstimated"] = ex[0].estimated
            if info:
                y = trailing_yield(market.dividends(a), info.price, today)
                if (y is None or y == 0) and a.manual_yield_pct:
                    y = a.manual_yield_pct / 100
                if y:
                    row["yieldPct"] = _num(y * 100)
        asset_rows.append(row)

    for lot in lots:
        currencies.add(lot.currency)
    rates, rate_days = {}, []
    for code in sorted(currencies):
        rate = market.fx(code, today)
        if rate is not None:
            rates[code] = _num(rate)
            day = market.fx_date(code, today)
            if day:
                rate_days.append(day)

    known = [
        fp[len(PHONE_FINGERPRINT_PREFIX):]
        for (fp,) in s.query(ImportedRow.fingerprint).filter(ImportedRow.fingerprint.like(PHONE_FINGERPRINT_PREFIX + "%"))
    ]
    return {
        "kind": "snapshot",
        "exportedAt": datetime.now().isoformat(timespec="seconds"),
        "settings": settings,
        "owners": [{"id": o.id, "name": o.name} for o in owners],
        "custodians": [
            {"id": c.id, "name": c.name, "kind": c.kind, "ownerId": c.owner_id, "selfCustody": c.is_self_custody,
             "withholds": c.withholds_il_tax, "location": c.location_note or "", "currency": c.currency}
            for c in custodians
        ],
        "assets": asset_rows,
        "lots": [
            {"id": lot.id, "assetId": lot.asset_id, "custodianId": lot.custodian_id,
             "buyDate": lot.buy_date.isoformat(), "qty": _num(lot.qty_remaining),
             "price": _num(lot.price), "fees": _num(lot.fees * lot.qty_remaining / lot.qty),
             "currency": lot.currency, "fxCcyIls": _num(lot.fx_ccy_ils), "fxUsdIls": _num(lot.fx_usd_ils)}
            for lot in lots
        ],
        "fx": {"asOf": max(rate_days).isoformat() if rate_days else None, "rates": rates,
               "source": "Банк Израиля (официальный курс)"},
        "pension": pension,
        "dividends": dividends,
        "phoneUids": known,
    }


# ---------- телефон → компьютер ----------

@dataclass
class PhoneImportReport:
    lots: int = 0
    skipped: int = 0
    created_assets: list[str] = field(default_factory=list)
    created_custodians: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


ASSET_CLASSES = {"stock", "etf", "crypto", "metal", "cash", "other"}
SOURCES = {"yfinance", "coingecko", "metal", "cash", "manual"}
KINDS = {"broker", "bank", "exchange", "self_custody", "physical", "institution", "other"}


def _find_asset(s: Session, spec: dict) -> Asset | None:
    symbol = str(spec.get("symbol", "")).strip().upper()
    source, source_id = spec.get("priceSource"), str(spec.get("sourceId", "")).strip()
    if source in ("coingecko", "metal", "yfinance") and source_id:
        found = s.query(Asset).filter(Asset.price_source == source, Asset.source_id == source_id).first()
        if found:
            return found
    return s.query(Asset).filter(Asset.symbol == symbol).first() if symbol else None


def apply_phone_file(s: Session, market: MarketData, payload: dict, today: date | None = None) -> PhoneImportReport:
    """Покупки, записанные на телефоне, становятся лотами. Курс берётся официальный на дату покупки
    (а не рыночный с телефона). Повторный импорт того же файла ничего не задваивает."""
    if payload.get("kind") != "phone-lots":
        raise ValueError("В файле нет покупок с телефона. Нужен файл, созданный кнопкой «Отправить на компьютер».")
    report = PhoneImportReport()
    seen = {fp for (fp,) in s.query(ImportedRow.fingerprint).filter(ImportedRow.custodian_id == 0)}
    owners = {o.name: o for o in s.query(Owner).all()}
    for item in payload.get("lots", []):
        uid = str(item.get("uid", "")).strip()
        label = f"{item.get('buyDate')} {item.get('asset', {}).get('symbol', '?')}"
        if not uid:
            report.errors.append(f"{label}: запись без идентификатора пропущена")
            continue
        fingerprint = PHONE_FINGERPRINT_PREFIX + uid
        if fingerprint in seen:
            report.skipped += 1
            continue
        try:
            with s.begin_nested():
                a_spec, c_spec = item["asset"], item["custodian"]
                asset = _find_asset(s, a_spec)
                if asset is None:
                    cls, source = a_spec.get("cls"), a_spec.get("priceSource")
                    if cls not in ASSET_CLASSES or source not in SOURCES:
                        raise ValueError("неизвестный тип актива")
                    asset = Asset(
                        symbol=str(a_spec["symbol"]).strip().upper()[:32], name=str(a_spec.get("name", ""))[:255],
                        asset_class=cls, currency=str(a_spec.get("currency") or "USD")[:8], price_source=source,
                        source_id=str(a_spec.get("sourceId", ""))[:64],
                        history_ticker=f"{str(a_spec['symbol']).upper()}-USD" if cls == "crypto" else "",
                        domicile="",
                    )
                    s.add(asset)
                    s.flush()
                    report.created_assets.append(asset.symbol)
                name = str(c_spec.get("name", "")).strip()
                owner = owners.get(str(c_spec.get("owner", ""))) or next(iter(owners.values()))
                custodian = (
                    s.query(Custodian).filter(Custodian.name == name, Custodian.owner_id == owner.id).first()
                    or s.query(Custodian).filter(Custodian.name == name).first()
                )
                if custodian is None:
                    if not name:
                        raise ValueError("не указано место хранения")
                    kind = c_spec.get("kind") if c_spec.get("kind") in KINDS else "other"
                    custodian = Custodian(name=name[:128], kind=kind, owner_id=owner.id)
                    s.add(custodian)
                    s.flush()
                    report.created_custodians.append(custodian.name)
                ledger.add_lot(
                    s, market, asset, custodian, date.fromisoformat(item["buyDate"]), D(item["qty"]), D(item["price"]),
                    fees=D(item.get("fees") or "0"), currency=str(item.get("currency") or asset.currency),
                    note=(str(item.get("note") or "").strip() or "записано на телефоне")[:500], today=today,
                )
                s.add(ImportedRow(custodian_id=0, fingerprint=fingerprint))
                report.lots += 1
        except (KeyError, ValueError, ArithmeticError) as exc:
            report.errors.append(f"{label}: {exc}")
    s.flush()
    return report


def phone_settings(s: Session, defaults: dict[str, str]) -> dict:
    """Настройки правил, нужные телефону."""
    def get(key: str) -> str:
        return get_setting(s, key, defaults.get(key, "")) or ""

    return {
        "taxRatePct": str(D(get("tax_rate_pct") or "25") + D(get("surtax_pct") or "0")),
        "taxMethod": get("tax_method") or "nominal",
        "estateLimitUsd": get("estate_limit_usd"), "estateWarnUsd": get("estate_warn_usd"),
        "concentrationPct": get("concentration_pct"),
        "blacklist": [b.strip().upper() for b in get("blacklist").split(",") if b.strip()],
        "buyWindowAfter": int(D(get("buy_window_after") or "30")),
        "buyWindowUntil": int(D(get("buy_window_until") or "60")),
        "xdayDays": int(D(get("xday_days") or "2")),
        "sma_window": int(D(get("sma_window") or "150")),
        "dca": {"asset": get("dca_asset"), "full": get("dca_full"), "reduced": get("dca_reduced"),
                "threshold": get("dca_threshold")},
    }
