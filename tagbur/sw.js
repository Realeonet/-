// שירות עבודה: שומר את האפליקציה במכשיר כדי שתעבוד גם בלי אינטרנט.
// כשמעדכנים את הקבצים, מעלים את מספר הגרסה כדי שהמכשיר יטען את הגרסה החדשה.
const VERSION = 'tagbur-v5';
const FILES = [
  './',
  './index.html',
  './privacy.html',
  './words-1.js',
  './words-2.js',
  './words-3.js',
  './words-4.js',
  './words-5.js',
  './words-6.js',
  './words-7.js',
  './words-8.js',
  './manifest.webmanifest',
  './icon-192.png',
  './icon-512.png',
];

self.addEventListener('install', event => {
  event.waitUntil(caches.open(VERSION).then(cache => cache.addAll(FILES)));
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== VERSION).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

// קודם רשת (כדי לקבל עדכונים), ואם אין רשת - מהמטמון.
self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;
  event.respondWith(
    fetch(event.request)
      .then(response => {
        const copy = response.clone();
        caches.open(VERSION).then(cache => cache.put(event.request, copy));
        return response;
      })
      .catch(() => caches.match(event.request))
  );
});
