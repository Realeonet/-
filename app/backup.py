"""Зашифрованный бэкап: личные таблицы → JSON → Fernet (ключ из парольной фразы через scrypt).

Формат файла: строка «FINBK2», base64(соль), затем токен Fernet.
В бэкап входят только личные данные; кэш котировок и курсов не входит (он скачивается заново).
Восстановление ЗАМЕНЯЕТ личные данные в базе, пароль входа остаётся текущим.
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
from sqlalchemy import Boolean, Date, DateTime
from sqlalchemy.orm import Session

from app.db import Base, Dec, DivPoint, FxPoint, PricePoint, Quote, Setting

MAGIC = b"FINBK2"
FORMAT_VERSION = 2
CACHE_TABLES = {PricePoint.__tablename__, Quote.__tablename__, DivPoint.__tablename__, FxPoint.__tablename__}
KEEP_SETTINGS = ("password_hash",)  # не переносятся из бэкапа: остаётся текущий пароль входа
AUTO_PREFIX = "auto-"
AUTO_KEEP = 30


def _tables():
    return [t for t in Base.metadata.sorted_tables if t.name not in CACHE_TABLES]


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    raw = scrypt(passphrase.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return base64.urlsafe_b64encode(raw)


def _to_json(value):  # noqa: ANN001, ANN202
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _from_json(column, value):  # noqa: ANN001, ANN202
    if value is None:
        return None
    if isinstance(column.type, Dec):
        return Decimal(value)
    if isinstance(column.type, DateTime):
        return datetime.fromisoformat(value)
    if isinstance(column.type, Date):
        return date.fromisoformat(value)
    if isinstance(column.type, Boolean):
        return bool(value)
    return value


def dump_payload(session: Session) -> dict:
    tables: dict[str, list[dict]] = {}
    for table in _tables():
        rows = session.execute(table.select()).mappings().all()
        out = []
        for row in rows:
            if table.name == Setting.__tablename__ and (
                row["key"] in KEEP_SETTINGS or row["key"].startswith("refreshed:")
            ):
                continue
            out.append({c.name: _to_json(row[c.name]) for c in table.columns})
        tables[table.name] = out
    return {"version": FORMAT_VERSION, "created": datetime.now().isoformat(timespec="seconds"), "tables": tables}


def load_payload(session: Session, payload: dict) -> None:
    if payload.get("version") != FORMAT_VERSION:
        raise ValueError("Неизвестная версия формата бэкапа")
    tables = _tables()
    for table in reversed(tables):
        if table.name == Setting.__tablename__:
            session.execute(table.delete().where(table.c.key.notin_(KEEP_SETTINGS)))
        else:
            session.execute(table.delete())
    for table in tables:
        rows = payload["tables"].get(table.name, [])
        cols = {c.name: c for c in table.columns}
        prepared = [
            {name: _from_json(cols[name], row.get(name)) for name in cols if name in row}
            for row in rows
            if not (table.name == Setting.__tablename__ and row.get("key") in KEEP_SETTINGS)
        ]
        if prepared:
            session.execute(table.insert(), prepared)
    session.flush()


def encrypt_session(session: Session, passphrase: str) -> bytes:
    if not passphrase:
        raise ValueError("Нужна парольная фраза для бэкапа")
    payload = dump_payload(session)
    salt = os.urandom(16)
    token = Fernet(_derive_key(passphrase, salt)).encrypt(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    return MAGIC + b"\n" + base64.b64encode(salt) + b"\n" + token


def decrypt_blob(blob: bytes, passphrase: str) -> dict:
    try:
        magic, salt_line, token = blob.split(b"\n", 2)
    except ValueError as exc:
        raise ValueError("Файл бэкапа повреждён или имеет неизвестный формат") from exc
    if magic != MAGIC:
        raise ValueError("Это не файл бэкапа этого приложения (или он от несовместимой версии)")
    try:
        plain = Fernet(_derive_key(passphrase, base64.b64decode(salt_line))).decrypt(token)
    except (InvalidToken, ValueError) as exc:
        raise ValueError("Неверная парольная фраза или файл повреждён") from exc
    return json.loads(plain.decode("utf-8"))


def restore_blob(session: Session, blob: bytes, passphrase: str) -> None:
    load_payload(session, decrypt_blob(blob, passphrase))


def write_backup_file(session: Session, passphrase: str, backup_dir: Path, prefix: str = "") -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    path = backup_dir / f"finance-backup-{prefix}{datetime.now():%Y%m%d-%H%M%S}.fbk"
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(encrypt_session(session, passphrase))
    tmp.replace(path)
    return path


def auto_backup(session: Session, passphrase: str, backup_dir: Path, today: date | None = None) -> Path | None:
    """Автобэкап раз в день (при входе). Хранит последние AUTO_KEEP автоматических файлов."""
    today = today or date.today()
    backup_dir.mkdir(parents=True, exist_ok=True)
    pattern = f"finance-backup-{AUTO_PREFIX}*.fbk"
    existing = sorted(backup_dir.glob(pattern))
    stamp = f"finance-backup-{AUTO_PREFIX}{today:%Y%m%d}"
    if any(p.name.startswith(stamp) for p in existing):
        return None
    path = write_backup_file(session, passphrase, backup_dir, AUTO_PREFIX)
    for old in sorted(backup_dir.glob(pattern))[:-AUTO_KEEP]:
        old.unlink(missing_ok=True)
    return path
