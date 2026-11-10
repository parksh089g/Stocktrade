// 서비스 워커: 웹 푸시 알림을 띄우고, 알림을 누르면 그 실행 기록을 연다.
// 화면 파일은 캐시해 두어 인터넷이 안 될 때 마지막 화면이라도 연다(기록 데이터는 캐시하지 않는다).
// 엔진이 보내는 내용: {"title", "body", "url": "/runs/123", "urgent": bool} (trading/notify/webpush.py)

const CACHE = "stocktrade-v1";
// 이 PC에서 개발할 때는 캐시하지 않는다(화면이 바로 바뀌도록)
const LOCAL = ["localhost", "127.0.0.1"].includes(self.location.hostname);

self.addEventListener("install", () => self.skipWaiting());

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      for (const key of await caches.keys()) if (key !== CACHE) await caches.delete(key);
      await self.clients.claim();
    })(),
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (LOCAL || req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return; // Supabase 요청은 건드리지 않는다

  if (req.mode === "navigate") {
    // 화면은 늘 새로 받고, 인터넷이 안 될 때만 마지막에 받은 화면을 쓴다
    event.respondWith(
      (async () => {
        try {
          const res = await fetch(req);
          if (res.ok) (await caches.open(CACHE)).put("/", res.clone());
          return res;
        } catch {
          return (await caches.match("/")) ?? Response.error();
        }
      })(),
    );
    return;
  }

  if (url.pathname.startsWith("/assets/") || url.pathname.startsWith("/icons/")) {
    // 빌드 파일은 이름에 내용 해시가 들어 있어 한 번 받으면 바뀌지 않는다
    event.respondWith(
      (async () => {
        const cached = await caches.match(req);
        if (cached) return cached;
        const res = await fetch(req);
        if (res.ok) (await caches.open(CACHE)).put(req, res.clone());
        return res;
      })(),
    );
  }
});

/** 같은 사이트 안의 주소만 연다 */
function safePath(raw) {
  try {
    const url = new URL(typeof raw === "string" ? raw : "/", self.location.origin);
    return url.origin === self.location.origin ? url.pathname + url.search : "/";
  } catch {
    return "/";
  }
}

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = { body: event.data ? event.data.text() : "" };
  }
  const options = {
    body: data.body || "",
    icon: "/icons/icon-192.png",
    badge: "/icons/badge-96.png",
    lang: "ko",
    data: { url: safePath(data.url) },
  };
  if (data.urgent) {
    options.requireInteraction = true;
    options.vibrate = [200, 100, 200];
  }
  event.waitUntil(self.registration.showNotification(data.title || "주식 자동매매", options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const path = safePath(event.notification.data && event.notification.data.url);
  event.waitUntil(
    (async () => {
      const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      const open = windows.find((c) => new URL(c.url).origin === self.location.origin);
      if (open) {
        await open.focus();
        open.postMessage({ type: "navigate", url: path });
        return;
      }
      await self.clients.openWindow(path);
    })(),
  );
});
