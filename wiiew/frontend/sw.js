/**
 * Wiiew Service Worker
 * Handles offline shell caching and calm W3C Web Push Notifications.
 * Path-agnostic: supports root deployment and GitHub Pages (/Wiiew/).
 */

const CACHE_NAME = 'wiiew-v2';
const ASSETS = [
  './',
  './index.html',
  './style.css',
  './app.js',
  './manifest.json',
  './icons/icon.svg',
  './icons/icon-192.png',
  './icons/icon-512.png',
  './icons/badge-72.png'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(ASSETS).catch((err) => {
        console.warn('[SW] Cache prefetch warning:', err);
      });
    })
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
      );
    })
  );
  self.clients.claim();
});

// Network-first with cache fallback
self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;
  const url = new URL(event.request.url);
  if (url.pathname.includes('/api/') || url.pathname.includes('/ws/')) return;

  event.respondWith(
    fetch(event.request)
      .then((response) => {
        if (response && response.status === 200) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
        }
        return response;
      })
      .catch(() => caches.match(event.request))
  );
});

// Calm Web Push Notification Event
self.addEventListener('push', (event) => {
  let data = {
    title: 'Wiiew',
    body: 'Someone has entered your room.',
    tag: 'wiiew-room-entry',
    data: { url: './' }
  };

  if (event.data) {
    try {
      data = event.data.json();
    } catch (e) {
      data.body = event.data.text();
    }
  }

  const options = {
    body: data.body || 'Someone has entered your room.',
    icon: data.icon || './icons/icon-192.png',
    badge: data.badge || './icons/badge-72.png',
    tag: data.tag || 'wiiew-room-entry',
    renotify: false,
    requireInteraction: false,
    vibrate: data.vibrate || [200, 100, 200],
    data: data.data || { url: './' },
    actions: [
      { action: 'open', title: 'Open Wiiew' },
      { action: 'dismiss', title: 'Dismiss' }
    ]
  };

  event.waitUntil(
    self.registration.showNotification(data.title || 'Wiiew', options)
  );
});

// Notification Click Handler (deep-links back to Wiiew)
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  if (event.action === 'dismiss') return;

  const targetPath = (event.notification.data && event.notification.data.url) || './';

  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windowClients) => {
      for (let client of windowClients) {
        if (client.url.includes(self.location.origin) && 'focus' in client) {
          return client.focus();
        }
      }
      if (clients.openWindow) {
        return clients.openWindow(new URL(targetPath, self.location.href).href);
      }
    })
  );
});
