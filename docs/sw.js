/* Telar · service worker
   Red primero para todo lo propio (siempre la versión más nueva si hay señal);
   si no hay conexión, sirve la última copia guardada. Lo externo no se toca.
   El registro (data/registro.json) no se precarga: puede no existir en una
   publicación estática, y un 404 aquí abortaría la instalación entera. */
const CACHE = "telar-app-v1";
const BASE = [
  "./", "index.html", "manifest.webmanifest", "data/world.json",
  "icons/icon-192.png", "icons/icon-512.png"
];
self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(BASE)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys()
    .then(ks => Promise.all(ks.filter(k => k.startsWith("telar-app-") && k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;
  e.respondWith((async () => {
    const cache = await caches.open(CACHE);
    try {
      const res = await fetch(req, {cache: "no-cache"});
      if (res.ok) cache.put(req, res.clone());
      return res;
    } catch (err) {
      const hit = await cache.match(req, {ignoreSearch: true});
      if (hit) return hit;
      if (req.mode === "navigate") return cache.match("index.html");
      throw err;
    }
  })());
});
