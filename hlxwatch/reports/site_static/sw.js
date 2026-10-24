// HLX Watch - service worker: riceve le notifiche push e le mostra come notifiche di sistema.
// Non salva pagine in cache: il sito resta sempre quello aggiornato.

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (err) {
    data = { body: event.data ? event.data.text() : "" };
  }
  event.waitUntil(self.registration.showNotification(data.title || "HLX Watch", {
    body: data.body || "Nuove scoperte nei dati dei giochi Valve.",
    icon: "icon-192.png",
    badge: "badge-96.png",
    tag: data.tag || "hlx-watch",
    renotify: true,
    data: { url: data.url || "./" },
  }));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = new URL(event.notification.data?.url || "./", self.registration.scope).href;
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const client of windows) {
      if (client.url.startsWith(self.registration.scope) && "focus" in client) {
        await client.focus();
        return client.navigate ? client.navigate(url) : undefined;
      }
    }
    return self.clients.openWindow(url);
  })());
});
