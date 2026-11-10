// 이 기기의 웹 푸시 구독. 구독 정보는 Supabase push_subscriptions에 넣고, 엔진이 읽어서 보낸다.
import type { DataSource } from "../data/source";

export type PushState = "unsupported" | "no-key" | "denied" | "off" | "on";

export function vapidPublicKey(): string {
  return import.meta.env.VITE_VAPID_PUBLIC_KEY ?? "";
}

export function pushSupported(): boolean {
  return "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
}

/** base64url 공개키 → 바이트 */
export function keyBytes(base64url: string): Uint8Array<ArrayBuffer> {
  const pad = "=".repeat((4 - (base64url.length % 4)) % 4);
  const raw = atob((base64url + pad).replace(/-/g, "+").replace(/_/g, "/"));
  const out = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

function sameKey(a: ArrayBuffer | null | undefined, b: Uint8Array): boolean {
  if (!a) return false;
  const x = new Uint8Array(a);
  return x.length === b.length && x.every((v, i) => v === b[i]);
}

async function currentSubscription(): Promise<PushSubscription | null> {
  const reg = await navigator.serviceWorker.ready;
  return reg.pushManager.getSubscription();
}

export async function pushState(): Promise<PushState> {
  if (!pushSupported()) return "unsupported";
  if (!vapidPublicKey()) return "no-key";
  if (Notification.permission === "denied") return "denied";
  return (await currentSubscription()) ? "on" : "off";
}

/** 알림을 켠다. 알림 권한을 묻고, 구독을 만들어 저장한다. */
export async function enablePush(source: DataSource): Promise<void> {
  const key = keyBytes(vapidPublicKey());
  const permission = await Notification.requestPermission();
  if (permission !== "granted") throw new Error("알림 권한을 허용해야 켤 수 있습니다");
  const reg = await navigator.serviceWorker.ready;
  let sub = await reg.pushManager.getSubscription();
  if (sub && !sameKey(sub.options.applicationServerKey, key)) {
    // 웹 푸시 키가 바뀌었다: 예전 구독으로는 받을 수 없다
    await source.deletePushSubscription(sub.endpoint).catch(() => {});
    await sub.unsubscribe();
    sub = null;
  }
  sub ??= await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
  const json = sub.toJSON();
  if (!json.endpoint || !json.keys?.p256dh || !json.keys.auth) throw new Error("구독 정보를 만들지 못했습니다");
  await source.savePushSubscription({ endpoint: json.endpoint, p256dh: json.keys.p256dh, auth: json.keys.auth });
}

/** 알림을 끈다. 저장한 구독을 지우고 이 기기의 구독도 없앤다. */
export async function disablePush(source: DataSource): Promise<void> {
  const sub = await currentSubscription();
  if (!sub) return;
  await source.deletePushSubscription(sub.endpoint);
  await sub.unsubscribe();
}
