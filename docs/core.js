// Расчёты приложения для телефона: чистые функции без доступа к сети и хранилищу.
// Точный учёт ведётся на компьютере (Decimal); здесь числа с плавающей точкой — для показа и оценок.

export const CLASS_LABELS = {
  stock: 'Акции', etf: 'ETF / фонды', crypto: 'Криптовалюта', metal: 'Физические металлы',
  cash: 'Наличные / валюта', other: 'Другое', pension: 'Пенсионные накопления',
};
export const KIND_LABELS = {
  broker: 'Брокер', bank: 'Банк', exchange: 'Криптобиржа', self_custody: 'Свой кошелёк',
  physical: 'Физическое хранение', institution: 'Пенсионная компания', other: 'Другое',
};
export const METALS = {
  XAU: { name: 'Золото', grams: 31.1034768 }, XAG: { name: 'Серебро', grams: 31.1034768 },
  XPT: { name: 'Платина', grams: 31.1034768 }, XPD: { name: 'Палладий', grams: 31.1034768 },
  HG: { name: 'Медь', grams: 453.59237 },
};
const MINOR = { GBp: ['GBP', 0.01], GBX: ['GBP', 0.01], ILA: ['ILS', 0.01], ILa: ['ILS', 0.01] };

export const num = (v) => (v === null || v === undefined || v === '' ? NaN : Number(v));

export function parseNumber(text) {
  const cleaned = String(text ?? '').trim().replace(/\s/g, '').replace(',', '.');
  if (cleaned === '' || !/^-?\d*\.?\d+$/.test(cleaned)) return NaN;
  return Number(cleaned);
}

/** ₪ за 1 единицу валюты котировки (понимает пенсы и агороты). NaN — курса нет. */
export function fxToIls(rates, currency) {
  const [major, factor] = MINOR[currency] || [String(currency || '').toUpperCase(), 1];
  if (major === 'ILS') return factor;
  const rate = num(rates?.[major]);
  return Number.isFinite(rate) ? rate * factor : NaN;
}

/** Курсы для оценки «сейчас»: официальные с компьютера, доллар — живой рыночный, если он свежее. */
export function currentRates(data) {
  const rates = { ...(data.fx?.rates || {}) };
  const live = data.fxLive;
  if (live && Number.isFinite(num(live.USD)) && (!data.fx?.asOf || live.asOf.slice(0, 10) >= data.fx.asOf)) {
    rates.USD = live.USD;
  }
  return rates;
}

export function lotCost(lot, qty = num(lot.qty)) {
  const total = num(lot.qty);
  return qty * num(lot.price) + (total ? num(lot.fees || 0) * qty / total : 0);
}

/** Оценка каждого лота. Лоты без цены или курса получают priced=false и не входят в суммы. */
export function valuePositions(data) {
  const rates = currentRates(data);
  const usdIls = fxToIls(rates, 'USD');
  const assets = new Map(data.assets.map((a) => [String(a.id), a]));
  const custodians = new Map(data.custodians.map((c) => [String(c.id), c]));
  const owners = new Map(data.owners.map((o) => [String(o.id), o.name]));
  return data.lots.map((lot) => {
    const asset = assets.get(String(lot.assetId));
    const cust = custodians.get(String(lot.custodianId));
    const qty = num(lot.qty);
    const costIls = lotCost(lot) * num(lot.fxCcyIls);
    const costUsd = costIls / num(lot.fxUsdIls);
    const price = asset ? num(asset.price) : NaN;
    const fx = asset ? fxToIls(rates, asset.currency) : NaN;
    const adj = 1 + (asset ? num(asset.adjPct || 0) : 0) / 100;
    const priced = Number.isFinite(price) && Number.isFinite(fx) && Number.isFinite(usdIls);
    const valueIls = priced ? qty * price * adj * fx : NaN;
    return {
      lot, asset, cust, qty, priced, costIls, costUsd, valueIls,
      valueUsd: priced ? valueIls / usdIls : NaN,
      owner: cust ? owners.get(String(cust.ownerId)) || '—' : '—',
      symbol: asset ? asset.symbol : '?', cls: asset ? asset.cls : 'other',
      place: cust ? cust.name : '—',
    };
  });
}

export function groupPositions(positions, keyFn) {
  const map = new Map();
  for (const p of positions) {
    const key = keyFn(p);
    const g = map.get(key) || { name: key, valueIls: 0, valueUsd: 0, costIls: 0, costUsd: 0, qty: 0, lots: 0, unpriced: 0 };
    g.lots += 1;
    g.qty += p.qty;
    if (!p.priced) g.unpriced += 1;
    else { g.valueIls += p.valueIls; g.valueUsd += p.valueUsd; g.costIls += p.costIls; g.costUsd += p.costUsd; }
    map.set(key, g);
  }
  return [...map.values()].sort((a, b) => b.valueIls - a.valueIls || a.name.localeCompare(b.name));
}

export function totals(positions) {
  return groupPositions(positions, () => 'Итого')[0]
    || { name: 'Итого', valueIls: 0, valueUsd: 0, costIls: 0, costUsd: 0, qty: 0, lots: 0, unpriced: 0 };
}

export function pensionTotals(data, ownerName = null) {
  return (data.pension || []).filter((p) => !ownerName || p.owner === ownerName)
    .reduce((sum, p) => sum + num(p.balance), 0);
}

// ---------- продажа ----------

export function fifoAllocate(lots, qty) {
  if (!(qty > 0)) throw new Error('Количество должно быть больше нуля');
  const sorted = [...lots].sort((a, b) => a.buyDate.localeCompare(b.buyDate) || String(a.id).localeCompare(String(b.id)));
  const out = [];
  let left = qty;
  for (const lot of sorted) {
    const have = num(lot.qty);
    if (!(have > 0)) continue;
    const take = Math.min(have, left);
    out.push([lot, take]);
    left -= take;
    if (left <= 1e-12) return out;
  }
  throw new Error(`Не хватает количества: недостаёт ${+left.toPrecision(10)}`);
}

/** Прибыль по части лота: в $, в ₪ номинально (курсы дат покупки и продажи) и с привязкой к валюте. */
export function computeGain(lot, qty, price, fee, saleCurrency, fxSaleCcyIls, fxSaleUsdIls, rate, method) {
  const proceedsCcy = qty * price - fee;
  const costCcy = lotCost(lot, qty);
  const proceedsIls = proceedsCcy * fxSaleCcyIls;
  const costIls = costCcy * num(lot.fxCcyIls);
  const nominal = proceedsIls - costIls;
  const proceedsUsd = proceedsIls / fxSaleUsdIls;
  const costUsd = costIls / num(lot.fxUsdIls);
  const gainUsd = proceedsUsd - costUsd;
  let linked;
  if (lot.currency === 'ILS') linked = nominal;
  else if (saleCurrency === lot.currency) linked = (proceedsCcy - costCcy) * fxSaleCcyIls;
  else linked = gainUsd * fxSaleUsdIls;
  const taxable = method === 'linked' ? linked : nominal;
  return { lotId: lot.id, qty, proceedsIls, proceedsUsd, costIls, costUsd, gainUsd, nominal, linked, taxable,
    tax: taxable > 0 ? taxable * rate : 0 };
}

export function simulateSale(allocation, price, feeTotal, saleCurrency, fxSaleCcyIls, fxSaleUsdIls, rate, method) {
  const totalQty = allocation.reduce((s, [, q]) => s + q, 0);
  const gains = allocation.map(([lot, q]) =>
    computeGain(lot, q, price, feeTotal * q / totalQty, saleCurrency, fxSaleCcyIls, fxSaleUsdIls, rate, method));
  const proceedsIls = gains.reduce((s, g) => s + g.proceedsIls, 0);
  const taxable = gains.reduce((s, g) => s + g.taxable, 0);
  const tax = taxable > 0 ? taxable * rate : 0;
  return { gains, proceedsIls, proceedsUsd: proceedsIls / fxSaleUsdIls, taxable, tax,
    gainUsd: gains.reduce((s, g) => s + g.gainUsd, 0),
    netIls: proceedsIls - tax, netUsd: (proceedsIls - tax) / fxSaleUsdIls };
}

// ---------- правила ----------

export function estateStatus(byCustodian, limit, warn) {
  const total = Object.values(byCustodian).reduce((s, v) => s + v, 0);
  const level = total >= limit ? 'over' : total >= warn ? 'warn' : 'ok';
  let left = Math.max(0, total - warn);
  const plan = [];
  for (const [name, amount] of Object.entries(byCustodian).sort((a, b) => b[1] - a[1])) {
    if (left <= 0) break;
    const take = Math.min(amount, left);
    plan.push([name, take]);
    left -= take;
  }
  return { total, level, overWarn: Math.max(0, total - warn), overLimit: Math.max(0, total - limit), plan };
}

function tzOffsetMinutes(utcMs, timeZone) {
  const parts = new Intl.DateTimeFormat('en-US', { timeZone, hourCycle: 'h23', year: 'numeric', month: 'numeric',
    day: 'numeric', hour: 'numeric', minute: 'numeric', second: 'numeric' }).formatToParts(new Date(utcMs));
  const f = Object.fromEntries(parts.map((p) => [p.type, Number(p.value)]));
  return (Date.UTC(f.year, f.month - 1, f.day, f.hour, f.minute, f.second) - utcMs) / 60000;
}

const hhmm = (ms, timeZone) => new Intl.DateTimeFormat('ru-RU', { timeZone, hour: '2-digit', minute: '2-digit',
  hourCycle: 'h23' }).format(new Date(ms));

/** Окно покупки: через after…until минут после открытия рынка США (09:30 Нью-Йорк), по времени Израиля. */
export function buyWindow(isoDay, after = 30, until = 60) {
  const [y, m, d] = isoDay.split('-').map(Number);
  const guess = Date.UTC(y, m - 1, d, 9, 30);
  const openMs = guess - tzOffsetMinutes(guess, 'America/New_York') * 60000;
  const weekday = new Date(Date.UTC(y, m - 1, d)).getUTCDay();
  return {
    openMs, startMs: openMs + after * 60000, endMs: openMs + until * 60000,
    open: hhmm(openMs, 'Asia/Jerusalem'), start: hhmm(openMs + after * 60000, 'Asia/Jerusalem'),
    end: hhmm(openMs + until * 60000, 'Asia/Jerusalem'), isWeekday: weekday >= 1 && weekday <= 5,
  };
}

export function windowState(nowMs, w) {
  if (!w.isWeekday) return 'closed';
  return nowMs < w.startMs ? 'before' : nowMs <= w.endMs ? 'open' : 'after';
}

export const dcaAmount = (price, threshold, full, reduced) => (price > threshold ? reduced : full);

export const blacklistHit = (symbol, list) =>
  (list || []).map((s) => s.trim().toUpperCase()).includes(String(symbol || '').trim().toUpperCase());

export function daysUntil(isoDay, todayIso) {
  return Math.round((Date.parse(isoDay) - Date.parse(todayIso)) / 86400000);
}

// ---------- обмен с компьютером ----------

export const emptyData = () => ({
  meta: { importedAt: null, exportedAt: null }, settings: { taxRatePct: '25', taxMethod: 'nominal',
    estateLimitUsd: '60000', estateWarnUsd: '45000', concentrationPct: '40', blacklist: ['BITO', 'BITI', 'MSTY', 'NVDY', 'PLTW'],
    buyWindowAfter: 30, buyWindowUntil: 60, xdayDays: 2, dca: { asset: 'BTC', full: '0', reduced: '0', threshold: '0' } },
  owners: [{ id: 'p:o1', name: 'Я' }], custodians: [], assets: [], lots: [],
  fx: { asOf: null, rates: {}, source: '' }, fxLive: null, pension: [], dividends: {},
});

const sameAsset = (a, b) => (a.priceSource === b.priceSource && a.sourceId && a.sourceId === b.sourceId
  && ['coingecko', 'metal', 'yfinance'].includes(a.priceSource)) || a.symbol.toUpperCase() === b.symbol.toUpperCase();

/**
 * Принять снимок с компьютера. Данные компьютера заменяют всё, кроме покупок, записанных на телефоне
 * и ещё не принятых компьютером (их uid нет в snapshot.phoneUids).
 */
export function mergeSnapshot(current, snapshot, nowIso) {
  const known = new Set(snapshot.phoneUids || []);
  const pending = current.lots.filter((l) => l.phone && !known.has(l.uid));
  const data = {
    meta: { importedAt: nowIso, exportedAt: snapshot.exportedAt },
    settings: { ...emptyData().settings, ...snapshot.settings },
    owners: snapshot.owners, custodians: [...snapshot.custodians], assets: snapshot.assets.map((a) => ({ ...a })),
    lots: [...snapshot.lots], fx: snapshot.fx, fxLive: current.fxLive, pension: snapshot.pension || [],
    dividends: snapshot.dividends || {},
  };
  const ownerName = (id) => (current.owners.find((o) => String(o.id) === String(id)) || {}).name;
  for (const lot of pending) {
    const oldAsset = current.assets.find((a) => String(a.id) === String(lot.assetId));
    const oldCust = current.custodians.find((c) => String(c.id) === String(lot.custodianId));
    if (!oldAsset || !oldCust) continue;
    let asset = data.assets.find((a) => sameAsset(a, oldAsset));
    if (!asset) { asset = { ...oldAsset }; data.assets.push(asset); }
    let cust = data.custodians.find((c) => c.name === oldCust.name);
    if (!cust) {
      const owner = data.owners.find((o) => o.name === ownerName(oldCust.ownerId)) || data.owners[0];
      cust = { ...oldCust, ownerId: owner.id };
      data.custodians.push(cust);
    }
    data.lots.push({ ...lot, assetId: asset.id, custodianId: cust.id });
  }
  return { data, kept: pending.length, dropped: current.lots.filter((l) => l.phone).length - pending.length };
}

/** Файл для компьютера: покупки, записанные на телефоне. */
export function buildPhoneExport(data, nowIso) {
  const lots = data.lots.filter((l) => l.phone).map((lot) => {
    const asset = data.assets.find((a) => String(a.id) === String(lot.assetId));
    const cust = data.custodians.find((c) => String(c.id) === String(lot.custodianId));
    const owner = data.owners.find((o) => String(o.id) === String(cust.ownerId));
    return {
      uid: lot.uid, buyDate: lot.buyDate, qty: String(lot.qty), price: String(lot.price), fees: String(lot.fees || '0'),
      currency: lot.currency, note: lot.note || '',
      asset: { symbol: asset.symbol, name: asset.name || '', cls: asset.cls, currency: asset.currency,
        priceSource: asset.priceSource, sourceId: asset.sourceId || '' },
      custodian: { name: cust.name, kind: cust.kind, owner: owner ? owner.name : '' },
    };
  });
  return { kind: 'phone-lots', createdAt: nowIso, lots };
}

// ---------- показ ----------

const group3 = (s) => s.replace(/\B(?=(\d{3})+(?!\d))/g, ' ');

export function fmtMoney(value, symbol = '', digits = 2) {
  if (!Number.isFinite(value)) return '—';
  const fixed = Math.abs(value).toFixed(digits);
  const [whole, frac] = fixed.split('.');
  const sign = value < 0 && Number(fixed) !== 0 ? '−' : '';
  return `${sign}${group3(whole)}${frac ? '.' + frac : ''}${symbol ? ' ' + symbol : ''}`;
}

export function fmtPct(fraction, signed = false) {
  if (!Number.isFinite(fraction)) return '—';
  const v = (fraction * 100).toFixed(2);
  return `${signed && fraction > 0 ? '+' : ''}${v.replace('-', '−')}%`;
}

export function fmtQty(value) {
  if (!Number.isFinite(value)) return '—';
  const text = String(+value.toFixed(8));
  const [whole, frac] = text.split('.');
  return group3(whole) + (frac ? '.' + frac : '');
}

export const fmtDate = (iso) => (iso ? `${iso.slice(8, 10)}.${iso.slice(5, 7)}.${iso.slice(0, 4)}` : '—');
