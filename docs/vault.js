// Шифрование данных на телефоне и файлов обмена: AES-256-GCM, ключ PBKDF2-HMAC-SHA256.
// Формат совпадает с app/services/phone_sync.py на компьютере.

export const FORMAT = 'fin-phone';
const ITERATIONS = 310000;
const enc = new TextEncoder();
const dec = new TextDecoder();

const b64 = (bytes) => {
  let s = '';
  const arr = new Uint8Array(bytes);
  for (let i = 0; i < arr.length; i += 0x8000) s += String.fromCharCode(...arr.subarray(i, i + 0x8000));
  return btoa(s);
};
const unb64 = (text) => Uint8Array.from(atob(text), (c) => c.charCodeAt(0));

async function deriveKey(passphrase, salt, iterations) {
  const material = await crypto.subtle.importKey('raw', enc.encode(passphrase), 'PBKDF2', false, ['deriveKey']);
  return crypto.subtle.deriveKey({ name: 'PBKDF2', hash: 'SHA-256', salt, iterations }, material,
    { name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt']);
}

export async function encryptJson(payload, passphrase) {
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const key = await deriveKey(passphrase, salt, ITERATIONS);
  const sealed = await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, key, enc.encode(JSON.stringify(payload)));
  return JSON.stringify({ format: FORMAT, v: 1, iter: ITERATIONS, salt: b64(salt), iv: b64(iv), data: b64(sealed) });
}

export async function decryptJson(text, passphrase) {
  let envelope;
  try {
    envelope = JSON.parse(text);
    if (envelope.format !== FORMAT) throw new Error('format');
  } catch {
    throw new Error('Это не файл обмена этого приложения или он повреждён');
  }
  const iterations = Number(envelope.iter);
  if (!(iterations >= 10000 && iterations <= 5000000)) throw new Error('Файл повреждён');
  try {
    const key = await deriveKey(passphrase, unb64(envelope.salt), iterations);
    const plain = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: unb64(envelope.iv) }, key, unb64(envelope.data));
    return JSON.parse(dec.decode(plain));
  } catch {
    throw new Error('Неверная парольная фраза или файл повреждён');
  }
}

// ---------- хранилище на телефоне: ключ выводится один раз за сеанс ----------

export async function createVault(passphrase) {
  const salt = crypto.getRandomValues(new Uint8Array(16));
  return { key: await deriveKey(passphrase, salt, ITERATIONS), salt };
}

export async function openVault(text, passphrase) {
  let envelope;
  try {
    envelope = JSON.parse(text);
    if (envelope.format !== FORMAT) throw new Error('format');
  } catch {
    throw new Error('Сохранённые данные повреждены');
  }
  const salt = unb64(envelope.salt);
  const key = await deriveKey(passphrase, salt, Number(envelope.iter));
  try {
    const plain = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: unb64(envelope.iv) }, key, unb64(envelope.data));
    return { key, salt, data: JSON.parse(dec.decode(plain)) };
  } catch {
    throw new Error('Неверный пароль');
  }
}

export async function sealVault(vault, payload) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const sealed = await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, vault.key, enc.encode(JSON.stringify(payload)));
  return JSON.stringify({ format: FORMAT, v: 1, iter: ITERATIONS, salt: b64(vault.salt), iv: b64(iv), data: b64(sealed) });
}
