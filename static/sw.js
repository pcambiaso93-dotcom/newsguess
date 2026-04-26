// Service worker per Newsguess (PWA)
// Strategia: cache-first per asset statici, network-first per API live
const CACHE = 'newsguess-v2';
const STATIC_ASSETS = [
  '/api/quiz',
  '/api/manifest.json',
  '/api/icon-192.png',
  '/api/icon-512.png',
  '/api/apple-touch-icon.png',
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(STATIC_ASSETS)).catch(()=>{}));
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then(keys => Promise.all(
      keys.filter(k => k !== CACHE).map(k => caches.delete(k))
    ))
  );
  self.clients.claim();
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  // Solo same-origin; lascia passare il resto
  if (url.origin !== self.location.origin) return;

  // Le API live vanno sempre alla rete (fallback cache solo per /api/quiz se offline)
  if (url.pathname.startsWith('/api/extract-headlines') || url.pathname.startsWith('/api/frontpage')) {
    return; // default network
  }

  // App shell: cache-first con aggiornamento in background
  if (STATIC_ASSETS.includes(url.pathname)) {
    e.respondWith(
      caches.match(e.request).then(hit => {
        const fetchPromise = fetch(e.request).then(res => {
          if (res && res.ok) {
            const copy = res.clone();
            caches.open(CACHE).then(c => c.put(e.request, copy));
          }
          return res;
        }).catch(() => hit);
        return hit || fetchPromise;
      })
    );
  }
});

// === Push notifications ===
self.addEventListener('push', (event) => {
  let data = { title: 'Newsguess', body: 'Nuova sfida disponibile', url: '/api/quiz' };
  try { if (event.data) data = { ...data, ...event.data.json() }; } catch {}
  event.waitUntil(self.registration.showNotification(data.title, {
    body: data.body,
    icon: '/api/icon-192.png',
    badge: '/api/icon-192.png',
    data: { url: data.url || '/api/quiz' },
    tag: 'newsguess-daily',
  }));
});
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = event.notification.data?.url || '/api/quiz';
  event.waitUntil((async () => {
    const cls = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const c of cls) { if ('focus' in c) { c.navigate(url); return c.focus(); } }
    if (self.clients.openWindow) await self.clients.openWindow(url);
  })());
});
