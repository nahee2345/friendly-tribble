/*
 * Legacy Wealthy service-worker cleanup only.
 * The localhost application no longer installs a PWA or receives Web Push.
 * This file remains at the former URL so an already registered worker can
 * update once, remove only its own caches, and unregister itself.
 */
const LEGACY_CACHE_PREFIX = "wealthy-static-";

self.addEventListener("install", (event) => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((key) => key.startsWith(LEGACY_CACHE_PREFIX)).map((key) => caches.delete(key)));
    await self.registration.unregister();
  })());
});
