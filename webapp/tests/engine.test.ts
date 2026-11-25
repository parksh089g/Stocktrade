import { describe, expect, it } from "vitest";
import { missingSources, secretProblem, settingsProblems } from "../src/lib/engine";
import type { EngineSettingsInput, SecretName } from "../src/lib/types";

const base: EngineSettingsInput = {
  llm_provider: "anthropic",
  llm_model: "",
  llm_summary: true,
  llm_screen: "off",
  sec_user_agent: "",
};
const keys = (...names: SecretName[]) => new Set<SecretName>(names);

describe("비밀값 입력 확인", () => {
  it("Anthropic 키는 sk-ant- 로 시작하고 빈칸이 없어야 한다", () => {
    expect(secretProblem("llm_api_key", " sk-ant-api03-abc ")).toBeNull();
    expect(secretProblem("llm_api_key", "sk-proj-abc")).toMatch("sk-ant-");
    expect(secretProblem("llm_api_key", "sk-ant- abc")).toMatch("빈칸");
    expect(secretProblem("llm_api_key", "   ")).toMatch("넣어");
  });

  it("OpenDART 인증키는 40자리", () => {
    expect(secretProblem("dart_api_key", "a".repeat(40))).toBeNull();
    expect(secretProblem("dart_api_key", "a".repeat(39))).toMatch("40");
  });
});

describe("설정 저장 전 확인", () => {
  it("Claude를 켜려면 키가 먼저 있어야 한다", () => {
    expect(settingsProblems(base, keys())).toContain("Anthropic API 키를 먼저 넣어 주세요");
    expect(settingsProblems(base, keys("llm_api_key"))).toEqual([]);
    expect(settingsProblems({ ...base, llm_provider: "none" }, keys())).toEqual([]);
  });

  it("모델 이름과 SEC 연락처 형식", () => {
    const k = keys("llm_api_key");
    expect(settingsProblems({ ...base, llm_model: "claude-haiku-5-5" }, k)).toEqual([]);
    expect(settingsProblems({ ...base, llm_model: "gpt-5" }, k)).toHaveLength(1);
    expect(settingsProblems({ ...base, sec_user_agent: "stocktrade" }, k)[0]).toMatch("이메일");
  });

  it("악재 판정을 켜면 공시 출처가 없는 시장을 알려 준다", () => {
    const s = { ...base, llm_screen: "shadow" as const };
    expect(missingSources(s, keys("llm_api_key"))).toEqual(["국내(OpenDART 인증키 없음)", "미국(SEC 연락처 없음)"]);
    expect(missingSources({ ...s, sec_user_agent: "st a@b.c" }, keys("dart_api_key"))).toEqual([]);
    expect(missingSources(base, keys())).toEqual([]);
  });
});
