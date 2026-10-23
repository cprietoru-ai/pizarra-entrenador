// Modo sin conexión: guarda la app y sirve primero lo guardado; si hay red,
// se actualiza en segundo plano para la próxima vez que se abra.
const CACHE = "pizarra-v7";
const BASE = ["./", "index.html", "ejemplos.json", "manifest.webmanifest", "icons/icon-192.png", "icons/icon-512.png", "icons/icon-180.png"];
// cache: "reload" para no guardar una copia vieja de la caché del navegador
self.addEventListener("install", e => { e.waitUntil(caches.open(CACHE).then(c => c.addAll(BASE.map(u => new Request(u, { cache: "reload" }))))); self.skipWaiting(); });
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k.startsWith("pizarra-") && k !== CACHE).map(k => caches.delete(k)))));
  self.clients.claim();
});
self.addEventListener("fetch", e => {
  if (e.request.method !== "GET") return;
  // La página: primero la red, para que con cobertura se abra siempre la última versión
  if (e.request.mode === "navigate") {
    e.respondWith(caches.open(CACHE).then(c => fetch(e.request, { cache: "no-cache" }).then(r => { if (r.ok) c.put("index.html", r.clone()); return r; })
      .catch(() => c.match("index.html").then(g => g || c.match("./")))));
    return;
  }
  // Datos del equipo: primero la red (para tenerlos al día) y, sin cobertura, lo guardado
  if (new URL(e.request.url).pathname.includes("/equipos/")) {
    e.respondWith(caches.open(CACHE).then(c => fetch(e.request).then(r => { if (r.ok) c.put(e.request, r.clone()); return r; })
      .catch(() => c.match(e.request, { ignoreSearch: true }))));
    return;
  }
  e.respondWith(caches.open(CACHE).then(async c => {
    const guardado = await c.match(e.request);
    const red = fetch(e.request).then(r => { if (r && (r.ok || r.type === "opaque")) c.put(e.request, r.clone()); return r; }).catch(() => guardado);
    return guardado || red;
  }));
});
