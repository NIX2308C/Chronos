/* Minimal app-shell service worker: on a dropped connection, reopening an
   already-visited page shows the last-cached shell instead of a blank tab.
   No offline data sync — chat/API calls always go to the network untouched.
   Bump VERSION when shell files change so old caches are dropped on deploy. */
const VERSION = "v1";
const SHELL_CACHE = "chronos-shell-" + VERSION;
const SHELL_URLS = [
  "/student", "/login", "/teacher", "/teacher-stats", "/status",
  "/theme.css", "/theme.js", "/transition.css", "/transition.js",
  "/settings.css", "/settings.js", "/mobile.css", "/mobile.js",
  "/auth.js", "/manifest.json"
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE)
      .then((cache) => cache.addAll(SHELL_URLS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== SHELL_CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  // Only ever intercept the app shell (pages + their static assets), never
  // /chat, /auth, /student/files, etc. — those must always hit the network live.
  if (req.mode !== "navigate" && !SHELL_URLS.includes(url.pathname)) return;

  event.respondWith(
    fetch(req)
      .then((res) => {
        const copy = res.clone();
        caches.open(SHELL_CACHE).then((cache) => cache.put(req, copy));
        return res;
      })
      .catch(() => caches.match(req).then((cached) => cached || caches.match("/login")))
  );
});
