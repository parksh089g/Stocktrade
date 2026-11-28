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

-- 엔진 설정(행 하나). 웹앱 설정 화면에서 소유자가 바꾼다. 비밀값은 여기 두지 않고 Vault에 둔다(아래 함수).
-- 엔진은 GitHub에 같은 이름의 값이 없을 때만 이 값을 쓴다
create table if not exists public.engine_settings (
  id smallint primary key default 1 check (id = 1),
  llm_provider text not null default 'none' check (llm_provider in ('none', 'anthropic')),
  llm_model text not null default '' check (length(llm_model) <= 100),
  llm_summary boolean not null default true,
  llm_screen text not null default 'off' check (llm_screen in ('off', 'shadow')),
  sec_user_agent text not null default '' check (length(sec_user_agent) <= 200),
  updated_at timestamptz not null default now()
);
insert into public.engine_settings (id) values (1) on conflict (id) do nothing;

-- 웹 푸시 공개키(행 하나). 웹앱 설정 화면에서 키를 만들면 set_push_key()가 개인키(Vault)와 함께 넣는다.
-- 공개키는 공개용 값이다. 웹앱이 알림 구독에 쓴다
create table if not exists public.push_key (
  id smallint primary key default 1 check (id = 1),
  public_key text not null check (public_key ~ '^[A-Za-z0-9_-]{87}$'),
  created_at timestamptz not null default now()
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
create or replace trigger engine_settings_touch before update on public.engine_settings
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
alter table public.engine_settings enable row level security;
alter table public.push_key enable row level security;  -- 읽기만, 넣기는 set_push_key()로만

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

drop policy if exists "owner read" on public.engine_settings;
create policy "owner read" on public.engine_settings
  for select to authenticated using (public.is_owner());
drop policy if exists "owner update" on public.engine_settings;
create policy "owner update" on public.engine_settings
  for update to authenticated using (public.is_owner()) with check (public.is_owner());

drop policy if exists "owner read" on public.push_key;
create policy "owner read" on public.push_key
  for select to authenticated using (public.is_owner());

-- ---------------------------------------------------------------- 엔진 비밀값(Supabase Vault)
-- 웹앱에서 넣는 API 키(LLM, DART). Vault가 암호화해서 저장한다.
-- 웹앱(소유자)은 넣기/지우기와 '넣었는지, 끝 4자리, 바꾼 시각'만 볼 수 있고 값은 다시 읽지 못한다.
-- 값을 꺼내는 함수는 엔진(secret 키, service_role)만 부를 수 있다.
-- 이름은 아래 목록만 받는다. Vault 안에서는 stocktrade_ 를 앞에 붙여 다른 비밀값과 섞이지 않게 한다.

create extension if not exists supabase_vault with schema vault;

create or replace function public.engine_secret_names() returns text[]
language sql immutable set search_path = ''
as $$ select array['llm_api_key', 'dart_api_key'] $$;

create or replace function public.set_engine_secret(secret_name text, secret_value text)
returns void
language plpgsql security definer set search_path = ''
as $$
declare
  existing uuid;
  vault_name text := 'stocktrade_' || secret_name;
  value text := btrim(coalesce(secret_value, ''));
begin
  if not public.is_owner() then
    raise exception 'not allowed' using errcode = '42501';
  end if;
  if not (secret_name = any (public.engine_secret_names())) then
    raise exception 'unknown secret name' using errcode = '22023';
  end if;
  if length(value) > 500 or value ~ '\s' then
    raise exception 'invalid secret value' using errcode = '22023';
  end if;
  select id into existing from vault.secrets where name = vault_name;
  if value = '' then
    delete from vault.secrets where id = existing;
  elsif existing is null then
    perform vault.create_secret(value, vault_name, 'stocktrade 엔진 비밀값(웹앱에서 넣음)');
  else
    perform vault.update_secret(existing, value, vault_name, 'stocktrade 엔진 비밀값(웹앱에서 넣음)');
  end if;
end
$$;

create or replace function public.engine_secret_status()
returns table (name text, hint text, updated_at timestamptz)
language plpgsql security definer set search_path = ''
as $$
begin
  if not public.is_owner() then
    raise exception 'not allowed' using errcode = '42501';
  end if;
  return query
    select substr(d.name, length('stocktrade_') + 1), right(d.decrypted_secret, 4), d.updated_at
    from vault.decrypted_secrets d
    where d.name in (select 'stocktrade_' || n from unnest(public.engine_secret_names()) n);
end
$$;

-- 웹 푸시 키 한 쌍을 넣는다(웹앱이 폰 브라우저에서 만든다). 개인키는 Vault에, 공개키는 push_key 표에.
-- 짝이 어긋나지 않게 둘을 한 번에 넣고, 이미 있으면 바꾸지 않는다(바꾸면 켜 둔 기기의 알림이 끊긴다).
-- set_engine_secret()으로는 개인키만 따로 넣거나 바꿀 수 없다(engine_secret_names에 없다).
create or replace function public.set_push_key(private_key text, public_key text)
returns void
language plpgsql security definer set search_path = ''
as $$
begin
  if not public.is_owner() then
    raise exception 'not allowed' using errcode = '42501';
  end if;
  if coalesce(private_key, '') !~ '^[A-Za-z0-9_-]{43}$'
     or coalesce(public_key, '') !~ '^[A-Za-z0-9_-]{87}$' then
    raise exception 'invalid push key' using errcode = '22023';
  end if;
  if exists (select 1 from public.push_key)
     or exists (select 1 from vault.secrets where name = 'stocktrade_vapid_private_key') then
    raise exception 'push key exists' using errcode = '23505';
  end if;
  perform vault.create_secret(private_key, 'stocktrade_vapid_private_key', 'stocktrade 웹 푸시 개인키(웹앱에서 만듦)');
  insert into public.push_key (id, public_key) values (1, public_key);
end
$$;

-- 엔진이 쓰는 비밀값: 웹앱에서 넣은 API 키와 웹 푸시 개인키
create or replace function public.get_engine_secrets()
returns table (name text, value text)
language sql security definer set search_path = ''
as $$
  select substr(d.name, length('stocktrade_') + 1), d.decrypted_secret
  from vault.decrypted_secrets d
  where d.name in (
    select 'stocktrade_' || n from unnest(public.engine_secret_names() || array['vapid_private_key']) n
  )
$$;

-- 함수는 기본으로 누구나 부를 수 있어서 막고 필요한 쪽에만 연다
revoke all on function public.set_engine_secret(text, text) from public, anon;
revoke all on function public.engine_secret_status() from public, anon;
revoke all on function public.get_engine_secrets() from public, anon, authenticated;
revoke all on function public.set_push_key(text, text) from public, anon;
grant execute on function public.set_push_key(text, text) to authenticated;
grant execute on function public.set_engine_secret(text, text) to authenticated;
grant execute on function public.engine_secret_status() to authenticated;
grant execute on function public.get_engine_secrets() to service_role;
