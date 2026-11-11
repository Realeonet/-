from datetime import date
from decimal import Decimal as D

import pytest
from sqlalchemy.orm import sessionmaker

from app.backup import encrypt_session, restore_blob
from app.db import BalancePoint, OfficialMonthly, PensionAccount, Setting, get_engine, init_db
from app.security import hash_password, verify_password


def _session():
    engine = get_engine(":memory:")
    init_db(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _seed(s):
    s.add(Setting(key="password_hash", value="x"))
    acc = PensionAccount(owner="Люба", product_type="hishtalmut", fund_name="Migdal",
                         track_key="general", balance_ils=D("12345.67"),
                         balance_date=date(2026, 3, 1), fx_hedge_pct=D("0"),
                         weights_as_of=None)
    acc.set_weights({"israel_equity": D(40), "foreign_equity": D(40),
                     "israel_bonds": D(10), "foreign_bonds": D(5), "cash_other": D(5)})
    s.add(acc)
    s.flush()
    s.add(BalancePoint(account_id=acc.id, date=date(2026, 3, 1), balance_ils=D("12345.67"), source="report"))
    s.add(OfficialMonthly(account_id=acc.id, period="2026-02", return_pct=D("1.5")))
    s.commit()


def test_backup_roundtrip_preserves_decimals_and_dates():
    src = _session()
    _seed(src)
    blob = encrypt_session(src, "correct horse")
    assert b"12345.67" not in blob  # в файле не видно открытого текста

    dst = _session()
    restore_blob(dst, blob, "correct horse")
    dst.commit()
    acc = dst.query(PensionAccount).one()
    assert acc.balance_ils == D("12345.67")
    assert acc.balance_date == date(2026, 3, 1)
    assert acc.weights()["israel_equity"] == D(40)
    assert dst.query(BalancePoint).one().source == "report"
    assert dst.query(OfficialMonthly).one().return_pct == D("1.5")


def test_wrong_passphrase_and_garbage_rejected():
    src = _session()
    _seed(src)
    blob = encrypt_session(src, "one")
    with pytest.raises(ValueError, match="парольная фраза"):
        restore_blob(_session(), blob, "two")
    with pytest.raises(ValueError, match="не файл бэкапа|повреждён"):
        restore_blob(_session(), b"hello world", "one")


def test_password_hash_verify():
    stored = hash_password("supersecret")
    assert verify_password("supersecret", stored)
    assert not verify_password("supersecre", stored)
    assert not verify_password("supersecret", "garbage")
    with pytest.raises(ValueError):
        hash_password("short")
