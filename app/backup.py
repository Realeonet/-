"""Зашифрованный бэкап: весь набор таблиц → JSON → Fernet (ключ из пароля через scrypt).

Формат файла: строка «FINBK1», base64(соль), затем токен Fernet.
Восстановление ЗАМЕНЯЕТ все данные в базе.
"""

from __future__ import annotations

import base64
import json
import os
from datetime import date, datetime
from decimal import Decimal
from hashlib import scrypt
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Date
from sqlalchemy.orm import Session

from app.db import BalancePoint, Dec, OfficialMonthly, PensionAccount, Setting

MAGIC = b"FINBK1"
FORMAT_VERSION = 1
TABLES = (Setting, PensionAccount, BalancePoint, OfficialMonthly)  # порядок вставки (FK)


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    raw = scrypt(passphrase.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return base64.urlsafe_b64encode(raw)


def _to_json(value):  # noqa: ANN001, ANN201
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


def _from_json(column, value):  # noqa: ANN001, ANN201
    if value is None:
        return None
    if isinstance(column.type, Dec):
        return Decimal(value)
    if isinstance(column.type, Date):
        return date.fromisoformat(value)
    return value


def dump_payload(session: Session) -> dict:
    tables: dict[str, list[dict]] = {}
    for model in TABLES:
        cols = model.__table__.columns
        rows = session.query(model).order_by(*model.__table__.primary_key.columns).all()
        tables[model.__tablename__] = [
            {c.name: _to_json(getattr(obj, c.name)) for c in cols} for obj in rows
        ]
    return {"version": FORMAT_VERSION, "tables": tables}


def load_payload(session: Session, payload: dict) -> None:
    if payload.get("version") != FORMAT_VERSION:
        raise ValueError("Неизвестная версия формата бэкапа")
    for model in reversed(TABLES):
        session.query(model).delete()
    for model in TABLES:
        cols = {c.name: c for c in model.__table__.columns}
        for row in payload["tables"].get(model.__tablename__, []):
            kwargs = {name: _from_json(cols[name], row.get(name)) for name in cols}
            session.add(model(**kwargs))
    session.flush()


def encrypt_session(session: Session, passphrase: str) -> bytes:
    if not passphrase:
        raise ValueError("Нужна парольная фраза для бэкапа")
    payload = dump_payload(session)
    salt = os.urandom(16)
    token = Fernet(_derive_key(passphrase, salt)).encrypt(
        json.dumps(payload, ensure_ascii=False).encode("utf-8")
    )
    return MAGIC + b"\n" + base64.b64encode(salt) + b"\n" + token


def decrypt_blob(blob: bytes, passphrase: str) -> dict:
    try:
        magic, salt_line, token = blob.split(b"\n", 2)
    except ValueError as exc:
        raise ValueError("Файл бэкапа повреждён или имеет неизвестный формат") from exc
    if magic != MAGIC:
        raise ValueError("Это не файл бэкапа этого приложения")
    try:
        plain = Fernet(_derive_key(passphrase, base64.b64decode(salt_line))).decrypt(token)
    except InvalidToken as exc:
        raise ValueError("Неверная парольная фраза или файл повреждён") from exc
    return json.loads(plain.decode("utf-8"))


def restore_blob(session: Session, blob: bytes, passphrase: str) -> None:
    load_payload(session, decrypt_blob(blob, passphrase))


def write_backup_file(session: Session, passphrase: str, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    path = backup_dir / f"finance-backup-{datetime.now():%Y%m%d-%H%M%S}.fbk"
    path.write_bytes(encrypt_session(session, passphrase))
    return path
