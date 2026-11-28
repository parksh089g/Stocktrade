// 설정: 일시정지 스위치, AI 기능(LLM 키와 설정), 이 기기 알림, 보는 기록(모의투자/실전), 계정.
import { useEffect, useState } from "preact/hooks";
import { Banner, Card } from "../components/ui";
import { readStoredMode, useApp } from "../hooks";
import {
  CHEAP_MODEL,
  DEFAULT_MODEL,
  SECRET_LABEL,
  maskedHint,
  missingSources,
  secretProblem,
  settingsProblems,
} from "../lib/engine";
import { formatKst } from "../lib/format";
import { disablePush, enablePush, generatePushKeyPair, pushState, type PushState } from "../lib/push";
import type { EngineSettings, EngineSettingsInput, Mode, SecretName, SecretStatus } from "../lib/types";

function PauseCard() {
  const { source, switchState, setSwitchState } = useApp();
  const [confirming, setConfirming] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const apply = async (paused: boolean) => {
    setBusy(true);
    setError("");
    try {
      setSwitchState(await source.setPause(paused, paused ? reason.trim() || "웹앱에서 켬" : ""));
      setConfirming(false);
      setReason("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <h2>일시정지</h2>
      <p class="basis">
        {switchState.paused ? "켜짐" : "꺼짐"} · {formatKst(switchState.updated_at)}에 바뀜
        {switchState.paused && switchState.reason ? ` · 이유: ${switchState.reason}` : ""}
      </p>
      <p class="small" style={{ margin: "0 0 12px" }}>
        켜면 다음 실행부터 새 주문을 내지 않습니다. <strong>손절 매도는 계속 나갑니다.</strong> 예약 실행 자체를 멈추려면 GitHub
        저장소 변수 SCHEDULE_ENABLED를 끕니다.
      </p>
      {error && (
        <p class="error-text" role="alert">
          {error}
        </p>
      )}
      {switchState.paused ? (
        confirming ? (
          <div>
            <p style={{ margin: "0 0 8px" }}>일시정지를 끄면 다음 실행부터 다시 주문합니다. 끌까요?</p>
            <div class="choice">
              <button class="btn" onClick={() => setConfirming(false)} disabled={busy}>
                취소
              </button>
              <button class="btn primary" onClick={() => apply(false)} disabled={busy}>
                {busy ? "바꾸는 중…" : "끄기"}
              </button>
            </div>
          </div>
        ) : (
          <button class="btn primary block" onClick={() => setConfirming(true)}>
            일시정지 끄기
          </button>
        )
      ) : confirming ? (
        <div>
          <label class="field">
            <span>이유(알림과 기록에 남습니다)</span>
            <input value={reason} maxLength={100} placeholder="예: 시장 점검" onInput={(e) => setReason(e.currentTarget.value)} />
          </label>
          <div class="choice">
            <button class="btn" onClick={() => setConfirming(false)} disabled={busy}>
              취소
            </button>
            <button class="btn danger" onClick={() => apply(true)} disabled={busy}>
              {busy ? "바꾸는 중…" : "일시정지 켜기"}
            </button>
          </div>
        </div>
      ) : (
        <button class="btn danger block" onClick={() => setConfirming(true)}>
          일시정지 켜기
        </button>
      )}
    </Card>
  );
}


const errorText = (err: unknown) => (err instanceof Error ? err.message : String(err));

function SecretRow(props: { name: SecretName; status?: SecretStatus; help: string; onChanged: () => void }) {
  const { source } = useApp();
  const { name, status } = props;
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [error, setError] = useState("");

  const save = async (next: string) => {
    if (next) {
      const problem = secretProblem(name, next);
      if (problem) return setError(problem);
    }
    setBusy(true);
    setError("");
    try {
      await source.setSecret(name, next.trim());
      setValue("");
      setConfirmDelete(false);
      props.onChanged();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div class="secret-row">
      <label class="field">
        <span>
          {SECRET_LABEL[name]} ·{" "}
          {status ? `넣어 둠 ${maskedHint(status.hint)} · ${formatKst(status.updated_at)}` : "없음"}
        </span>
        <input
          type="password"
          autocomplete="off"
          spellcheck={false}
          value={value}
          placeholder={status ? "바꾸려면 새 키를 붙여 넣기" : "키 붙여 넣기"}
          onInput={(e) => setValue(e.currentTarget.value)}
        />
      </label>
      <p class="small" style={{ margin: "-6px 0 8px" }}>
        {props.help}
      </p>
      {error && (
        <p class="error-text" role="alert">
          {error}
        </p>
      )}
      <div class="choice">
        <button class="btn primary" onClick={() => save(value)} disabled={busy || !value.trim()}>
          {busy && !confirmDelete ? "저장 중…" : "저장"}
        </button>
        {status &&
          (confirmDelete ? (
            <button class="btn danger" onClick={() => save("")} disabled={busy}>
              {busy ? "지우는 중…" : "정말 지우기"}
            </button>
          ) : (
            <button class="btn" onClick={() => setConfirmDelete(true)} disabled={busy}>
              지우기
            </button>
          ))}
      </div>
    </div>
  );
}

function AiCard() {
  const { source } = useApp();
  const [saved, setSaved] = useState<EngineSettings | null | undefined>(undefined);
  const [draft, setDraft] = useState<EngineSettingsInput | null>(null);
  const [secrets, setSecrets] = useState<SecretStatus[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const loadSecrets = () => source.secretStatus().then(setSecrets, (err) => setError(errorText(err)));
  useEffect(() => {
    source.getEngineSettings().then(
      (s) => {
        setSaved(s);
        if (s) {
          const { updated_at: _, ...input } = s;
          setDraft(input);
        }
      },
      (err) => {
        setSaved(null);
        setError(errorText(err));
      },
    );
    loadSecrets();
  }, []);

  if (saved === undefined)
    return (
      <Card>
        <h2>AI 기능</h2>
        <p class="small">불러오는 중…</p>
      </Card>
    );
  if (saved === null || !draft)
    return (
      <Card>
        <h2>AI 기능</h2>
        <p class="small">
          설정 표가 없습니다. Supabase SQL Editor에서 supabase/schema.sql을 한 번 더 실행해 주세요.
          {error && ` (${error})`}
        </p>
      </Card>
    );

  const keys = new Set(secrets.map((s) => s.name));
  const problems = settingsProblems(draft, keys);
  const missing = missingSources(draft, keys);
  const set = (patch: Partial<EngineSettingsInput>) => {
    setDraft({ ...draft, ...patch });
    setMessage("");
  };
  const save = async () => {
    setBusy(true);
    setError("");
    try {
      const row = await source.saveEngineSettings({
        ...draft,
        llm_model: draft.llm_model.trim(),
        sec_user_agent: draft.sec_user_agent.trim(),
      });
      setSaved(row);
      setMessage(`저장했습니다 · ${formatKst(row.updated_at)}. 다음 실행부터 씁니다.`);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };
  const on = draft.llm_provider !== "none";

  return (
    <Card>
      <h2>AI 기능</h2>
      <p class="small" style={{ margin: "0 0 4px" }}>
        실행 요약(알림 맨 앞 2~3줄)과 매수 후보 악재 판정(기록만, 주문에는 반영 안 함). 주문과 리스크 한도에는 손대지
        않고, 실패하면 AI 없이 지금처럼 돕니다.
      </p>
      <p class="basis">
        키는 Supabase Vault에 암호화해 저장하고, 이 앱에서도 다시 볼 수 없습니다(끝 4자리만). GitHub 저장소에 같은 이름의
        값이 있으면 그 값이 먼저입니다.
      </p>

      <SecretRow
        name="llm_api_key"
        status={secrets.find((s) => s.name === "llm_api_key")}
        help="Claude Console(platform.claude.com) → API Keys에서 만든 키"
        onChanged={loadSecrets}
      />
      <SecretRow
        name="dart_api_key"
        status={secrets.find((s) => s.name === "dart_api_key")}
        help="(악재 판정, 국내 공시) opendart.fss.or.kr에서 무료로 받는 인증키"
        onChanged={loadSecrets}
      />

      <div class="field">
        <span>AI 사용</span>
        <div class="choice">
          <button class="btn" aria-pressed={!on} onClick={() => set({ llm_provider: "none", llm_screen: "off" })}>
            끄기
          </button>
          <button class="btn" aria-pressed={on} onClick={() => set({ llm_provider: "anthropic" })}>
            Claude
          </button>
        </div>
      </div>

      {on && (
        <>
          <label class="field">
            <span>모델(비우면 {DEFAULT_MODEL})</span>
            <input
              value={draft.llm_model}
              placeholder={DEFAULT_MODEL}
              autocomplete="off"
              spellcheck={false}
              onInput={(e) => set({ llm_model: e.currentTarget.value })}
            />
          </label>
          <p class="small" style={{ margin: "-6px 0 8px" }}>
            비용을 줄이려면{" "}
            <button class="btn link small" onClick={() => set({ llm_model: CHEAP_MODEL })}>
              {CHEAP_MODEL}
            </button>
          </p>
          <div class="field">
            <span>실행 요약</span>
            <div class="choice">
              <button class="btn" aria-pressed={!draft.llm_summary} onClick={() => set({ llm_summary: false })}>
                끄기
              </button>
              <button class="btn" aria-pressed={draft.llm_summary} onClick={() => set({ llm_summary: true })}>
                켜기
              </button>
            </div>
          </div>
          <div class="field">
            <span>악재 판정</span>
            <div class="choice">
              <button class="btn" aria-pressed={draft.llm_screen === "off"} onClick={() => set({ llm_screen: "off" })}>
                끄기
              </button>
              <button
                class="btn"
                aria-pressed={draft.llm_screen === "shadow"}
                onClick={() => set({ llm_screen: "shadow" })}
              >
                기록만
              </button>
            </div>
          </div>
          {draft.llm_screen === "shadow" && (
            <label class="field">
              <span>SEC 연락처(미국 공시, 예: stocktrade 이메일주소)</span>
              <input
                value={draft.sec_user_agent}
                maxLength={200}
                autocomplete="off"
                onInput={(e) => set({ sec_user_agent: e.currentTarget.value })}
              />
            </label>
          )}
        </>
      )}

      {missing.length > 0 && <p class="small">공시를 보지 못하는 시장: {missing.join(", ")}. 그 시장은 '판정 못 함'으로 기록됩니다.</p>}
      {problems.map((p) => (
        <p key={p} class="error-text">
          {p}
        </p>
      ))}
      {error && (
        <p class="error-text" role="alert">
          {error}
        </p>
      )}
      {message && <p class="small">{message}</p>}
      <button class="btn primary block" onClick={save} disabled={busy || problems.length > 0}>
        {busy ? "저장 중…" : "설정 저장"}
      </button>
      <p class="basis" style={{ marginTop: 8 }}>
        마지막 저장 {formatKst(saved.updated_at)}
      </p>
    </Card>
  );
}

const PUSH_TEXT: Record<PushState, string> = {
  unsupported: "이 브라우저는 웹 푸시를 지원하지 않습니다. 안드로이드 크롬에서 앱을 설치해 쓰세요.",
  "no-key": "알림 키가 아직 없습니다. 한 번 만들면 엔진이 같은 키로 알림을 보냅니다. 개인키는 Supabase Vault에 암호화해 두고 화면에는 나오지 않습니다.",
  denied: "알림이 차단돼 있습니다. 크롬 메뉴 → 설정 → 사이트 설정 → 알림에서 이 앱을 허용한 뒤 다시 켜세요.",
  off: "꺼짐. 켜면 실행이 끝날 때마다 이 기기로 알림이 옵니다.",
  on: "켜짐. 실행이 끝나면 이 기기로 알림이 옵니다.",
};

function PushCard() {
  const { source } = useApp();
  const [state, setState] = useState<PushState | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [storedKey, setStoredKey] = useState<string | null>(null);

  const refresh = async () => {
    try {
      const key = source.demo ? null : await source.getPushKey();
      setStoredKey(key);
      setState(await pushState(key));
    } catch (err) {
      setState("unsupported");
      setError(err instanceof Error ? err.message : String(err));
    }
  };
  useEffect(() => {
    refresh();
  }, []);

  /** 키를 만들어 넣고 바로 이 기기 알림을 켠다 */
  const createKey = async () => {
    setBusy(true);
    setError("");
    try {
      const pair = await generatePushKeyPair();
      await source.createPushKey(pair.privateKey, pair.publicKey);
      setStoredKey(pair.publicKey);
      await enablePush(source, pair.publicKey);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
      refresh();
    }
  };

  const toggle = async (on: boolean) => {
    setBusy(true);
    setError("");
    try {
      if (on) await enablePush(source, storedKey);
      else await disablePush(source);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
      refresh();
    }
  };

  return (
    <Card>
      <h2>이 기기 알림</h2>
      {source.demo ? (
        <p class="small">데모 데이터에서는 알림을 켤 수 없습니다.</p>
      ) : (
        <>
          <p class="small" style={{ margin: "0 0 12px" }}>
            {state ? PUSH_TEXT[state] : "확인 중…"}
          </p>
          {error && (
            <p class="error-text" role="alert">
              {error}
            </p>
          )}
          {state === "no-key" && (
            <button class="btn primary block" onClick={createKey} disabled={busy}>
              {busy ? "만드는 중…" : "알림 키 만들고 알림 켜기"}
            </button>
          )}
          {state === "off" && (
            <button class="btn primary block" onClick={() => toggle(true)} disabled={busy}>
              {busy ? "켜는 중…" : "알림 켜기"}
            </button>
          )}
          {state === "on" && (
            <button class="btn block" onClick={() => toggle(false)} disabled={busy}>
              {busy ? "끄는 중…" : "알림 끄기"}
            </button>
          )}
        </>
      )}
    </Card>
  );
}

function ModeCard() {
  const { mode, setMode } = useApp();
  const stored = readStoredMode();
  const options: [Mode, string][] = [
    ["paper", "모의투자"],
    ["live", "실전"],
  ];
  return (
    <Card>
      <h2>보는 기록</h2>
      <p class="basis">모의투자와 실전 기록은 따로 저장됩니다. 기본은 가장 최근 실행의 모드입니다.</p>
      <div class="choice">
        {options.map(([m, label]) => (
          <button key={m} class="btn" aria-pressed={mode === m} onClick={() => setMode(m)}>
            {label}
          </button>
        ))}
      </div>
      {stored && (
        <button class="btn link small" onClick={() => setMode(null)}>
          기본값(가장 최근 실행)으로 되돌리기
        </button>
      )}
    </Card>
  );
}

export function Settings() {
  const { source, user } = useApp();
  return (
    <div>
      {source.demo && (
        <Banner tone="info">
          <strong>데모 데이터</strong>
          화면 확인용 가짜 기록입니다. 실제 계좌와 관계없습니다.
        </Banner>
      )}
      <PauseCard />
      <AiCard />
      <PushCard />
      <ModeCard />
      <Card>
        <h2>계정</h2>
        <p class="basis">{user.email}</p>
        <button class="btn block" onClick={() => source.signOut()}>
          로그아웃
        </button>
      </Card>
      <p class="basis" style={{ textAlign: "center" }}>
        버전 {__APP_VERSION__} · {formatKst(__BUILT_AT__)} 빌드
      </p>
    </div>
  );
}
