// Supabase 연결. publishable 키와 로그인한 사용자로 접속하므로 RLS(supabase/schema.sql)가 적용된다.
import { createClient, type PostgrestError } from "@supabase/supabase-js";
import type {
  Balance,
  EngineSettings,
  Benchmark,
  Market,
  Mode,
  Num,
  OrderRow,
  PositionRow,
  RunRow,
  SecretStatus,
  Snapshot,
  SwitchState,
} from "../lib/types";
import { MARKETS } from "../lib/types";
import type { DataSource, User } from "./source";

const ENGINE_SETTINGS_COLUMNS = "llm_provider,llm_model,llm_summary,llm_screen,sec_user_agent,updated_at";
const RUN_LIST_COLUMNS = "id,mode,market,status,message,error,started_at,finished_at,summary";
// PostgREST가 한 번에 돌려주는 최대 행 수(Supabase 기본값)
const MAX_ROWS = 1000;

type Result = { data: unknown; error: PostgrestError | null };

class DataError extends Error {}

function rows<T>(result: Result): T[] {
  if (result.error) throw new DataError(result.error.message);
  return (result.data ?? []) as T[];
}

function one<T>(result: Result): T | null {
  if (result.error) throw new DataError(result.error.message);
  return (result.data ?? null) as T | null;
}

function authMessage(message: string): string {
  if (/invalid login credentials/i.test(message)) return "이메일이나 비밀번호가 맞지 않습니다";
  if (/email not confirmed/i.test(message)) return "이메일 확인이 끝나지 않은 계정입니다";
  if (/fetch|network/i.test(message)) return "서버에 연결하지 못했습니다. 인터넷 연결을 확인해 주세요";
  return message;
}

interface SnapshotRow {
  id: number;
  market: Market;
  started_at: string;
  stop_loss_only: boolean | null;
  balance: Balance;
  prices: Record<string, Num> | null;
}

interface EquityQueryRow {
  market: Market;
  started_at: string;
  balance: Balance;
  benchmark: Benchmark | null;
}

export function createSupabaseSource(url: string, key: string): DataSource {
  const db = createClient(url, key, {
    auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: false },
  });
  const toUser = (u: { id: string; email?: string } | null | undefined): User | null =>
    u ? { id: u.id, email: u.email ?? "" } : null;

  return {
    demo: false,

    async currentUser() {
      const { data } = await db.auth.getSession();
      return toUser(data.session?.user);
    },
    onAuthChange(callback) {
      const { data } = db.auth.onAuthStateChange((_event, session) => callback(toUser(session?.user)));
      return () => data.subscription.unsubscribe();
    },
    async signIn(email, password) {
      const { error } = await db.auth.signInWithPassword({ email, password });
      if (error) throw new DataError(authMessage(error.message));
    },
    async signOut() {
      await db.auth.signOut();
    },

    async getSwitch() {
      return one<SwitchState>(
        await db.from("trading_switch").select("paused,reason,updated_at").eq("id", 1).maybeSingle(),
      );
    },
    async setPause(paused, reason) {
      // 바뀐 행을 돌려받아 확인한다(소유자가 아니면 RLS 때문에 바뀐 행이 없다)
      const row = one<SwitchState>(
        await db
          .from("trading_switch")
          .update({ paused, reason })
          .eq("id", 1)
          .select("paused,reason,updated_at")
          .maybeSingle(),
      );
      if (!row) throw new DataError("스위치를 바꿀 권한이 없습니다(소유자 등록을 확인하세요)");
      return row;
    },

    async getEngineSettings() {
      const result = await db.from("engine_settings").select(ENGINE_SETTINGS_COLUMNS).eq("id", 1).maybeSingle();
      // 표가 아직 없으면(schema.sql을 다시 실행하기 전) 화면에서 안내한다
      if (result.error && /engine_settings/.test(result.error.message)) return null;
      return one<EngineSettings>(result);
    },
    async saveEngineSettings(settings) {
      const row = one<EngineSettings>(
        await db.from("engine_settings").update(settings).eq("id", 1).select(ENGINE_SETTINGS_COLUMNS).maybeSingle(),
      );
      if (!row) throw new DataError("설정을 바꿀 권한이 없습니다(소유자 등록을 확인하세요)");
      return row;
    },
    async secretStatus() {
      return rows<SecretStatus>(await db.rpc("engine_secret_status"));
    },
    async setSecret(name, value) {
      const { error } = await db.rpc("set_engine_secret", { secret_name: name, secret_value: value });
      if (error) throw new DataError(/invalid secret/.test(error.message) ? "키 형식이 맞지 않습니다" : error.message);
    },

    async latestMode() {
      const row = one<{ mode: Mode }>(
        await db.from("runs").select("mode").order("started_at", { ascending: false }).limit(1).maybeSingle(),
      );
      return row?.mode ?? null;
    },
    async listRuns(mode, { limit, before }) {
      let q = db.from("runs").select(RUN_LIST_COLUMNS).eq("mode", mode);
      if (before) q = q.lt("started_at", before);
      return rows<RunRow>(await q.order("started_at", { ascending: false }).limit(limit));
    },
    async runsSince(mode, sinceIso) {
      return rows<RunRow>(
        await db
          .from("runs")
          .select("*")
          .eq("mode", mode)
          .gte("started_at", sinceIso)
          .order("started_at", { ascending: false }),
      );
    },
    async getRun(id) {
      return one<RunRow>(await db.from("runs").select("*").eq("id", id).maybeSingle());
    },
    async latestSnapshots(mode) {
      const out: Partial<Record<Market, Snapshot>> = {};
      for (const market of MARKETS) {
        // report->>balance는 값이 null이면 SQL NULL이 된다(report->balance는 JSON null이라 걸러지지 않는다)
        const row = one<SnapshotRow>(
          await db
            .from("runs")
            .select(
              "id,market,started_at,stop_loss_only:report->stop_loss_only,balance:report->balance,prices:report->prices",
            )
            .eq("mode", mode)
            .eq("market", market)
            .not("report->>balance", "is", null)
            .order("started_at", { ascending: false })
            .limit(1)
            .maybeSingle(),
        );
        if (row)
          out[market] = {
            runId: row.id,
            market: row.market,
            startedAt: row.started_at,
            stopLossOnly: row.stop_loss_only === true,
            balance: row.balance,
            prices: row.prices ?? {},
          };
      }
      return out;
    },
    async equityHistory(mode) {
      const found = rows<EquityQueryRow>(
        await db
          .from("runs")
          .select("market,started_at,balance:report->balance,benchmark:report->benchmark")
          .eq("mode", mode)
          .in("status", ["OK", "PAUSED"])
          .eq("report->>stop_loss_only", "false")
          .not("report->>balance", "is", null)
          .order("started_at", { ascending: true })
          .limit(MAX_ROWS),
      );
      return found.map((r) => ({
        market: r.market,
        startedAt: r.started_at,
        balance: r.balance,
        benchmark: r.benchmark,
      }));
    },

    async listPositions(mode) {
      return rows<PositionRow>(await db.from("positions").select("*").eq("mode", mode));
    },
    async listOrders(mode, { limit, before }) {
      let q = db.from("orders").select("*").eq("mode", mode);
      if (before) q = q.lt("created_at", before);
      return rows<OrderRow>(await q.order("created_at", { ascending: false }).limit(limit));
    },
    async filledOrders(mode) {
      return rows<OrderRow>(
        await db
          .from("orders")
          .select("*")
          .eq("mode", mode)
          .gt("filled_qty", 0)
          .order("created_at", { ascending: true })
          .limit(MAX_ROWS),
      );
    },

    async savePushSubscription(keys) {
      rows(
        await db
          .from("push_subscriptions")
          .upsert(
            { ...keys, user_agent: navigator.userAgent.slice(0, 300) },
            { onConflict: "endpoint" },
          ),
      );
    },
    async deletePushSubscription(endpoint) {
      rows(await db.from("push_subscriptions").delete().eq("endpoint", endpoint));
    },
  };
}
