/* Telar · service worker
   Red primero para todo lo propio (siempre la versión más nueva si hay señal);
   si no hay conexión, sirve la última copia guardada. Lo externo no se toca.
   Una sola visita con conexión deja guardados la app, el registro y el taller. */
const CACHE = "telar-app-v9";
const BASE = [
  "./", "index.html", "analisis.js", "investigador.js", "telar-open.js", "manifest.webmanifest",
  "data/registro.json", "data/evidencia.json", "data/identidad.json", "data/historial.json", "data/rectificaciones.json",
  "data/contacto.json", "data/world.json",
  "icons/icon-192.png", "icons/icon-512.png", "icons/apple-touch-icon.png"
];
self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE)
    .then(c => c.addAll(BASE.map(u => new Request(u, {cache: "reload"}))))
    .then(() => self.skipWaiting()));
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
