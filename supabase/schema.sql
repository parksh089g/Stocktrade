-- 주식 자동 매매 시스템 Supabase 스키마
-- Supabase 대시보드 > SQL Editor에 전체를 붙여 넣고 실행한다. 여러 번 실행해도 안전하다.
--
-- 엔진(GitHub Actions)은 secret 키로 접속해 RLS를 거치지 않는다.
-- 웹앱(M7)은 로그인한 사용자로 접속하고, owners 표에 등록된 사람만 읽고 스위치를 바꿀 수 있다.
-- 시간은 모두 timestamptz(UTC 저장)이다.

-- ---------------------------------------------------------------- 표

-- 일시정지 스위치: 행 하나만 둔다
create table if not exists public.trading_switch (
  id smallint primary key default 1 check (id = 1),
  paused boolean not null default false,
  reason text not null default '',
  updated_at timestamptz not null default now()
);
insert into public.trading_switch (id) values (1) on conflict (id) do nothing;

-- 전략별 보유분. 모의투자(paper)와 실전(live)은 계좌가 달라 따로 둔다
create table if not exists public.positions (
  mode text not null check (mode in ('paper', 'live')),
  market text not null check (market in ('KR', 'US')),
  symbol text not null,
  strategy text not null,
  qty integer not null check (qty > 0),
  avg_price numeric not null check (avg_price > 0),
  opened_at timestamptz not null,
  updated_at timestamptz not null default now(),
  primary key (mode, market, symbol, strategy)
);

-- 주문 기록. filled_qty는 보유분에 반영한 체결 수량이다
create table if not exists public.orders (
  mode text not null check (mode in ('paper', 'live')),
  client_order_id text not null,
  market text not null check (market in ('KR', 'US')),
  symbol text not null,
  side text not null check (side in ('BUY', 'SELL')),
  qty integer not null check (qty > 0),
  order_type text not null check (order_type in ('MARKET', 'LIMIT')),
  limit_price numeric,
  strategy text not null,
  trade_date date not null,  -- 그 시장 현지 날짜
  broker_order_id text,
  status text not null,
  filled_qty integer not null default 0 check (filled_qty >= 0),
  avg_fill_price numeric,
  message text not null default '',
  created_at timestamptz not null,
  updated_at timestamptz not null default now(),
  primary key (mode, client_order_id)
);
-- 주문을 낸 신호의 근거(웹앱에서 왜 샀는지/팔았는지 보여 준다). 이 칸이 생기기 전 주문은 빈 문자열이다.
-- 이미 만든 표에도 칸이 생기도록 따로 추가한다
alter table public.orders add column if not exists reason text not null default '';
create index if not exists orders_by_day on public.orders (mode, market, trade_date);
create index if not exists orders_open on public.orders (mode, market)
  where status in ('SUBMITTED', 'PARTIALLY_FILLED');
-- 마지막 안전장치: 같은 날 같은 종목에는 거부되지 않은 주문이 하나만 있을 수 있다
create unique index if not exists orders_one_per_symbol_day
  on public.orders (mode, market, symbol, trade_date) where status <> 'REJECTED';

-- 실행 기록. summary는 웹앱 알림함에 그대로 보여 줄 문장이다
create table if not exists public.runs (
  id bigint generated always as identity primary key,
  mode text not null,
  market text not null,
  status text not null,
  message text not null default '',
  error text,
  started_at timestamptz not null,
  finished_at timestamptz,
  summary text not null,
  report jsonb not null
);
create index if not exists runs_recent on public.runs (started_at desc);

-- 매수 후보 악재 판정(LLM, 섀도 모드) 기록. 실행 기록(runs.report)의 screens를 한 줄씩 펼친 보기다.
-- 판정은 주문에 반영하지 않으므로, 몇 주 뒤 '거부했을 것' 종목과 나머지의 이후 수익을 비교해 효과를 본다.
-- security_invoker: 보는 사람의 권한(runs 표의 RLS)을 그대로 따른다
create or replace view public.llm_screens with (security_invoker = true) as
select
  r.id as run_id,
  r.mode,
  r.started_at,
  s ->> 'market' as market,
  s ->> 'symbol' as symbol,
  s ->> 'strategy' as strategy,
  s ->> 'verdict' as verdict,
  s ->> 'reason' as reason,
  s ->> 'mode' as screen_mode,
  s ->> 'model' as model,
  (s ->> 'since')::date as since,
  jsonb_array_length(coalesce(s -> 'disclosures', '[]'::jsonb)) as disclosure_count
from public.runs r
cross join lateral jsonb_array_elements(coalesce(r.report -> 'screens', '[]'::jsonb)) s;

-- 웹 푸시 구독(기기별). 웹앱이 넣고, 엔진이 읽어서 보낸다. 만료되면 엔진이 지운다
create table if not exists public.push_subscriptions (
  endpoint text primary key,
  p256dh text not null,
  auth text not null,
  user_id uuid not null default auth.uid() references auth.users (id) on delete cascade,
  user_agent text not null default '',
  created_at timestamptz not null default now()
);

-- 엔진만 쓰는 값(GitHub Actions 실행 사이에 남겨 둘 KIS 토큰 등). 토큰은 앱 시크릿으로 암호화해서 넣는다.
-- 정책이 없어 웹앱에서는 볼 수 없다
create table if not exists public.engine_cache (
  name text primary key,
  value text not null,
  updated_at timestamptz not null default now()
);

-- 웹앱을 쓸 수 있는 사람. 웹앱에서 처음 가입한 뒤 대시보드에서 직접 한 줄 넣는다
create table if not exists public.owners (
  user_id uuid primary key references auth.users (id) on delete cascade
);

-- ---------------------------------------------------------------- 함수, 트리거

create or replace function public.is_owner() returns boolean
language sql stable security definer set search_path = ''
as $$
  select exists (select 1 from public.owners where user_id = auth.uid())
$$;

create or replace function public.touch_updated_at() returns trigger
language plpgsql set search_path = ''
as $$
begin
  new.updated_at = now();
  return new;
end
$$;

create or replace trigger trading_switch_touch before update on public.trading_switch
  for each row execute function public.touch_updated_at();
create or replace trigger positions_touch before update on public.positions
  for each row execute function public.touch_updated_at();
create or replace trigger orders_touch before update on public.orders
  for each row execute function public.touch_updated_at();
create or replace trigger engine_cache_touch before update on public.engine_cache
  for each row execute function public.touch_updated_at();

-- ---------------------------------------------------------------- 접근 권한(RLS)

alter table public.trading_switch enable row level security;
alter table public.positions enable row level security;
alter table public.orders enable row level security;
alter table public.runs enable row level security;
alter table public.push_subscriptions enable row level security;
alter table public.owners enable row level security;  -- 정책 없음: 웹앱에서는 볼 수 없다
alter table public.engine_cache enable row level security;  -- 정책 없음: 웹앱에서는 볼 수 없다

drop policy if exists "owner read" on public.trading_switch;
create policy "owner read" on public.trading_switch
  for select to authenticated using (public.is_owner());
drop policy if exists "owner update" on public.trading_switch;
create policy "owner update" on public.trading_switch
  for update to authenticated using (public.is_owner()) with check (public.is_owner());

drop policy if exists "owner read" on public.positions;
create policy "owner read" on public.positions
  for select to authenticated using (public.is_owner());

drop policy if exists "owner read" on public.orders;
create policy "owner read" on public.orders
  for select to authenticated using (public.is_owner());

drop policy if exists "owner read" on public.runs;
create policy "owner read" on public.runs
  for select to authenticated using (public.is_owner());

drop policy if exists "owner manages own" on public.push_subscriptions;
create policy "owner manages own" on public.push_subscriptions
  for all to authenticated
  using (public.is_owner() and user_id = auth.uid())
  with check (public.is_owner() and user_id = auth.uid());
