import { useState } from "preact/hooks";
import type { DataSource } from "../data/source";

export function Login({ source }: { source: DataSource }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (e: Event) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await source.signIn(email.trim(), password);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div class="center-page">
      <h1 style={{ fontSize: "22px", margin: "0 0 4px" }}>주식 자동매매</h1>
      <p class="muted" style={{ margin: "0 0 12px" }}>
        기록을 보고 일시정지를 켜고 끄는 앱입니다.
      </p>
      <form onSubmit={submit}>
        <label class="field">
          <span>이메일</span>
          <input type="email" autocomplete="username" required value={email} onInput={(e) => setEmail(e.currentTarget.value)} />
        </label>
        <label class="field">
          <span>비밀번호</span>
          <input
            type="password"
            autocomplete="current-password"
            required
            value={password}
            onInput={(e) => setPassword(e.currentTarget.value)}
          />
        </label>
        {error && (
          <p class="error-text" role="alert">
            {error}
          </p>
        )}
        <button class="btn primary block" type="submit" disabled={busy}>
          {busy ? "로그인 중…" : "로그인"}
        </button>
      </form>
      <p class="muted small" style={{ marginTop: "16px" }}>
        새 가입은 받지 않습니다. 계정은 Supabase 대시보드에서 만듭니다(docs/SETUP.md 6장).
      </p>
    </div>
  );
}
