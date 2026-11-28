import { describe, expect, it } from "vitest";
import { isAdminKey } from "../src/data";
import { base64url, generatePushKeyPair, keyBytes } from "../src/lib/push";

// 시험은 Node에서 돈다. 웹앱 타입에는 Node 타입이 없어서 필요한 것만 적는다
interface Ecdh {
  setPrivateKey(key: string, encoding: "base64url"): void;
  getPublicKey(encoding: "base64url"): string;
}
const nodeCrypto = "node:crypto";
const { createECDH } = (await import(/* @vite-ignore */ nodeCrypto)) as { createECDH(curve: string): Ecdh };

function jwt(payload: object): string {
  const b64 = (o: object) => btoa(JSON.stringify(o)).replace(/=+$/, "").replace(/\+/g, "-").replace(/\//g, "_");
  return `${b64({ alg: "HS256", typ: "JWT" })}.${b64(payload)}.signature`;
}

describe("웹앱에 넣으면 안 되는 관리자 키를 알아본다", () => {
  it("새 형식: secret은 거부, publishable은 허용", () => {
    expect(isAdminKey("sb_secret_abc123")).toBe(true);
    expect(isAdminKey("sb_publishable_abc123")).toBe(false);
  });

  it("예전 형식(JWT): service_role은 거부, anon은 허용", () => {
    expect(isAdminKey(jwt({ role: "service_role", iss: "supabase" }))).toBe(true);
    expect(isAdminKey(jwt({ role: "anon", iss: "supabase" }))).toBe(false);
    expect(isAdminKey("a.%%%.c")).toBe(false);
  });
});

describe("웹 푸시 공개키", () => {
  it("base64url을 바이트로 바꾼다(65바이트 압축하지 않은 점)", () => {
    const raw = new Uint8Array(65).map((_, i) => i * 3);
    const b64url = btoa(String.fromCharCode(...raw)).replace(/=+$/, "").replace(/\+/g, "-").replace(/\//g, "_");
    expect([...keyBytes(b64url)]).toEqual([...raw]);
  });

  it("base64url로 되돌린다", () => {
    const raw = new Uint8Array(65).map((_, i) => 255 - i);
    expect([...keyBytes(base64url(raw))]).toEqual([...raw]);
  });

  it("웹앱에서 만든 키 한 쌍은 짝이 맞고 엔진이 받는 형식이다", async () => {
    for (let i = 0; i < 20; i++) {
      const { privateKey, publicKey } = await generatePushKeyPair();
      // 서버(set_push_key)가 받는 형식: 개인키 32바이트, 공개키 65바이트(0x04로 시작)
      expect(privateKey).toMatch(/^[A-Za-z0-9_-]{43}$/);
      expect(publicKey).toMatch(/^[A-Za-z0-9_-]{87}$/);
      expect(keyBytes(publicKey)[0]).toBe(4);
      // 개인키에서 계산한 공개키와 같아야 엔진이 보낸 알림을 이 기기가 받는다
      const ecdh = createECDH("prime256v1");
      ecdh.setPrivateKey(privateKey, "base64url");
      expect(ecdh.getPublicKey("base64url")).toBe(publicKey);
    }
  });
});
