/* Service Worker der Fitness-App: Offline-Modus.
 *
 * Immer zuerst das Netz – ist der Pi erreichbar, kommt der aktuelle Stand (auch direkt nach
 * einem Deploy). Antwortet er nicht, liefert der Worker die zuletzt geladene Fassung aus dem
 * Cache und markiert sie mit dem Header X-Offline-Cache (Zeitpunkt des Stands); die App zeigt
 * dann einen Hinweis. Aendernde Anfragen (POST/PATCH/DELETE) laufen unveraendert durch.
 *
 * __VERSION__ und __ASSETS__ setzt der Server beim Ausliefern von /sw.js ein. */
const VERSION = "__VERSION__";
const ASSETS = __ASSETS__;
const SHELL = `shell-${VERSION}`;
const API = "api-v1"; // Daten ueberleben App-Updates
const NETWORK_TIMEOUT_MS = 6000;
const OFF_PATHS = /^\/api\/foods\/(search$|portion\/|barcode\/)/;
// Ohne Netz ohnehin nutzlos (Assistent speichert auf dem Pi) – nicht abfangen, nicht cachen.
const NO_CACHE = /^\/api\/(wizard|export|planner\/(suggest|create|pain|new-block))\b/;
const KEEP_DAYS = 14;   // so weit zurueck bleiben Tagesansichten (nutrition/day?date=) im Cache

self.addEventListener("install", (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(SHELL);
    // Einzeln statt addAll: eine fehlende Datei soll nicht die ganze Installation kippen
    await Promise.all(ASSETS.map((url) => cache.add(new Request(url, { cache: "reload" })).catch(() => {})));
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    for (const key of await caches.keys()) {
      if (key.startsWith("shell-") && key !== SHELL) await caches.delete(key);
    }
    await pruneApiCache();
    await self.clients.claim();
  })());
});

/** Der API-Cache wuchs ohne Grenze: jede Suche, jeder angesehene Tag. Beim Update
 *  aufraeumen – alte Tagesansichten und alles, was inzwischen gar nicht mehr gecacht wird. */
async function pruneApiCache() {
  const cache = await caches.open(API);
  const cutoff = new Date(Date.now() - KEEP_DAYS * 86400000).toISOString().slice(0, 10);
  for (const req of await cache.keys()) {
    const url = new URL(req.url);
    const day = url.searchParams.get("date");
    const stale = (day && day < cutoff) || OFF_PATHS.test(url.pathname) || NO_CACHE.test(url.pathname);
    if (stale) await cache.delete(req);
  }
}

/** Netz mit Zeitlimit; bei Erfolg Kopie in den Cache. Das Netz laeuft nach dem Zeitlimit weiter
 *  und aktualisiert den Cache trotzdem, falls es doch noch antwortet. */
function fromNetwork(request, cacheName, stamp) {
  const network = fetch(request).then(async (response) => {
    if (response.ok) {
      const cache = await caches.open(cacheName);
      const copy = stamp ? await withHeader(response.clone(), "X-Cached-At", new Date().toISOString()) : response.clone();
      await cache.put(request, copy);
    }
    return response;
  });
  const timeout = new Promise((_, reject) => setTimeout(() => reject(new Error("timeout")), NETWORK_TIMEOUT_MS));
  return { race: Promise.race([network, timeout]), network };
}

async function withHeader(response, name, value) {
  const headers = new Headers(response.headers);
  headers.set(name, value);
  return new Response(await response.blob(), { status: response.status, statusText: response.statusText, headers });
}

async function handleApi(request, race) {
  try {
    return await race;
  } catch {
    const cached = await (await caches.open(API)).match(request);
    if (cached) return withHeader(cached, "X-Offline-Cache", cached.headers.get("X-Cached-At") || "");
    return new Response(JSON.stringify({ detail: "Offline – noch nicht geladen." }), {
      status: 503, headers: { "Content-Type": "application/json", "X-Offline": "1" },
    });
  }
}

async function handleShell(request, race) {
  try {
    return await race;
  } catch {
    const cache = await caches.open(SHELL);
    const cached = await cache.match(request, { ignoreSearch: true })
      // Seitenaufruf mit Hash/Query: immer die App-Seite
      || (request.mode === "navigate" ? await cache.match("/") : undefined);
    return cached || Response.error();
  }
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin) return;
  if (url.pathname === "/sw.js") return;
  // Open Food Facts braucht ohnehin Netz und darf serverseitig laenger als NETWORK_TIMEOUT_MS
  // brauchen – sonst meldet die App "offline", obwohl der Pi nur auf OFF wartet.
  if (OFF_PATHS.test(url.pathname) || NO_CACHE.test(url.pathname)) return;
  const isApi = url.pathname.startsWith("/api/");
  const { race, network } = fromNetwork(request, isApi ? API : SHELL, isApi);
  // waitUntil: der Browser darf den Worker nicht beenden, bevor die Kopie im Cache liegt –
  // sonst faehrt man mit einem halb gefuellten Vorrat ins Fitnessstudio.
  event.waitUntil(network.catch(() => {}));
  event.respondWith(isApi ? handleApi(request, race) : handleShell(request, race));
});
