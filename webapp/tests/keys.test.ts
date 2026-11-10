import { describe, expect, it } from "vitest";
import { isAdminKey } from "../src/data";
import { keyBytes } from "../src/lib/push";

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
});
