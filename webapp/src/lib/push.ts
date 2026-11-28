// 이 기기의 웹 푸시 구독. 구독 정보는 Supabase push_subscriptions에 넣고, 엔진이 읽어서 보낸다.
import type { DataSource } from "../data/source";

export type PushState = "unsupported" | "no-key" | "denied" | "off" | "on";

/**
 * 알림 구독에 쓰는 공개키. 빌드에 넣은 값(VITE_VAPID_PUBLIC_KEY, GitHub Secret과 짝)이 먼저고,
 * 없으면 웹앱 설정 화면에서 만든 키(push_key 표, Vault의 개인키와 짝)를 쓴다. 엔진도 같은 순서다.
 */
export function vapidPublicKey(stored?: string | null): string {
  return import.meta.env.VITE_VAPID_PUBLIC_KEY || stored || "";
}

/**
 * 웹 푸시 키 한 쌍을 이 브라우저에서 만든다(P-256). 둘 다 base64url.
 * privateKey: 32바이트 개인키(엔진의 pywebpush가 그대로 받는 형식)
 * publicKey: 압축하지 않은 65바이트 공개키(구독의 applicationServerKey)
 */
export async function generatePushKeyPair(): Promise<{ privateKey: string; publicKey: string }> {
  const pair = await crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, true, ["sign"]);
  const jwk = await crypto.subtle.exportKey("jwk", pair.privateKey);
  if (!jwk.d || !jwk.x || !jwk.y) throw new Error("알림 키를 만들지 못했습니다");
  const x = keyBytes(jwk.x);
  const y = keyBytes(jwk.y);
  const pub = new Uint8Array(65);
  pub[0] = 4;
  pub.set(x, 1 + 32 - x.length);
  pub.set(y, 33 + 32 - y.length);
  return { privateKey: jwk.d, publicKey: base64url(pub) };
}

export function base64url(bytes: Uint8Array): string {
  let raw = "";
  for (const b of bytes) raw += String.fromCharCode(b);
  return btoa(raw).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
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

export async function pushState(stored?: string | null): Promise<PushState> {
  if (!pushSupported()) return "unsupported";
  if (!vapidPublicKey(stored)) return "no-key";
  if (Notification.permission === "denied") return "denied";
  return (await currentSubscription()) ? "on" : "off";
}

/** 알림을 켠다. 알림 권한을 묻고, 구독을 만들어 저장한다. */
export async function enablePush(source: DataSource, stored?: string | null): Promise<void> {
  const key = keyBytes(vapidPublicKey(stored));
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
