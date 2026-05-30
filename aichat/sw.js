const CACHE = 'chyunx-aichat-v1';
const STATIC = ['/aichat/index.html', '/aichat/manifest.json'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(STATIC)));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', e => {
  if (e.request.url.includes('/v1/')) return; // API 请求不走缓存
  e.respondWith(
    caches.match(e.request).then(cached => cached || fetch(e.request))
  );
});

// Web Push
self.addEventListener('push', e => {
  const d = e.data?.json() ?? {};
  e.waitUntil(
    self.registration.showNotification(d.title ?? '新消息', {
      body:    d.body ?? '',
      icon:    '../icon-192.png',
      badge:   '../icon-192.png',
      tag:     'chat',
      renotify: true,
      vibrate: [200, 100, 200],
    })
  );
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  e.waitUntil(clients.openWindow('/aichat/index.html'));
});
