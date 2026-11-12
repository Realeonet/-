"""Обмен с телефоном: шифрование, снимок портфеля, приём покупок, совместимость с приложением телефона."""
import json
import shutil
import subprocess
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest

from app.db import Asset, Custodian, ImportedRow, Lot
from app.services import ledger
from app.services.phone_sync import (apply_phone_file, build_snapshot, decrypt_json, encrypt_json,
                                     phone_settings)
from app.ui.common import DEFAULTS
from tests.conftest import TODAY

ROOT = Path(__file__).resolve().parent.parent


def test_encrypt_roundtrip_and_rejections():
    blob = encrypt_json({"сумма": "12345.67"}, "correct horse")
    assert b"12345" not in blob
    assert decrypt_json(blob, "correct horse") == {"сумма": "12345.67"}
    with pytest.raises(ValueError, match="парольная фраза"):
        decrypt_json(blob, "wrong phrase")
    with pytest.raises(ValueError, match="не файл обмена"):
        decrypt_json(b"hello", "correct horse")
    with pytest.raises(ValueError, match="не короче"):
        encrypt_json({}, "short")
    tampered = json.loads(blob)
    tampered["iter"] = 1
    with pytest.raises(ValueError, match="повреждён"):
        decrypt_json(json.dumps(tampered).encode(), "correct horse")


def seed(s, market, world, sources):
    sources.yahoo.quotes = {"SPYI": (150, "USD")}
    sources.coingecko.prices = {"bitcoin": 70000}
    spyi, btc = s.get(Asset, world["asset"]["SPYI"]), s.get(Asset, world["asset"]["BTC"])
    ibkr, exch = s.get(Custodian, world["cust"]["ibkr"]), s.get(Custodian, world["cust"]["exch"])
    ledger.add_lot(s, market, spyi, ibkr, date(2024, 1, 10), D(10), D(100), fees=D(10))
    ledger.add_lot(s, market, btc, exch, date(2025, 6, 2), D("0.5"), D(60000))
    ledger.sell(s, market, spyi, ibkr.id, TODAY, D(150), qty=D(4))
    s.commit()
    market.refresh_quotes([spyi, btc])


def snapshot(s, market):
    return build_snapshot(s, market, TODAY, phone_settings(s, DEFAULTS),
                          [{"owner": "Люба", "product": "קרן השתלמות", "fund": "Тест", "balance": "1000", "asOf": "2026-11-11"}],
                          {"yearNetIls": "120", "yearNetUsd": "37.5"})


def test_snapshot_contains_open_quantities_prices_and_rates(sf, market, world, sources):
    with sf() as s:
        seed(s, market, world, sources)
        snap = snapshot(s, market)
    assert snap["kind"] == "snapshot" and snap["fx"]["rates"]["USD"] == "3.2" and snap["fx"]["asOf"] == "2026-11-11"
    spyi = next(a for a in snap["assets"] if a["symbol"] == "SPYI")
    assert (spyi["price"], spyi["usSitus"], spyi["held"]) == ("150", True, True)
    lot = next(l for l in snap["lots"] if l["assetId"] == spyi["id"])
    # после продажи 4 из 10 в снимке остаток 6 и пропорциональная доля комиссии (10 × 6/10)
    assert (lot["qty"], lot["fees"], lot["fxUsdIls"], lot["buyDate"]) == ("6", "6", "3.5", "2024-01-10")
    assert snap["settings"]["taxRatePct"] == "25" and snap["settings"]["blacklist"][:2] == ["BITO", "BITI"]
    assert snap["pension"][0]["balance"] == "1000" and snap["phoneUids"] == []
    assert {c["name"] for c in snap["custodians"]} >= {"Тест-брокер США", "Тест-биржа"}


PHONE_FILE = {
    "kind": "phone-lots", "createdAt": "2026-11-11T10:00:00",
    "lots": [
        {"uid": "u-1", "buyDate": "2025-06-02", "qty": "0.1", "price": "65000", "fees": "3", "currency": "USD",
         "asset": {"symbol": "BTC", "cls": "crypto", "currency": "USD", "priceSource": "coingecko", "sourceId": "bitcoin"},
         "custodian": {"name": "Тест-биржа", "kind": "exchange", "owner": "Леонид"}},
        {"uid": "u-2", "buyDate": "2025-06-02", "qty": "100", "price": "1.5", "fees": "0", "currency": "USD",
         "asset": {"symbol": "ATOM", "name": "Cosmos Hub", "cls": "crypto", "currency": "USD",
                   "priceSource": "coingecko", "sourceId": "cosmos"},
         "custodian": {"name": "Новая биржа", "kind": "exchange", "owner": "Люба"}},
        {"uid": "u-3", "buyDate": "2020-01-01", "qty": "1", "price": "1", "currency": "USD",
         "asset": {"symbol": "BTC", "cls": "crypto", "currency": "USD", "priceSource": "coingecko", "sourceId": "bitcoin"},
         "custodian": {"name": "Тест-биржа", "kind": "exchange", "owner": "Леонид"}},
    ],
}


def test_phone_purchases_become_lots_with_official_rate_and_are_not_duplicated(sf, market, world, sources):
    with sf() as s:
        seed(s, market, world, sources)
        report = apply_phone_file(s, market, PHONE_FILE, today=TODAY)
        s.commit()
        assert (report.lots, report.skipped) == (2, 0)
        assert report.created_assets == ["ATOM"] and report.created_custodians == ["Новая биржа"]
        assert len(report.errors) == 1 and "2020-01-01" in report.errors[0]      # нет курса на эту дату
        btc_lot = s.query(Lot).filter(Lot.qty == "0.1").one()
        assert btc_lot.asset.symbol == "BTC" and btc_lot.custodian.name == "Тест-биржа"
        assert btc_lot.fx_usd_ils == D("3.0") and btc_lot.fees == D(3)             # официальный курс на дату
        atom = s.query(Asset).filter_by(symbol="ATOM").one()
        assert (atom.price_source, atom.source_id, atom.history_ticker) == ("coingecko", "cosmos", "ATOM-USD")
        assert s.query(Custodian).filter_by(name="Новая биржа").one().owner.name == "Люба"

        again = apply_phone_file(s, market, PHONE_FILE, today=TODAY)
        s.commit()
        assert (again.lots, again.skipped) == (0, 2)
        assert s.query(Lot).count() == 4
        assert sorted(snapshot(s, market)["phoneUids"]) == ["u-1", "u-2"]            # телефон узнает о приёме
        assert s.query(ImportedRow).count() == 2
    with pytest.raises(ValueError, match="нет покупок"):
        with sf() as s:
            apply_phone_file(s, market, {"kind": "snapshot"})


@pytest.mark.skipif(shutil.which("node") is None, reason="нужен Node.js")
def test_phone_app_decrypts_computer_file_and_values_it_like_the_computer(sf, market, world, sources, tmp_path):
    """Сквозная проверка двух языков: Python шифрует снимок, код телефона расшифровывает и считает капитал."""
    with sf() as s:
        seed(s, market, world, sources)
        snap = snapshot(s, market)
    (tmp_path / "snap.finphone").write_bytes(encrypt_json(snap, "correct horse"))
    script = f"""
      import {{ readFileSync, writeFileSync }} from 'node:fs';
      import * as core from '{ROOT.as_posix()}/docs/core.js';
      import {{ decryptJson, encryptJson }} from '{ROOT.as_posix()}/docs/vault.js';
      const snap = await decryptJson(readFileSync('{tmp_path.as_posix()}/snap.finphone', 'utf8'), 'correct horse');
      const {{ data }} = core.mergeSnapshot(core.emptyData(), snap, '2026-11-11T12:00:00');
      const t = core.totals(core.valuePositions(data));
      const out = core.buildPhoneExport({{ ...data, lots: [...data.lots, {{ id: 'p:x', uid: 'x-9', phone: true,
        assetId: data.assets.find((a) => a.symbol === 'BTC').id, custodianId: data.custodians[0].id,
        buyDate: '2025-06-02', qty: '0.01', price: '66000', fees: '1', currency: 'USD' }}] }}, 'now');
      writeFileSync('{tmp_path.as_posix()}/back.finphone', await encryptJson(out, 'correct horse'));
      console.log(JSON.stringify({{ usd: t.valueUsd, ils: t.valueIls, costUsd: t.costUsd, pension: core.pensionTotals(data) }}));
    """
    (tmp_path / "run.mjs").write_text(script, encoding="utf-8")
    result = subprocess.run(["node", str(tmp_path / "run.mjs")], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    got = json.loads(result.stdout)
    # 6 × 150 + 0.5 × 70000 = 35 900 $; × 3.2 = 114 880 ₪; себестоимость 606 + 30 000 = 30 606 $
    assert got["usd"] == pytest.approx(35900) and got["ils"] == pytest.approx(114880)
    assert got["costUsd"] == pytest.approx(30606) and got["pension"] == 1000
    # и обратно: файл, зашифрованный телефоном, читается компьютером и превращается в лот
    back = decrypt_json((tmp_path / "back.finphone").read_bytes(), "correct horse")
    with sf() as s:
        report = apply_phone_file(s, market, back, today=TODAY)
        assert report.lots == 1 and not report.errors
        assert s.query(Lot).filter(Lot.qty == "0.01").one().note == "записано на телефоне"


@pytest.mark.skipif(shutil.which("node") is None, reason="нужен Node.js")
def test_phone_app_unit_tests_pass():
    result = subprocess.run(["node", "--test", str(ROOT / "tests_js" / "core.test.mjs")], capture_output=True,
                            text=True, timeout=180)
    assert result.returncode == 0, result.stdout[-3000:]
