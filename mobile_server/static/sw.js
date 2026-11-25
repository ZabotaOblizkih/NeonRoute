/**
 * NeonRoute Mobile Service Worker
 * Network-First for app shell and static JS/CSS (prevents stale UI logic on phones),
 * Network-only for /api/* and /media/* streams/images.
 */

const CACHE_NAME = 'neonroute-shell-v6';

const PRECACHE_ASSETS = [
  '/',
  '/static/index.html',
  '/static/manifest.json',
  '/static/css/theme.css',
  '/static/css/layout.css',
  '/static/css/components.css',
  '/static/js/app.js',
  '/static/js/api.js',
  '/static/js/stream.js',
  '/static/js/ui.js',
  '/static/js/markdown.js',
  '/static/assets/icon-192.svg',
  '/static/assets/icon-512.svg'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(PRECACHE_ASSETS);
    }).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.map((key) => {
          if (key !== CACHE_NAME) {
            return caches.delete(key);
          }
        })
      );
    }).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // 1. API, media endpoints, and non-GET requests are strictly Network-only
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/media/') || event.request.method !== 'GET') {
    return;
  }

  // 2. Network-First for root navigation and static assets (offline cache fallback only)
  event.respondWith(
    fetch(event.request)
      .then((networkResponse) => {
        if (networkResponse && networkResponse.status === 200) {
          const responseToCache = networkResponse.clone();
          caches.open(CACHE_NAME).then((cache) => {
            cache.put(event.request, responseToCache);
          });
        }
        return networkResponse;
      })
      .catch(() => caches.match(event.request))
  );
});
