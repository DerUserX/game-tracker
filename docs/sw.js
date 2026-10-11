// Service Worker: App-Shell offline verfügbar, Cover-Bilder gecacht.
// Die Spieleliste selbst (Gist) wird immer frisch geladen – Offline-Fallback übernimmt localStorage in index.html.
const CACHE = 'gametracker-v2';
const SHELL = ['./', 'index.html', 'manifest.json', 'icon-192.png', 'icon-512.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET') return;

  // Cover: einmal laden, danach aus dem Cache
  if (url.hostname === 'images.igdb.com') {
    e.respondWith(caches.open(CACHE).then(async cache => {
      const hit = await cache.match(e.request);
      if (hit) return hit;
      const res = await fetch(e.request);
      if (res.ok || res.type === 'opaque') await cache.put(e.request, res.clone());
      return res;
    }));
    return;
  }

  // App-Shell: Netzwerk zuerst (für Updates), bei Offline aus dem Cache
  if (url.origin === location.origin && !url.searchParams.has('t')) {
    e.respondWith(
      fetch(e.request)
        .then(res => {
          const copy = res.clone();
          caches.open(CACHE).then(c => c.put(e.request, copy));
          return res;
        })
        .catch(() => caches.match(e.request).then(hit => hit || caches.match('index.html')))
    );
  }
});
