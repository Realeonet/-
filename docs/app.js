// Мой капитал — приложение для телефона.
// Данные хранятся только на этом телефоне, зашифрованные паролем. Наружу уходят только запросы цен:
// идентификаторы монет и коды металлов. Количества, суммы и названия счетов не передаются.

import * as core from './core.js';
import { createVault, decryptJson, encryptJson, openVault, sealVault } from './vault.js';

const STORE_KEY = 'fin.vault';
const MIN_PASS = 8;
const REFRESH_MINUTES = 30;

// Частые монеты доступны сразу, без поиска (поиск CoinGecko иногда отвечает «слишком много запросов»).
const POPULAR_COINS = [
  { id: 'bitcoin', symbol: 'BTC', name: 'Bitcoin' }, { id: 'ethereum', symbol: 'ETH', name: 'Ethereum' },
  { id: 'solana', symbol: 'SOL', name: 'Solana' }, { id: 'cosmos', symbol: 'ATOM', name: 'Cosmos Hub' },
  { id: 'tether', symbol: 'USDT', name: 'Tether' }, { id: 'usd-coin', symbol: 'USDC', name: 'USDC' },
  { id: 'binancecoin', symbol: 'BNB', name: 'BNB' }, { id: 'ripple', symbol: 'XRP', name: 'XRP' },
  { id: 'cardano', symbol: 'ADA', name: 'Cardano' }, { id: 'dogecoin', symbol: 'DOGE', name: 'Dogecoin' },
];
const state = { vault: null, data: null, view: 'by-class', owner: '', coins: POPULAR_COINS, busy: false };
const app = document.getElementById('app');
const nav = document.getElementById('nav');

// ---------- утилиты ----------

const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const ils = (v) => core.fmtMoney(v, '₪');
const usd = (v) => core.fmtMoney(v, '$');
const todayIso = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};
const nowIso = () => new Date().toISOString().slice(0, 19);
const uid = () => (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`);
const signClass = (v) => (v > 0 ? 'good' : v < 0 ? 'bad' : '');
const rate = () => core.num(state.data.settings.taxRatePct || 25) / 100;

let toastTimer;
function toast(message) {
  const el = document.getElementById('toast');
  el.textContent = message;
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), 3500);
}

async function save() {
  localStorage.setItem(STORE_KEY, await sealVault(state.vault, state.data));
}

function go(hash) {
  if (location.hash === hash) render(); else location.hash = hash;
}

const field = (id) => document.getElementById(id);
const val = (id) => field(id).value.trim();

function numberField(id, name, { optional = false, positive = false } = {}) {
  const raw = val(id);
  if (raw === '' && optional) return null;
  const n = core.parseNumber(raw);
  if (!Number.isFinite(n)) throw new Error(`«${name}»: введите число, например 12.5`);
  if (n < 0 || (positive && n <= 0)) throw new Error(`«${name}» должно быть ${positive ? 'больше нуля' : 'не меньше нуля'}`);
  return n;
}

// ---------- вход ----------

function renderLock() {
  nav.hidden = true;
  const exists = localStorage.getItem(STORE_KEY) !== null;
  app.innerHTML = exists ? `
    <h1>Мой капитал</h1>
    <form id="unlock" class="card">
      <label for="pw">Пароль</label>
      <input id="pw" type="password" autocomplete="current-password" required>
      <button class="primary">Войти</button>
      <p id="err" class="bad" role="alert"></p>
    </form>
    <button id="wipe" class="danger wide">Забыл пароль — стереть данные на телефоне</button>
    <p class="muted">Пароль восстановить нельзя. После стирания данные можно снова загрузить файлом с компьютера.</p>` : `
    <h1>Мой капитал</h1>
    <p>Придумайте пароль для входа. Им шифруются данные на этом телефоне.</p>
    <form id="setup" class="card">
      <label for="pw1">Пароль (не короче ${MIN_PASS} символов)</label>
      <input id="pw1" type="password" autocomplete="new-password" required minlength="${MIN_PASS}">
      <label for="pw2">Повторите пароль</label>
      <input id="pw2" type="password" autocomplete="new-password" required>
      <button class="primary">Создать и войти</button>
      <p id="err" class="bad" role="alert"></p>
    </form>
    <p class="muted">Приложение никогда не спрашивает пароли от брокеров, seed-фразы и приватные ключи.</p>`;

  field('unlock')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    field('err').textContent = 'Проверяю…';
    try {
      const opened = await openVault(localStorage.getItem(STORE_KEY), field('pw').value);
      state.vault = { key: opened.key, salt: opened.salt };
      state.data = { ...core.emptyData(), ...opened.data };
      start();
    } catch (err) {
      field('err').textContent = err.message;
    }
  });
  field('setup')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const p1 = field('pw1').value;
    if (p1.length < MIN_PASS) { field('err').textContent = `Пароль должен быть не короче ${MIN_PASS} символов`; return; }
    if (p1 !== field('pw2').value) { field('err').textContent = 'Пароли не совпадают'; return; }
    state.vault = await createVault(p1);
    state.data = core.emptyData();
    await save();
    start();
  });
  field('wipe')?.addEventListener('click', () => {
    if (confirm('Стереть все данные приложения на этом телефоне? Отменить это нельзя.')) {
      localStorage.removeItem(STORE_KEY);
      renderLock();
    }
  });
}

function start() {
  nav.hidden = false;
  if (!location.hash) location.hash = '#/';
  render();
  const last = state.data.meta.liveAt;
  if (navigator.onLine && (!last || Date.now() - Date.parse(last) > REFRESH_MINUTES * 60000)) refreshLive(true);
}

// ---------- живые цены ----------

async function getJson(url) {
  const res = await fetch(url, { cache: 'no-store' });
  if (!res.ok) throw new Error(`${new URL(url).host}: код ${res.status}`);
  return res.json();
}

async function refreshLive(silent = false) {
  if (state.busy) return;
  state.busy = true;
  const errors = [];
  const day = todayIso();
  try {
    const coins = state.data.assets.filter((a) => a.priceSource === 'coingecko' && a.sourceId);
    const ids = [...new Set(['bitcoin', ...coins.map((a) => a.sourceId)])];
    try {
      const body = await getJson(`https://api.coingecko.com/api/v3/simple/price?ids=${encodeURIComponent(ids.join(','))}&vs_currencies=usd,ils`);
      for (const a of coins) {
        if (body[a.sourceId]?.usd !== undefined) Object.assign(a, { price: String(body[a.sourceId].usd), priceAsOf: day, priceSrc: 'CoinGecko' });
        else errors.push(`${a.symbol}: CoinGecko не знает монету`);
      }
      if (body.bitcoin?.usd && body.bitcoin?.ils) {
        state.data.fxLive = { USD: body.bitcoin.ils / body.bitcoin.usd, asOf: nowIso() };
      }
    } catch (err) { errors.push(`Крипта и курс: ${err.message}`); }
    const metals = state.data.assets.filter((a) => a.priceSource === 'metal' && core.METALS[a.sourceId]);
    for (const code of new Set(metals.map((a) => a.sourceId))) {
      try {
        const body = await getJson(`https://api.gold-api.com/price/${code}`);
        const perGram = Number(body.price) / core.METALS[code].grams;
        for (const a of metals.filter((m) => m.sourceId === code)) {
          Object.assign(a, { price: String(perGram), priceAsOf: day, priceSrc: 'спот gold-api.com' });
        }
      } catch (err) { errors.push(`${core.METALS[code].name}: ${err.message}`); }
    }
    state.data.meta.liveAt = new Date().toISOString();
    await save();
  } finally {
    state.busy = false;
  }
  if (errors.length) toast(`Обновлено с замечаниями: ${errors[0]}`);
  else if (!silent) toast('Цены обновлены');
  render();
}

// ---------- общие куски ----------

function freshness() {
  const d = state.data;
  const parts = [];
  parts.push(d.meta.exportedAt ? `Данные с компьютера: ${core.fmtDate(d.meta.exportedAt.slice(0, 10))}` : 'Данные с компьютера ещё не загружались');
  if (d.meta.liveAt) parts.push(`крипта и металлы: ${new Date(d.meta.liveAt).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })}`);
  const rates = core.currentRates(d);
  const usdRate = core.fxToIls(rates, 'USD');
  if (Number.isFinite(usdRate)) {
    const live = d.fxLive && rates.USD === d.fxLive.USD;
    parts.push(`$1 = ${usdRate.toFixed(3)} ₪ (${live ? 'рыночный курс, не официальный' : `Банк Израиля на ${core.fmtDate(d.fx.asOf)}`})`);
  }
  return `<p class="muted">${esc(parts.join(' · '))}</p>`;
}

function positions() {
  const all = core.valuePositions(state.data);
  return state.owner ? all.filter((p) => p.owner === state.owner) : all;
}

const ownerSelect = () => (state.data.owners.length > 1 ? `
  <label for="owner">Владелец</label>
  <select id="owner"><option value="">Все</option>${state.data.owners.map((o) =>
    `<option${o.name === state.owner ? ' selected' : ''}>${esc(o.name)}</option>`).join('')}</select>` : '');

function bindOwner() {
  field('owner')?.addEventListener('change', (e) => { state.owner = e.target.value; render(); });
}

function bars(rows, total, threshold = null) {
  return rows.map((r) => {
    const share = total > 0 ? r.value / total : 0;
    const over = threshold !== null && share * 100 > threshold;
    return `<div class="bar-row"><div class="row"><span>${esc(r.name)}${r.tag ? `<span class="tag">${esc(r.tag)}</span>` : ''}</span>
      <span>${ils(r.value)} · ${core.fmtPct(share)}</span></div>
      <div class="bar${over ? ' over' : ''}"><i style="width:${Math.min(100, share * 100).toFixed(1)}%"></i></div>
      ${r.sub ? `<div class="muted">${r.sub}</div>` : ''}</div>`;
  }).join('');
}

// ---------- капитал ----------

const VIEWS = { 'by-class': 'Классы', 'by-place': 'Места', 'by-owner': 'Владельцы', 'by-asset': 'Активы' };

function viewHome() {
  const d = state.data;
  const pos = positions();
  const t = core.totals(pos);
  const usdIls = core.fxToIls(core.currentRates(d), 'USD');
  const pension = core.pensionTotals(d, state.owner || null);
  const grandIls = t.valueIls + pension;
  const grandUsd = t.valueUsd + (Number.isFinite(usdIls) ? pension / usdIls : 0);
  const gainUsd = t.valueUsd - t.costUsd;
  const gainIls = t.valueIls - t.costIls;

  if (!d.lots.length && !pension) {
    return `<h1>Мой капитал</h1><div class="card"><p>Пока пусто.</p>
      <p>Загрузите данные с компьютера — раздел <a href="#/sync">Обмен с компьютером</a> — или запишите первую покупку
      на вкладке <a href="#/add">Добавить</a>.</p></div>`;
  }

  const keyFn = { 'by-class': (p) => core.CLASS_LABELS[p.cls] || p.cls, 'by-place': (p) => p.place,
    'by-owner': (p) => p.owner, 'by-asset': (p) => p.symbol }[state.view];
  const groups = core.groupPositions(pos, keyFn);
  const rows = groups.map((g) => ({ name: g.name, value: g.valueIls,
    sub: `${usd(g.valueUsd)} · прибыль <span class="${signClass(g.valueUsd - g.costUsd)}">${usd(g.valueUsd - g.costUsd)}</span>${g.unpriced ? ` · без цены: ${g.unpriced}` : ''}` }));
  if (pension && state.view === 'by-class') rows.push({ name: core.CLASS_LABELS.pension, value: pension, sub: 'оценка с компьютера' });
  if (pension && state.view === 'by-owner') {
    for (const p of d.pension) {
      if (state.owner && p.owner !== state.owner) continue;
      const row = rows.find((r) => r.name === p.owner);
      if (row) row.value += core.num(p.balance); else rows.push({ name: p.owner, value: core.num(p.balance), sub: 'пенсионные' });
    }
  }
  rows.sort((a, b) => b.value - a.value);
  const div = d.dividends || {};

  return `<h1>Мой капитал</h1>
    ${ownerSelect()}
    <div class="card">
      <div class="muted">Капитал</div>
      <div class="big">${ils(grandIls)}</div>
      <div class="row"><span class="muted">в долларах</span><span>${usd(grandUsd)}</span></div>
      <div class="row"><span class="muted">прибыль по лотам, $</span><span class="${signClass(gainUsd)}">${usd(gainUsd)} · ${core.fmtPct(t.costUsd ? gainUsd / t.costUsd : NaN, true)}</span></div>
      <div class="row"><span class="muted">прибыль по лотам, ₪</span><span class="${signClass(gainIls)}">${ils(gainIls)}</span></div>
      ${pension ? `<div class="row"><span class="muted">из них пенсионные</span><span>${ils(pension)}</span></div>` : ''}
      ${div.yearNetIls ? `<div class="row"><span class="muted">дивиденды нетто в год</span><span>${ils(core.num(div.yearNetIls))}</span></div>` : ''}
    </div>
    ${t.unpriced ? `<div class="note warn">У ${t.unpriced} лот(ов) нет цены — они не входят в сумму. Задайте цену в разделе «Ещё → Цены вручную».</div>` : ''}
    <div class="seg" role="group" aria-label="Разрез">${Object.entries(VIEWS).map(([k, label]) =>
      `<button data-view="${k}" aria-pressed="${k === state.view}">${label}</button>`).join('')}</div>
    <div class="card">${bars(rows, grandIls) || '<p class="muted">Нет данных.</p>'}</div>
    ${state.view !== 'by-class' && state.view !== 'by-owner' && pension ? '<p class="muted">Пенсионные накопления в этом разрезе не показаны.</p>' : ''}
    <button id="refresh" class="wide"${state.busy ? ' disabled' : ''}>🔄 Обновить цены крипты и металлов</button>
    ${freshness()}
    <p class="foot">Цены акций и ETF приходят с компьютера и на телефоне не обновляются. Прибыль в ₪ считается по курсам дат покупки.
    Это личный инструмент учёта, а не инвестиционный совет.</p>`;
}

// ---------- позиции ----------

function viewPositions() {
  const pos = positions();
  if (!pos.length) return '<h1>Позиции</h1><p class="muted">Лотов пока нет.</p>';
  const groups = core.groupPositions(pos, (p) => p.symbol);
  return `<h1>Позиции</h1>${ownerSelect()}<div class="card">${groups.map((g) => {
    const lots = pos.filter((p) => p.symbol === g.name).sort((a, b) => a.lot.buyDate.localeCompare(b.lot.buyDate));
    const a = lots[0].asset || {};
    const gain = g.valueUsd - g.costUsd;
    return `<details><summary><div class="row"><strong>${esc(g.name)}</strong><span>${usd(g.valueUsd)}</span></div>
      <div class="row muted"><span>${core.fmtQty(g.qty)}${a.cls === 'metal' ? ' г' : ''} · ${a.price ? `${core.fmtQty(core.num(a.price))} ${esc(a.currency)}` : 'нет цены'}${a.priceAsOf ? ` на ${core.fmtDate(a.priceAsOf)}` : ''}</span>
      <span class="${signClass(gain)}">${usd(gain)}</span></div></summary>
      ${lots.map((p) => `<div class="lot">
        <div class="row"><span>${core.fmtDate(p.lot.buyDate)} · ${esc(p.place)}${p.lot.phone ? '<span class="tag">с телефона</span>' : ''}</span><span>${core.fmtQty(p.qty)}</span></div>
        <div class="row muted"><span>себестоимость ${core.fmtQty(core.lotCost(p.lot) / p.qty)} ${esc(p.lot.currency)}/ед.</span>
        <span class="${signClass(p.valueUsd - p.costUsd)}">${usd(p.valueUsd - p.costUsd)} · ${ils(p.valueIls - p.costIls)}</span></div>
        ${p.lot.phone ? `<button class="danger" data-del-lot="${esc(p.lot.id)}" style="margin-top:6px">Удалить эту запись</button>` : ''}
      </div>`).join('')}</details>`;
  }).join('')}</div>
  <p class="muted">Нажмите на актив, чтобы увидеть лоты. Прибыль показана в $ и в ₪.</p>`;
}

// ---------- места ----------

function viewPlaces() {
  const d = state.data;
  const pos = core.valuePositions(d);
  const total = core.totals(pos).valueIls;
  const threshold = core.num(d.settings.concentrationPct || 40);
  if (!d.custodians.length) return '<h1>Места хранения</h1><p class="muted">Мест пока нет. Они появятся после загрузки данных с компьютера или при записи покупки.</p>';
  const owners = new Map(d.owners.map((o) => [String(o.id), o.name]));
  const groups = new Map(core.groupPositions(pos, (p) => String(p.lot.custodianId)).map((g) => [g.name, g]));
  const self = pos.filter((p) => p.priced && p.cust && (p.cust.selfCustody || ['self_custody', 'physical'].includes(p.cust.kind)))
    .reduce((s, p) => s + p.valueIls, 0);
  const cards = [...d.custodians].sort((a, b) => (groups.get(String(b.id))?.valueIls || 0) - (groups.get(String(a.id))?.valueIls || 0)).map((c) => {
    const g = groups.get(String(c.id));
    const value = g ? g.valueIls : 0;
    const share = total > 0 ? value / total : 0;
    const mine = pos.filter((p) => String(p.lot.custodianId) === String(c.id));
    const selfC = c.selfCustody || ['self_custody', 'physical'].includes(c.kind);
    return `<div class="card">
      <div class="row"><strong>${esc(c.name)}</strong><span>${ils(value)}</span></div>
      <div class="muted">${esc(core.KIND_LABELS[c.kind] || c.kind)} · ${esc(owners.get(String(c.ownerId)) || '')} · ${selfC ? '🔑 своё хранение' : '🏛 кастодиальное'} · ${c.withholds ? 'налог удерживает сам' : 'налог декларировать самому'}</div>
      <div class="bar${share * 100 > threshold ? ' over' : ''}"><i style="width:${Math.min(100, share * 100).toFixed(1)}%"></i></div>
      <div class="muted">${core.fmtPct(share)} капитала${g ? ` · ${usd(g.valueUsd)}` : ''}</div>
      ${share * 100 > threshold ? `<div class="note warn">Концентрация выше порога ${threshold}%.</div>` : ''}
      ${c.location ? `<div class="muted">Где лежит: ${esc(c.location)}</div>` : ''}
      ${mine.length ? `<details><summary class="muted">Что здесь лежит (${new Set(mine.map((p) => p.symbol)).size})</summary>
        ${core.groupPositions(mine, (p) => p.symbol).map((a) => `<div class="row"><span>${esc(a.name)} · ${core.fmtQty(a.qty)}</span><span>${usd(a.valueUsd)}</span></div>`).join('')}</details>` : ''}
    </div>`;
  }).join('');
  return `<h1>Места хранения</h1>
    <div class="card grid2"><div><div class="muted">🏛 У кастодианов</div><strong>${ils(total - self)}</strong><div class="muted">${core.fmtPct(total ? (total - self) / total : NaN)}</div></div>
    <div><div class="muted">🔑 Своё хранение</div><strong>${ils(self)}</strong><div class="muted">${core.fmtPct(total ? self / total : NaN)}</div></div></div>
    ${cards}<p class="muted">Пенсионные накопления здесь не учитываются.</p>`;
}

// ---------- добавить покупку ----------

function viewAdd() {
  const d = state.data;
  const assetOptions = d.assets.map((a) => `<option value="${esc(a.id)}">${esc(a.symbol)}${a.name ? ` — ${esc(a.name)}` : ''}</option>`).join('');
  const placeOptions = d.custodians.map((c) => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('');
  const usdKnown = Number.isFinite(core.fxToIls(core.currentRates(d), 'USD'));
  return `<h1>Записать покупку</h1>
    <form id="addForm" class="card" novalidate>
      <label for="asset">Актив</label>
      <select id="asset">${assetOptions}<option value="__new">➕ Новый актив…</option></select>
      <div id="newAsset" hidden>
        <label for="naClass">Что это</label>
        <select id="naClass"><option value="crypto">Криптовалюта</option><option value="metal">Физический металл</option>
          <option value="etf">Акция / ETF</option><option value="cash">Наличные</option><option value="other">Другое</option></select>
        <div id="naCrypto">
          <label for="naCoin">Монета</label>
          <select id="naCoin">${state.coins.map((c) => `<option>${esc(c.symbol)} — ${esc(c.name)}</option>`).join('')}</select>
          <label for="naSearch">Нет в списке? Найдите по названию или тикеру</label>
          <input id="naSearch" placeholder="например NEAR" autocapitalize="off">
          <button type="button" id="naFind" class="wide">Найти в CoinGecko</button>
        </div>
        <div id="naMetal" hidden><label for="naMetalCode">Металл</label>
          <select id="naMetalCode">${Object.entries(core.METALS).map(([k, m]) => `<option value="${k}">${m.name}</option>`).join('')}</select></div>
        <div id="naManual" hidden>
          <label for="naSymbol">Тикер или название</label><input id="naSymbol" autocapitalize="characters">
          <label for="naCcy">Валюта цены</label><input id="naCcy" value="USD" autocapitalize="characters">
          <p class="muted">Цена акций и ETF на телефоне не обновляется: она придёт с компьютера после синхронизации.</p>
        </div>
      </div>
      <div id="assetWarn"></div>
      <label for="place">Место хранения</label>
      <select id="place">${placeOptions}<option value="__new">➕ Новое место…</option></select>
      <div id="newPlace" hidden>
        <label for="npName">Название</label><input id="npName">
        <label for="npKind">Тип</label>
        <select id="npKind">${Object.entries(core.KIND_LABELS).filter(([k]) => k !== 'institution').map(([k, v]) => `<option value="${k}">${v}</option>`).join('')}</select>
        <label for="npOwner">Владелец</label>
        <select id="npOwner">${d.owners.map((o) => `<option value="${esc(o.id)}">${esc(o.name)}</option>`).join('')}</select>
      </div>
      <label for="buyDate">Дата покупки</label>
      <input id="buyDate" type="date" value="${todayIso()}" max="${todayIso()}">
      <label for="qty" id="qtyLabel">Количество</label>
      <input id="qty" inputmode="decimal" placeholder="0">
      <label for="price">Цена за единицу</label>
      <input id="price" inputmode="decimal" placeholder="0">
      <label for="total">…или общая сумма покупки</label>
      <input id="total" inputmode="decimal" placeholder="необязательно">
      <label for="fee">Комиссия</label>
      <input id="fee" inputmode="decimal" value="0">
      ${usdKnown ? '' : '<label for="fxUsd">Курс доллара, ₪ за $1 (нет в данных)</label><input id="fxUsd" inputmode="decimal" placeholder="например 3.20">'}
      <label for="note">Заметка</label>
      <input id="note" placeholder="необязательно">
      <button class="primary">Записать</button>
      <p id="err" class="bad" role="alert"></p>
    </form>
    <p class="muted">Покупка сохраняется на телефоне и помечается «с телефона». Отправьте её на компьютер в разделе
    «Ещё → Обмен с компьютером»: там она получит официальный курс Банка Израиля на дату покупки.
    Для металла количество — граммы чистого металла, цена — за грамм.</p>`;
}

function bindAdd() {
  const d = state.data;
  const toggle = () => {
    const isNew = val('asset') === '__new';
    field('newAsset').hidden = !isNew;
    const cls = isNew ? val('naClass') : null;
    field('naCrypto').hidden = cls !== 'crypto';
    field('naMetal').hidden = cls !== 'metal';
    field('naManual').hidden = !cls || cls === 'crypto' || cls === 'metal';
    field('newPlace').hidden = val('place') !== '__new';
    const asset = d.assets.find((a) => String(a.id) === val('asset'));
    const symbol = isNew ? (cls === 'crypto' ? (state.coins[field('naCoin').selectedIndex]?.symbol || '') : val('naSymbol')) : asset?.symbol;
    const warn = [];
    if (core.blacklistHit(symbol, d.settings.blacklist)) warn.push(`<div class="note bad">⛔ ${esc(symbol)} в чёрном списке. Подумайте дважды.</div>`);
    if (asset?.nextEx) {
      const left = core.daysUntil(asset.nextEx, todayIso());
      if (left >= 0 && left <= (d.settings.xdayDays ?? 2)) {
        warn.push(`<div class="note warn">⏳ X-day ${core.fmtDate(asset.nextEx)}${asset.nextExEstimated ? ' (прогноз)' : ''} — через ${left} дн. Покупка сейчас = налог 25% с ближайшей выплаты.</div>`);
      }
    }
    field('assetWarn').innerHTML = warn.join('');
    field('qtyLabel').textContent = (asset?.cls === 'metal' || cls === 'metal') ? 'Количество, граммы чистого металла' : 'Количество';
    if (asset?.price && !val('price')) field('price').placeholder = `сейчас ${core.fmtQty(core.num(asset.price))} ${asset.currency}`;
  };
  ['asset', 'naClass', 'place', 'naCoin', 'naSymbol'].forEach((id) => field(id).addEventListener('input', toggle));
  if (!d.assets.length) field('asset').value = '__new';
  if (!d.custodians.length) field('place').value = '__new';
  toggle();

  field('naFind').addEventListener('click', async () => {
    const q = val('naSearch');
    if (!q) return;
    try {
      const body = await getJson(`https://api.coingecko.com/api/v3/search?query=${encodeURIComponent(q)}`);
      const found = (body.coins || []).slice(0, 15).map((c) => ({ id: c.id, symbol: String(c.symbol || '').toUpperCase(), name: c.name }));
      if (!found.length) { toast('Ничего не найдено'); return; }
      state.coins = found;
      field('naCoin').innerHTML = found.map((c) => `<option>${esc(c.symbol)} — ${esc(c.name)}</option>`).join('');
      toast(`Найдено: ${found.length}. Выберите монету в списке выше.`);
      toggle();
    } catch (err) { toast(`Поиск сейчас недоступен (${err.message}). Попробуйте через минуту.`); }
  });

  field('addForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const err = field('err');
    err.textContent = '';
    try {
      const qty = numberField('qty', 'Количество', { positive: true });
      const total = numberField('total', 'Общая сумма', { optional: true });
      const price = total !== null ? total / qty : numberField('price', 'Цена за единицу');
      const fee = numberField('fee', 'Комиссия', { optional: true }) ?? 0;
      const buyDate = val('buyDate');
      if (!buyDate || buyDate > todayIso()) throw new Error('Дата покупки не может быть в будущем');

      let asset = d.assets.find((a) => String(a.id) === val('asset'));
      let newAsset = null;
      if (!asset) {
        const cls = val('naClass');
        if (cls === 'crypto') {
          const coin = state.coins[field('naCoin').selectedIndex];
          if (!coin) throw new Error('Выберите монету');
          newAsset = { symbol: coin.symbol, name: coin.name, cls, currency: 'USD', priceSource: 'coingecko', sourceId: coin.id };
        } else if (cls === 'metal') {
          const code = val('naMetalCode');
          newAsset = { symbol: core.METALS[code].name.toUpperCase(), name: core.METALS[code].name, cls, currency: 'USD', priceSource: 'metal', sourceId: code };
        } else {
          const symbol = val('naSymbol').toUpperCase();
          if (!symbol) throw new Error('Укажите тикер или название актива');
          const ccy = val('naCcy') || 'USD';
          newAsset = cls === 'cash'
            ? { symbol, name: '', cls, currency: ccy, priceSource: 'cash', sourceId: '', price: '1', priceAsOf: todayIso() }
            : { symbol, name: '', cls, currency: ccy, priceSource: cls === 'etf' ? 'yfinance' : 'manual', sourceId: cls === 'etf' ? symbol : '', price: String(price), priceAsOf: buyDate, priceSrc: 'цена покупки' };
        }
        asset = d.assets.find((a) => a.symbol.toUpperCase() === newAsset.symbol.toUpperCase()
          || (newAsset.sourceId && a.priceSource === newAsset.priceSource && a.sourceId === newAsset.sourceId));
      }
      let place = d.custodians.find((c) => String(c.id) === val('place'));
      let newPlace = null;
      if (!place) {
        const name = val('npName');
        if (!name) throw new Error('Укажите название места хранения');
        place = d.custodians.find((c) => c.name === name);
        if (!place) newPlace = { id: `p:${uid()}`, name, kind: val('npKind'), ownerId: val('npOwner'), selfCustody: ['self_custody', 'physical'].includes(val('npKind')), withholds: false, location: '' };
      }
      const currency = (asset || newAsset).currency;
      const rates = { ...core.currentRates(d) };
      const manualUsd = field('fxUsd') ? numberField('fxUsd', 'Курс доллара', { positive: true }) : null;
      if (manualUsd) rates.USD = manualUsd;
      const fxUsd = core.fxToIls(rates, 'USD');
      const fxCcy = core.fxToIls(rates, currency);
      if (!Number.isFinite(fxUsd)) throw new Error('Нет курса доллара: обновите цены или загрузите данные с компьютера');
      if (!Number.isFinite(fxCcy)) throw new Error(`Нет курса для валюты ${currency}: загрузите данные с компьютера`);

      if (!asset) { asset = { id: `p:${uid()}`, adjPct: '0', usSitus: false, ...newAsset }; d.assets.push(asset); }
      if (newPlace) { d.custodians.push(newPlace); place = newPlace; }
      if (manualUsd) d.fxLive = { USD: manualUsd, asOf: nowIso() };
      const id = uid();
      d.lots.push({ id: `p:${id}`, uid: id, phone: true, assetId: asset.id, custodianId: place.id, buyDate,
        qty: String(qty), price: String(price), fees: String(fee), currency, fxCcyIls: String(fxCcy), fxUsdIls: String(fxUsd),
        fxApprox: true, note: val('note') });
      await save();
      toast(`Записано: ${asset.symbol}, ${core.fmtQty(qty)}`);
      if (asset.priceSource === 'coingecko' || asset.priceSource === 'metal') refreshLive(true);
      go('#/positions');
    } catch (ex) {
      err.textContent = ex.message;
    }
  });
}

// ---------- симулятор продажи ----------

function viewSell() {
  const d = state.data;
  const held = [...new Set(d.lots.map((l) => String(l.assetId)))].map((id) => d.assets.find((a) => String(a.id) === id)).filter(Boolean);
  if (!held.length) return '<h1>Если продам</h1><p class="muted">Лотов пока нет.</p>';
  return `<h1>Если продам</h1>
    <div class="note warn">Ориентировочно — сверить с налоговым консультантом. Точный расчёт с официальным курсом — на компьютере.</div>
    <div class="card">
      <label for="sAsset">Актив</label>
      <select id="sAsset">${held.map((a) => `<option value="${esc(a.id)}">${esc(a.symbol)}</option>`).join('')}</select>
      <label for="sPlace">Место хранения</label><select id="sPlace"></select>
      <label for="sQty">Количество</label><input id="sQty" inputmode="decimal">
      <label for="sPrice">Цена продажи за единицу</label><input id="sPrice" inputmode="decimal">
      <label for="sFee">Комиссия</label><input id="sFee" inputmode="decimal" value="0">
    </div>
    <div id="sOut"></div>`;
}

function bindSell() {
  const d = state.data;
  if (!field('sAsset')) return;
  const fillPlaces = () => {
    const lots = d.lots.filter((l) => String(l.assetId) === val('sAsset'));
    const ids = [...new Set(lots.map((l) => String(l.custodianId)))];
    field('sPlace').innerHTML = ids.map((id) => {
      const c = d.custodians.find((x) => String(x.id) === id);
      const qty = lots.filter((l) => String(l.custodianId) === id).reduce((s, l) => s + core.num(l.qty), 0);
      return `<option value="${esc(id)}">${esc(c ? c.name : id)} — ${core.fmtQty(qty)}</option>`;
    }).join('');
    const asset = d.assets.find((a) => String(a.id) === val('sAsset'));
    field('sPrice').value = asset?.price ? String(core.num(asset.price)) : '';
    fillQty();
  };
  const lotsHere = () => d.lots.filter((l) => String(l.assetId) === val('sAsset') && String(l.custodianId) === val('sPlace'));
  const fillQty = () => { field('sQty').value = String(+lotsHere().reduce((s, l) => s + core.num(l.qty), 0).toFixed(8)); calc(); };
  const calc = () => {
    const out = field('sOut');
    const asset = d.assets.find((a) => String(a.id) === val('sAsset'));
    const qty = core.parseNumber(val('sQty'));
    const price = core.parseNumber(val('sPrice'));
    const fee = core.parseNumber(val('sFee') || '0');
    const rates = core.currentRates(d);
    const fxCcy = core.fxToIls(rates, asset.currency);
    const fxUsd = core.fxToIls(rates, 'USD');
    if (![qty, price, fee].every(Number.isFinite) || qty <= 0) { out.innerHTML = '<p class="muted">Введите количество и цену.</p>'; return; }
    if (!Number.isFinite(fxCcy) || !Number.isFinite(fxUsd)) { out.innerHTML = '<div class="note bad">Нет курса валюты — загрузите данные с компьютера.</div>'; return; }
    try {
      const adj = 1 + core.num(asset.adjPct || 0) / 100;
      const sim = core.simulateSale(core.fifoAllocate(lotsHere(), qty), price * adj, fee, asset.currency, fxCcy, fxUsd, rate(), d.settings.taxMethod);
      const place = d.custodians.find((c) => String(c.id) === val('sPlace'));
      out.innerHTML = `<div class="card">
        <div class="row"><span>Выручка</span><span>${usd(sim.proceedsUsd)} · ${ils(sim.proceedsIls)}</span></div>
        <div class="row"><span>Прибыль в $</span><span class="${signClass(sim.gainUsd)}">${usd(sim.gainUsd)}</span></div>
        <div class="row"><span>Налогооблагаемая прибыль, ₪</span><span class="${signClass(sim.taxable)}">${ils(sim.taxable)}</span></div>
        <div class="row"><strong>Налог ${core.fmtPct(rate())}</strong><strong>${ils(sim.tax)}</strong></div>
        <div class="row"><strong>На руки</strong><strong>${ils(sim.netIls)} · ${usd(sim.netUsd)}</strong></div>
        <p class="muted">Лоты списываются по FIFO. Метод: ${d.settings.taxMethod === 'linked' ? 'с привязкой к валюте' : 'номинальный, курсовая разница входит в прибыль'}.
        ${place?.withholds ? 'Это место удерживает налог само.' : 'Это место налог не удерживает — декларировать самому.'}</p>
        <table><tr><th>Куплен</th><th>Кол-во</th><th>Прибыль, ₪</th></tr>${sim.gains.map((g) => {
          const lot = d.lots.find((l) => l.id === g.lotId);
          return `<tr><td>${core.fmtDate(lot.buyDate)}</td><td>${core.fmtQty(g.qty)}</td><td>${ils(g.taxable)}</td></tr>`;
        }).join('')}</table></div>`;
    } catch (ex) { out.innerHTML = `<div class="note bad">${esc(ex.message)}</div>`; }
  };
  field('sAsset').addEventListener('change', fillPlaces);
  field('sPlace').addEventListener('change', fillQty);
  ['sQty', 'sPrice', 'sFee'].forEach((id) => field(id).addEventListener('input', calc));
  fillPlaces();
}

// ---------- наследство США ----------

function viewEstate() {
  const d = state.data;
  const limit = core.num(d.settings.estateLimitUsd || 60000);
  const warn = core.num(d.settings.estateWarnUsd || 45000);
  const pos = core.valuePositions(d).filter((p) => p.priced && p.asset?.usSitus);
  const cards = d.owners.map((o) => {
    const by = {};
    for (const p of pos.filter((x) => x.owner === o.name)) by[p.place] = (by[p.place] || 0) + p.valueUsd;
    const st = core.estateStatus(by, limit, warn);
    const note = st.level === 'over' ? `<div class="note bad">🚨 Лимит ${usd(limit)} превышен на ${usd(st.overLimit)}.</div>`
      : st.level === 'warn' ? `<div class="note warn">⚠️ Рабочий порог ${usd(warn)} превышен на ${usd(st.overWarn)}.</div>`
        : `<div class="note ok">✅ Ниже рабочего порога ${usd(warn)}.</div>`;
    return `<div class="card"><div class="row"><strong>${esc(o.name)}</strong><span class="big" style="font-size:1.3rem">${usd(st.total)}</span></div>
      <div class="bar${st.level !== 'ok' ? ' over' : ''}"><i style="width:${Math.min(100, st.total / limit * 100).toFixed(1)}%"></i></div>
      <div class="muted">до лимита ${usd(Math.max(0, limit - st.total))}</div>${note}
      ${Object.entries(by).sort((a, b) => b[1] - a[1]).map(([name, v]) => `<div class="row"><span>${esc(name)}</span><span>${usd(v)}</span></div>`).join('')}
      ${st.plan.length ? `<p class="muted">Чтобы вернуться ниже порога, убрать из американских бумаг:</p>${st.plan.map(([n, v]) => `<div class="row"><span>${esc(n)}</span><span>${usd(v)}</span></div>`).join('')}` : ''}
    </div>`;
  }).join('');
  return `<h1>Наследство США</h1>
    <p class="muted">Американские бумаги (акции и ETF с домицилем в США). Ирландские UCITS, крипта и металлы не входят. Ориентировочно — сверить с консультантом.</p>${cards}`;
}

// ---------- правила ----------

function viewRules() {
  const d = state.data;
  const s = d.settings;
  const day = todayIso();
  const w = core.buyWindow(day, s.buyWindowAfter, s.buyWindowUntil);
  const st = core.windowState(Date.now(), w);
  const windowText = { open: `🟢 Окно открыто сейчас — до ${w.end}`, before: `🕒 Сегодня окно: ${w.start}–${w.end}`,
    after: `Сегодняшнее окно (${w.start}–${w.end}) уже прошло`, closed: 'Сегодня выходной — рынок США закрыт' }[st];
  const ex = d.assets.filter((a) => a.nextEx && core.daysUntil(a.nextEx, day) >= 0)
    .sort((a, b) => a.nextEx.localeCompare(b.nextEx));
  const sma = d.assets.filter((a) => a.sma);
  const dca = s.dca || {};
  const coin = d.assets.find((a) => a.symbol === String(dca.asset || '').toUpperCase());
  const full = core.num(dca.full); const reduced = core.num(dca.reduced); const threshold = core.num(dca.threshold);
  let dcaHtml = '<p class="muted">Правило не настроено. Суммы и порог задаются на компьютере в разделе «Алгоритм Минкина».</p>';
  if (full > 0 && threshold > 0) {
    dcaHtml = coin?.price
      ? `<div class="row"><span>Покупка ${esc(coin.symbol)} в этом месяце</span><strong>${usd(core.dcaAmount(core.num(coin.price), threshold, full, reduced))}</strong></div>
         <p class="muted">${esc(coin.symbol)} = ${usd(core.num(coin.price))}, порог ${usd(threshold)}. Выше порога — ${usd(reduced)}, иначе — ${usd(full)}.</p>`
      : `<p class="muted">Нет цены ${esc(dca.asset)}.</p>`;
  }
  const held = new Set(d.lots.map((l) => String(l.assetId)));
  const black = d.assets.filter((a) => held.has(String(a.id)) && core.blacklistHit(a.symbol, s.blacklist));
  return `<h1>Правила</h1>
    <div class="card"><strong>Окно покупки (рынок США)</strong>
      <div class="note info">${windowText}. Открытие рынка — ${w.open} по Израилю.</div>
      <p class="muted">Через ${s.buyWindowAfter}–${s.buyWindowUntil} минут после открытия. Летнее и зимнее время учтены. Праздники биржи не учитываются.</p></div>
    <div class="card"><strong>X-day: когда не покупать</strong>
      ${ex.length ? ex.map((a) => { const left = core.daysUntil(a.nextEx, day); return `<div class="row"><span>${esc(a.symbol)} · ${core.fmtDate(a.nextEx)}${a.nextExEstimated ? ' (прогноз)' : ''}</span><span>${left <= (s.xdayDays ?? 2) ? '⛔ не покупать' : `через ${left} дн.`}</span></div>`; }).join('') : '<p class="muted">Нет данных. Они приходят с компьютера.</p>'}</div>
    <div class="card"><strong>Цена относительно SMA</strong>
      ${sma.length ? sma.map((a) => `<div class="row"><span>${esc(a.symbol)}</span><span>${a.sma.above ? '🟢 над' : '🔴 под'} · ${core.fmtPct(core.num(a.sma.distance), true)}</span></div>`).join('') + `<p class="muted">SMA ${sma[0].sma.window}, рассчитано на компьютере на ${core.fmtDate(sma[0].sma.asOf)}.</p>` : '<p class="muted">Нет данных. Они приходят с компьютера.</p>'}</div>
    <div class="card"><strong>Крипто-DCA с порогом</strong>${dcaHtml}</div>
    <div class="card"><strong>Чёрный список</strong>
      <p class="muted">${esc((s.blacklist || []).join(', ') || 'пуст')}</p>
      ${black.length ? `<div class="note bad">⛔ В портфеле: ${esc(black.map((a) => a.symbol).join(', '))}</div>` : ''}</div>`;
}

// ---------- цены вручную ----------

function viewPrices() {
  const d = state.data;
  const held = new Set(d.lots.map((l) => String(l.assetId)));
  const list = d.assets.filter((a) => held.has(String(a.id)) && a.priceSource !== 'cash');
  if (!list.length) return '<h1>Цены вручную</h1><p class="muted">Активов пока нет.</p>';
  return `<h1>Цены вручную</h1>
    <p class="muted">Для акций и ETF цена приходит с компьютера. Здесь её можно поправить до следующей синхронизации. Крипта и металлы обновляются сами.</p>
    <form id="priceForm" class="card">${list.map((a) => `
      <label for="pr_${esc(a.id)}">${esc(a.symbol)}, ${esc(a.currency)}${a.cls === 'metal' ? ' за грамм' : ''}
        <span class="muted">· ${a.priceAsOf ? `на ${core.fmtDate(a.priceAsOf)}` : 'нет цены'}${a.priceSrc ? `, ${esc(a.priceSrc)}` : ''}</span></label>
      <input id="pr_${esc(a.id)}" data-asset="${esc(a.id)}" inputmode="decimal" value="${a.price ? esc(String(core.num(a.price))) : ''}">`).join('')}
      <button class="primary">Сохранить цены</button><p id="err" class="bad" role="alert"></p></form>`;
}

function bindPrices() {
  field('priceForm')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
      let changed = 0;
      for (const input of e.target.querySelectorAll('input[data-asset]')) {
        const asset = state.data.assets.find((a) => String(a.id) === input.dataset.asset);
        const raw = input.value.trim();
        if (raw === '') continue;
        const n = core.parseNumber(raw);
        if (!Number.isFinite(n) || n < 0) throw new Error(`${asset.symbol}: введите число`);
        if (n !== core.num(asset.price)) { Object.assign(asset, { price: String(n), priceAsOf: todayIso(), priceSrc: 'вручную на телефоне' }); changed += 1; }
      }
      await save();
      toast(changed ? `Обновлено цен: ${changed}` : 'Изменений нет');
      go('#/');
    } catch (ex) { field('err').textContent = ex.message; }
  });
}

// ---------- обмен с компьютером ----------

function viewSync() {
  const pending = state.data.lots.filter((l) => l.phone);
  return `<h1>Обмен с компьютером</h1>
    <div class="card"><strong>1. Загрузить данные с компьютера</strong>
      <p class="muted">На компьютере: «Настройки → Телефон → Создать файл для телефона». Перенесите файл на телефон (Google Drive, кабель, почта себе) и выберите его здесь.</p>
      <label for="inFile">Файл .finphone</label><input id="inFile" type="file" accept=".finphone,application/json,application/octet-stream">
      <label for="inPass">Парольная фраза файла</label><input id="inPass" type="password" autocomplete="off">
      <button id="inGo" class="primary">Загрузить</button><p id="inErr" class="bad" role="alert"></p></div>
    <div class="card"><strong>2. Отправить покупки на компьютер</strong>
      <p class="muted">Записано на телефоне и ещё не подтверждено компьютером: <strong>${pending.length}</strong>.</p>
      <label for="outPass">Парольная фраза для файла (не короче ${MIN_PASS} символов)</label><input id="outPass" type="password" autocomplete="off">
      <button id="outGo" class="primary"${pending.length ? '' : ' disabled'}>Создать файл для компьютера</button><p id="outErr" class="bad" role="alert"></p>
      <p class="muted">На компьютере: «Настройки → Телефон → Принять покупки с телефона». После следующей загрузки данных с компьютера принятые покупки перестанут числиться «с телефона».</p></div>
    ${freshness()}
    <p class="muted">Файлы зашифрованы (AES-256). Без парольной фразы их не прочитать.</p>`;
}

function download(name, text) {
  const blob = new Blob([text], { type: 'application/octet-stream' });
  const url = URL.createObjectURL(blob);
  const a = Object.assign(document.createElement('a'), { href: url, download: name });
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

function bindSync() {
  field('inGo').addEventListener('click', async () => {
    const err = field('inErr');
    const file = field('inFile').files[0];
    if (!file) { err.textContent = 'Выберите файл'; return; }
    err.textContent = 'Расшифровываю…';
    try {
      const snapshot = await decryptJson(await file.text(), field('inPass').value);
      if (snapshot.kind !== 'snapshot') throw new Error('В файле нет данных с компьютера. Нужен файл, созданный на компьютере для телефона.');
      const merged = core.mergeSnapshot(state.data, snapshot, nowIso());
      merged.data.meta.liveAt = null;
      state.data = merged.data;
      await save();
      toast(`Загружено. Лотов: ${snapshot.lots.length}${merged.kept ? `, ждут отправки с телефона: ${merged.kept}` : ''}`);
      refreshLive(true);
      go('#/');
    } catch (ex) { err.textContent = ex.message; }
  });
  field('outGo').addEventListener('click', async () => {
    const err = field('outErr');
    const pass = field('outPass').value;
    if (pass.length < MIN_PASS) { err.textContent = `Фраза должна быть не короче ${MIN_PASS} символов`; return; }
    err.textContent = '';
    const payload = core.buildPhoneExport(state.data, nowIso());
    download(`s-telefona-${todayIso()}.finphone`, await encryptJson(payload, pass));
    toast(`Файл создан: покупок ${payload.lots.length}`);
  });
}

// ---------- ещё ----------

function viewMore() {
  const pending = state.data.lots.filter((l) => l.phone).length;
  return `<h1>Ещё</h1>
    <div class="card menu">
      <a href="#/sell"><span>🧾 Если продам — налог и сумма на руки</span><span>›</span></a>
      <a href="#/estate"><span>🇺🇸 Наследство США</span><span>›</span></a>
      <a href="#/rules"><span>🧭 Правила: окно покупки, X-day, SMA, DCA</span><span>›</span></a>
      <a href="#/prices"><span>✏️ Цены вручную</span><span>›</span></a>
      <a href="#/sync"><span>🔁 Обмен с компьютером${pending ? `<span class="tag">${pending} к отправке</span>` : ''}</span><span>›</span></a>
      <a href="#/settings"><span>⚙️ Пароль и данные</span><span>›</span></a>
    </div>
    <p class="foot">Налоги, импорт отчётов брокера, дивиденды, пенсия, сравнение и сценарии — в полной версии на компьютере.</p>`;
}

function viewSettings() {
  return `<h1>Пароль и данные</h1>
    <form id="pwForm" class="card"><strong>Сменить пароль</strong>
      <label for="np1">Новый пароль</label><input id="np1" type="password" autocomplete="new-password">
      <label for="np2">Повторите</label><input id="np2" type="password" autocomplete="new-password">
      <button class="primary">Сменить пароль</button><p id="pwErr" class="bad" role="alert"></p></form>
    <div class="card"><strong>Выйти</strong><p class="muted">Закрывает данные до следующего ввода пароля.</p><button id="lock" class="wide">Выйти</button></div>
    <div class="card"><strong>Стереть данные на телефоне</strong>
      <p class="muted">Удаляет всё, включая покупки, не отправленные на компьютер. Данные на компьютере не затрагиваются.</p>
      <button id="wipe" class="danger wide">Стереть</button></div>
    <p class="muted">Данные хранятся только в памяти этого телефона, зашифрованные вашим паролем (AES-256). В интернет уходят только
    запросы цен: названия монет и коды металлов — в CoinGecko и gold-api.com. Количества, суммы и названия счетов не передаются.
    Если очистить данные браузера или удалить приложение — данные на телефоне пропадут; на компьютере они останутся.</p>`;
}

function bindSettings() {
  field('pwForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const p1 = field('np1').value;
    if (p1.length < MIN_PASS) { field('pwErr').textContent = `Пароль должен быть не короче ${MIN_PASS} символов`; return; }
    if (p1 !== field('np2').value) { field('pwErr').textContent = 'Пароли не совпадают'; return; }
    state.vault = await createVault(p1);
    await save();
    toast('Пароль изменён');
    go('#/more');
  });
  field('lock').addEventListener('click', () => { state.vault = null; state.data = null; location.hash = ''; renderLock(); });
  field('wipe').addEventListener('click', () => {
    if (confirm('Стереть все данные приложения на этом телефоне? Отменить это нельзя.')) {
      localStorage.removeItem(STORE_KEY);
      state.vault = null; state.data = null; location.hash = '';
      renderLock();
    }
  });
}

// ---------- маршрутизация ----------

const ROUTES = {
  '#/': [viewHome, () => {
    bindOwner();
    document.querySelectorAll('[data-view]').forEach((b) => b.addEventListener('click', () => { state.view = b.dataset.view; render(); }));
    field('refresh')?.addEventListener('click', () => { toast('Обновляю…'); refreshLive(); });
  }],
  '#/positions': [viewPositions, () => {
    bindOwner();
    document.querySelectorAll('[data-del-lot]').forEach((b) => b.addEventListener('click', async () => {
      if (!confirm('Удалить эту запись с телефона?')) return;
      state.data.lots = state.data.lots.filter((l) => String(l.id) !== b.dataset.delLot);
      await save();
      render();
    }));
  }],
  '#/places': [viewPlaces, () => {}],
  '#/add': [viewAdd, bindAdd],
  '#/more': [viewMore, () => {}],
  '#/sell': [viewSell, bindSell],
  '#/estate': [viewEstate, () => {}],
  '#/rules': [viewRules, () => {}],
  '#/prices': [viewPrices, bindPrices],
  '#/sync': [viewSync, bindSync],
  '#/settings': [viewSettings, bindSettings],
};
const TABS = [['#/', '🏠', 'Капитал'], ['#/positions', '📊', 'Позиции'], ['#/places', '🏦', 'Места'], ['#/add', '➕', 'Добавить'], ['#/more', '☰', 'Ещё']];

function render() {
  if (!state.data) { renderLock(); return; }
  const hash = ROUTES[location.hash] ? location.hash : '#/';
  const [view, bind] = ROUTES[hash];
  const tab = TABS.some(([h]) => h === hash) ? hash : '#/more';
  nav.innerHTML = TABS.map(([h, icon, label]) => `<a href="${h}"${h === tab ? ' aria-current="page"' : ''}><span aria-hidden="true">${icon}</span>${label}</a>`).join('');
  app.innerHTML = view();
  bind();
  window.scrollTo(0, 0);
}

window.addEventListener('hashchange', render);
if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js').catch(() => {});
renderLock();
