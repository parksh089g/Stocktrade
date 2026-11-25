// 화면이 쓰는 데이터 창구. 운영은 Supabase(supabase.ts), 화면 확인용은 데모 데이터(demo.ts).
// 웹앱은 기록을 읽기만 하고, 바꾸는 것은 일시정지 스위치, 엔진 설정(LLM 기능과 그 키), 이 기기의 푸시 구독뿐이다
// (RLS와 함수 권한도 그렇게 막는다). 키는 넣기만 하고 다시 읽지 못한다.
import type {
  EngineSettings,
  EngineSettingsInput,
  EquityRow,
  Market,
  Mode,
  OrderRow,
  PositionRow,
  RunRow,
  SecretName,
  SecretStatus,
  Snapshot,
  SwitchState,
} from "../lib/types";

export interface User {
  id: string;
  email: string;
}

export interface PushKeys {
  endpoint: string;
  p256dh: string;
  auth: string;
}

export interface DataSource {
  readonly demo: boolean;

  currentUser(): Promise<User | null>;
  onAuthChange(callback: (user: User | null) => void): () => void;
  signIn(email: string, password: string): Promise<void>;
  signOut(): Promise<void>;

  /** 일시정지 스위치. null이면 소유자로 등록되지 않은 계정이다(RLS가 행을 숨긴다). */
  getSwitch(): Promise<SwitchState | null>;
  setPause(paused: boolean, reason: string): Promise<SwitchState>;

  /** 가장 최근 실행의 모드(모의투자/실전). 실행 기록이 없으면 null */
  latestMode(): Promise<Mode | null>;
  /** 알림함 목록(최근 순서, report 없이) */
  listRuns(mode: Mode, opts: { limit: number; before?: string }): Promise<RunRow[]>;
  /** since 이후 실행(최근 순서, report 포함) */
  runsSince(mode: Mode, sinceIso: string): Promise<RunRow[]>;
  getRun(id: number): Promise<RunRow | null>;
  /** 시장별 가장 최근 잔고 기록 */
  latestSnapshots(mode: Mode): Promise<Partial<Record<Market, Snapshot>>>;
  /** 수익 곡선용: 매매 실행(손절 확인 제외)의 잔고와 비교 지수, 오래된 순서 */
  equityHistory(mode: Mode): Promise<EquityRow[]>;

  listPositions(mode: Mode): Promise<PositionRow[]>;
  listOrders(mode: Mode, opts: { limit: number; before?: string }): Promise<OrderRow[]>;
  /** 체결된 주문 전부(성적표, 산 이유) */
  filledOrders(mode: Mode): Promise<OrderRow[]>;

  /** 엔진 설정(LLM 기능). null이면 표가 없다(schema.sql을 다시 실행하기 전) */
  getEngineSettings(): Promise<EngineSettings | null>;
  saveEngineSettings(settings: EngineSettingsInput): Promise<EngineSettings>;
  /** 넣어 둔 비밀값의 끝 4자리와 시각. 값 자체는 돌려주지 않는다 */
  secretStatus(): Promise<SecretStatus[]>;
  /** 비밀값 넣기. 빈 문자열이면 지운다 */
  setSecret(name: SecretName, value: string): Promise<void>;

  savePushSubscription(keys: PushKeys): Promise<void>;
  deletePushSubscription(endpoint: string): Promise<void>;
}
