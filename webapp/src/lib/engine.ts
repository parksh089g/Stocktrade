// 엔진 설정 화면의 입력 확인. 저장 전에 화면에서 먼저 막고, 데이터베이스(schema.sql)도 한 번 더 막는다.
import type { EngineSettingsInput, SecretName } from "./types";

export const DEFAULT_MODEL = "claude-opus-5-5";
export const CHEAP_MODEL = "claude-haiku-5-5";

export const SECRET_LABEL: Record<SecretName, string> = {
  llm_api_key: "Anthropic API 키",
  dart_api_key: "OpenDART 인증키",
};

/** 비밀값 입력 확인. 문제가 있으면 이유, 없으면 null. 빈 값은 '지우기'로 따로 다룬다. */
export function secretProblem(name: SecretName, value: string): string | null {
  const v = value.trim();
  if (!v) return "값을 넣어 주세요";
  if (/\s/.test(v)) return "중간에 빈칸이 있습니다. 키를 다시 복사해 주세요";
  if (v.length > 500) return "너무 깁니다";
  if (name === "llm_api_key" && !v.startsWith("sk-ant-")) return "Anthropic API 키는 sk-ant- 로 시작합니다";
  if (name === "dart_api_key" && !/^[0-9a-fA-F]{40}$/.test(v)) return "OpenDART 인증키는 40자리 영문·숫자입니다";
  return null;
}

/** 설정 저장 전 확인. 문제가 있으면 이유 목록. */
export function settingsProblems(s: EngineSettingsInput, keys: Set<SecretName>): string[] {
  const problems: string[] = [];
  const model = s.llm_model.trim();
  if (model && !/^claude-[a-z0-9.-]+$/.test(model)) problems.push("모델 이름은 claude- 로 시작합니다(예: claude-haiku-5-5)");
  if (s.sec_user_agent.length > 200) problems.push("SEC 연락처가 너무 깁니다");
  if (s.sec_user_agent.trim() && !s.sec_user_agent.includes("@"))
    problems.push("SEC 연락처에는 이메일 주소가 들어가야 합니다(예: stocktrade 이메일)");
  if (s.llm_provider === "anthropic" && !keys.has("llm_api_key")) problems.push("Anthropic API 키를 먼저 넣어 주세요");
  if (s.llm_screen === "shadow" && s.llm_provider === "none") problems.push("악재 판정은 LLM을 켜야 쓸 수 있습니다");
  return problems;
}

/** 악재 판정을 켰을 때 공시를 못 보는 시장(경고용) */
export function missingSources(s: EngineSettingsInput, keys: Set<SecretName>): string[] {
  if (s.llm_screen !== "shadow") return [];
  const missing: string[] = [];
  if (!keys.has("dart_api_key")) missing.push("국내(OpenDART 인증키 없음)");
  if (!s.sec_user_agent.trim()) missing.push("미국(SEC 연락처 없음)");
  return missing;
}

/** 키 끝자리 표시. 예: ••••ab12 */
export function maskedHint(hint: string): string {
  return `••••${hint}`;
}
