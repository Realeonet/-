// Тесты расчётов телефонного приложения. Запуск: node --test tests_js/
// Ожидаемые числа те же, что в тестах компьютерной версии (tests/test_lots_tax.py), посчитаны вручную.
import test from 'node:test';
import assert from 'node:assert/strict';
import * as core from '../docs/core.js';
import { decryptJson, encryptJson } from '../docs/vault.js';

const near = (a, b, eps = 1e-9) => assert.ok(Math.abs(a - b) < eps, `${a} ≠ ${b}`);
const lot = (o = {}) => ({ id: 1, buyDate: '2024-01-10', qty: '10', price: '100', fees: '10', currency: 'USD',
  fxCcyIls: '3.5', fxUsdIls: '3.5', ...o });

test('курс: пенсы, агороты, отсутствие курса', () => {
  near(core.fxToIls({ GBP: '4.0' }, 'GBp'), 0.04);
  assert.equal(core.fxToIls({}, 'ILS'), 1);
  near(core.fxToIls({}, 'ILA'), 0.01);
  assert.ok(Number.isNaN(core.fxToIls({}, 'EUR')));
});

test('себестоимость включает долю комиссий', () => {
  assert.equal(core.lotCost(lot(), 4), 404);
  assert.equal(core.lotCost(lot()), 1010);
});

test('прибыль: номинальный метод включает курсовую разницу', () => {
  const g = core.computeGain(lot(), 4, 150, 2, 'USD', 3.0, 3.0, 0.25, 'nominal');
  near(g.gainUsd, 194); near(g.costIls, 1414); near(g.proceedsIls, 1794);
  near(g.nominal, 380); near(g.tax, 95);
  const l = core.computeGain(lot(), 4, 150, 2, 'USD', 3.0, 3.0, 0.25, 'linked');
  near(l.linked, 582); near(l.tax, 145.5);
});

test('прибыль в $, но убыток в ₪ — налога нет', () => {
  const g = core.computeGain(lot({ fees: '0' }), 4, 111.5, 0, 'USD', 3.0, 3.0, 0.25, 'nominal');
  near(g.gainUsd, 46); near(g.nominal, -62); assert.equal(g.tax, 0);
});

test('FIFO и симуляция продажи: убыточный лот гасит прибыльный', () => {
  const a = lot({ id: 1, buyDate: '2024-03-01', qty: '5' });
  const b = lot({ id: 2, buyDate: '2023-03-01', qty: '5' });
  assert.deepEqual(core.fifoAllocate([a, b], 7).map(([l, q]) => [l.id, q]), [[2, 5], [1, 2]]);
  assert.throws(() => core.fifoAllocate([a, b], 11), /Не хватает/);
  const win = lot({ id: 1, price: '100', fees: '0', fxCcyIls: '3', fxUsdIls: '3' });
  const lose = lot({ id: 2, price: '200', fees: '0', fxCcyIls: '3', fxUsdIls: '3' });
  const sim = core.simulateSale([[win, 10], [lose, 10]], 150, 0, 'USD', 3, 3, 0.25, 'nominal');
  near(sim.taxable, 0); assert.equal(sim.tax, 0); near(sim.proceedsUsd, 3000); near(sim.netIls, 9000);
});

const world = () => ({
  ...core.emptyData(),
  owners: [{ id: 1, name: 'Леонид' }, { id: 2, name: 'Люба' }],
  custodians: [{ id: 1, name: 'Брокер', kind: 'broker', ownerId: 1 }, { id: 2, name: 'Сейф', kind: 'physical', ownerId: 1 }],
  assets: [
    { id: 1, symbol: 'SPYI', cls: 'etf', currency: 'USD', price: '150', usSitus: true, adjPct: '0' },
    { id: 2, symbol: 'VUSA', cls: 'etf', currency: 'GBp', price: '8000', adjPct: '0' },
    { id: 3, symbol: 'СЕРЕБРО', cls: 'metal', currency: 'USD', price: '1.6', adjPct: '-5' },
    { id: 4, symbol: 'БЕЗЦЕНЫ', cls: 'other', currency: 'USD', price: null },
  ],
  lots: [
    lot({ id: 1, assetId: 1, custodianId: 1 }),
    lot({ id: 2, assetId: 2, custodianId: 1, qty: '5', price: '7000', fees: '0', currency: 'GBp', fxCcyIls: '0.045', fxUsdIls: '3.0' }),
    lot({ id: 3, assetId: 3, custodianId: 2, qty: '311.034768', price: '1', fees: '56', fxCcyIls: '3', fxUsdIls: '3' }),
    lot({ id: 4, assetId: 4, custodianId: 2, qty: '1', price: '5', fees: '0' }),
  ],
  fx: { asOf: '2026-11-11', rates: { USD: '3.2', GBP: '4.0' } },
  pension: [{ owner: 'Люба', balance: '1000' }, { owner: 'Леонид', balance: '500' }],
});

test('оценка позиций в ₪ и $, поправка к споту, лот без цены', () => {
  const pos = core.valuePositions(world());
  near(pos[0].valueUsd, 1500); near(pos[0].valueIls, 4800); near(pos[0].costIls, 3535); near(pos[0].costUsd, 1010);
  near(pos[1].valueIls, 1600); near(pos[1].valueUsd, 500);          // 5 × 8000 пенсов × 0.04
  near(pos[2].valueUsd, 311.034768 * 1.6 * 0.95);                   // дисконт −5%
  assert.equal(pos[3].priced, false);
  const t = core.totals(pos);
  assert.equal(t.unpriced, 1); assert.equal(t.lots, 4);
  near(t.valueUsd, 2000 + 311.034768 * 1.6 * 0.95);
  const byClass = core.groupPositions(pos, (p) => p.cls);
  assert.equal(byClass[0].name, 'etf'); near(byClass[0].valueUsd, 2000);
  assert.equal(core.pensionTotals(world()), 1500); assert.equal(core.pensionTotals(world(), 'Люба'), 1000);
});

test('живой курс доллара используется, только если он свежее официального', () => {
  const data = world();
  data.fxLive = { USD: 3.3, asOf: '2026-11-12T08:00:00' };
  near(core.fxToIls(core.currentRates(data), 'USD'), 3.3);
  data.fxLive = { USD: 3.3, asOf: '2026-11-01T08:00:00' };
  near(core.fxToIls(core.currentRates(data), 'USD'), 3.2);
});

test('наследство США: пороги и план', () => {
  assert.equal(core.estateStatus({ A: 44999 }, 60000, 45000).level, 'ok');
  assert.equal(core.estateStatus({ A: 45000 }, 60000, 45000).level, 'warn');
  const over = core.estateStatus({ A: 50000, B: 20000 }, 60000, 45000);
  assert.equal(over.level, 'over'); assert.equal(over.overLimit, 10000);
  assert.deepEqual(over.plan, [['A', 25000]]);
});

test('окно покупки по времени Израиля при всех сочетаниях летнего и зимнего времени', () => {
  for (const [day, start] of [['2026-01-15', '17:00'], ['2026-07-01', '17:00'], ['2026-03-16', '16:00'], ['2026-10-27', '16:00']]) {
    const w = core.buyWindow(day);
    assert.equal(w.start, start, day);
    assert.equal(w.endMs - w.startMs, 30 * 60000);
  }
  const w = core.buyWindow('2026-07-01');
  assert.equal(core.windowState(w.startMs - 1, w), 'before');
  assert.equal(core.windowState(w.startMs + 1, w), 'open');
  assert.equal(core.windowState(w.endMs + 1, w), 'after');
  assert.equal(core.windowState(0, core.buyWindow('2026-07-04')), 'closed');
});

test('правила: DCA, чёрный список, дни до X-day', () => {
  assert.equal(core.dcaAmount(100001, 100000, 500, 100), 100);
  assert.equal(core.dcaAmount(100000, 100000, 500, 100), 500);
  assert.ok(core.blacklistHit(' msty ', ['MSTY'])); assert.ok(!core.blacklistHit('SPYI', ['MSTY']));
  assert.equal(core.daysUntil('2026-11-18', '2026-11-12'), 6);
});

test('ввод чисел и показ', () => {
  assert.equal(core.parseNumber('1 234,5'), 1234.5); assert.ok(Number.isNaN(core.parseNumber('abc')));
  assert.ok(Number.isNaN(core.parseNumber('')));
  assert.equal(core.fmtMoney(1234567.895, '₪'), '1 234 567.90 ₪');
  assert.equal(core.fmtMoney(-5.5, '$'), '−5.50 $'); assert.equal(core.fmtMoney(NaN), '—');
  assert.equal(core.fmtPct(0.12345), '12.35%'); assert.equal(core.fmtPct(-0.5, true), '−50.00%');
  assert.equal(core.fmtQty(0.5), '0.5'); assert.equal(core.fmtQty(1500), '1 500');
  assert.equal(core.fmtDate('2026-11-12'), '12.11.2026');
});

test('снимок с компьютера заменяет данные, но сохраняет неотправленные покупки с телефона', () => {
  const current = world();
  current.assets.push({ id: 'p:a1', symbol: 'ATOM', cls: 'crypto', currency: 'USD', priceSource: 'coingecko', sourceId: 'cosmos', price: '1.4' });
  current.custodians.push({ id: 'p:c1', name: 'Новая биржа', kind: 'exchange', ownerId: 2 });
  current.lots.push(lot({ id: 'p:u1', uid: 'u1', phone: true, assetId: 1, custodianId: 1, qty: '2' }));          // уже принят
  current.lots.push(lot({ id: 'p:u2', uid: 'u2', phone: true, assetId: 'p:a1', custodianId: 'p:c1', qty: '7' })); // ещё нет
  const snapshot = {
    exportedAt: '2026-11-12T10:00:00', settings: { taxRatePct: '28' }, owners: current.owners,
    custodians: [{ id: 1, name: 'Брокер', kind: 'broker', ownerId: 1 }],
    assets: [{ id: 1, symbol: 'SPYI', cls: 'etf', currency: 'USD', price: '151' }],
    lots: [lot({ id: 9, assetId: 1, custodianId: 1, qty: '12' })], fx: { asOf: '2026-11-12', rates: { USD: '3.1' } },
    pension: [], dividends: {}, phoneUids: ['u1'],
  };
  const { data, kept, dropped } = core.mergeSnapshot(current, snapshot, '2026-11-12T11:00:00');
  assert.equal(kept, 1); assert.equal(dropped, 1);
  assert.equal(data.settings.taxRatePct, '28'); assert.equal(data.settings.xdayDays, 2);
  assert.deepEqual(data.lots.map((l) => l.id), [9, 'p:u2']);
  const atom = data.assets.find((a) => a.symbol === 'ATOM');
  const place = data.custodians.find((c) => c.name === 'Новая биржа');
  assert.ok(atom && place && place.ownerId === 2);
  const out = core.buildPhoneExport(data, '2026-11-12T12:00:00');
  assert.equal(out.kind, 'phone-lots'); assert.equal(out.lots.length, 1);
  assert.deepEqual(out.lots[0].asset, { symbol: 'ATOM', name: '', cls: 'crypto', currency: 'USD', priceSource: 'coingecko', sourceId: 'cosmos' });
  assert.deepEqual(out.lots[0].custodian, { name: 'Новая биржа', kind: 'exchange', owner: 'Люба' });
  assert.equal(out.lots[0].qty, '7');
});

test('шифрование: круговой проход и неверная фраза', async () => {
  const sealed = await encryptJson({ secret: 'сумма 12345.67 ₪' }, 'correct horse');
  assert.ok(!sealed.includes('12345'));
  assert.deepEqual(await decryptJson(sealed, 'correct horse'), { secret: 'сумма 12345.67 ₪' });
  await assert.rejects(() => decryptJson(sealed, 'wrong'), /Неверная парольная фраза/);
  await assert.rejects(() => decryptJson('hello', 'x'), /не файл обмена/);
});
