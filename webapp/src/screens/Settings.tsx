// 설정: 일시정지 스위치, 이 기기 알림, 보는 기록(모의투자/실전), 계정.
import { useEffect, useState } from "preact/hooks";
import { Banner, Card } from "../components/ui";
import { readStoredMode, useApp } from "../hooks";
import { formatKst } from "../lib/format";
import { disablePush, enablePush, pushState, type PushState } from "../lib/push";
import type { Mode } from "../lib/types";

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

const PUSH_TEXT: Record<PushState, string> = {
  unsupported: "이 브라우저는 웹 푸시를 지원하지 않습니다. 안드로이드 크롬에서 앱을 설치해 쓰세요.",
  "no-key": "웹 푸시 공개키가 빌드에 없습니다(VITE_VAPID_PUBLIC_KEY).",
  denied: "알림이 차단돼 있습니다. 크롬 메뉴 → 설정 → 사이트 설정 → 알림에서 이 앱을 허용한 뒤 다시 켜세요.",
  off: "꺼짐. 켜면 실행이 끝날 때마다 이 기기로 알림이 옵니다.",
  on: "켜짐. 실행이 끝나면 이 기기로 알림이 옵니다.",
};

function PushCard() {
  const { source } = useApp();
  const [state, setState] = useState<PushState | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const refresh = () =>
    pushState().then(setState, (err: Error) => {
      setState("unsupported");
      setError(err.message);
    });
  useEffect(() => {
    refresh();
  }, []);

  const toggle = async (on: boolean) => {
    setBusy(true);
    setError("");
    try {
      if (on) await enablePush(source);
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
