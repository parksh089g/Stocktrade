import type { DataSource, User } from "../data/source";

/** 로그인은 됐지만 owners 표에 없는 계정. RLS 때문에 기록이 하나도 보이지 않는다. */
export function NotOwner({ source, user, onRetry }: { source: DataSource; user: User; onRetry(): void }) {
  const sql = `insert into public.owners (user_id)\nselect id from auth.users where email = '${user.email.replace(/'/g, "''")}'\non conflict do nothing;`;
  return (
    <div class="center-page">
      <h1 style={{ fontSize: "20px", margin: "0 0 8px" }}>소유자 등록이 필요합니다</h1>
      <p style={{ margin: "0 0 12px" }}>
        <strong>{user.email}</strong> 계정은 아직 이 앱의 소유자로 등록되지 않아 기록을 볼 수 없습니다. Supabase 대시보드의{" "}
        <strong>SQL Editor</strong>에서 아래를 실행한 뒤 다시 확인을 누르세요.
      </p>
      <section class="card">
        <code style={{ whiteSpace: "pre-wrap" }}>{sql}</code>
      </section>
      <div style={{ display: "flex", gap: "8px" }}>
        <button class="btn primary" style={{ flex: 1 }} onClick={onRetry}>
          다시 확인
        </button>
        <button class="btn" onClick={() => source.signOut()}>
          로그아웃
        </button>
      </div>
    </div>
  );
}
