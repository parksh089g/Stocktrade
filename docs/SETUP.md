# 준비 안내

직접 해 주셔야 하는 준비 작업이다. 비밀값은 이 PC의 `.env`와 GitHub Secrets에만 넣는다.
대화창이나 다른 곳에 붙여 넣지 않는다. 대시보드 메뉴 이름은 Supabase가 바꿀 수 있으니 비슷한 이름을 찾으면 된다.

## 1. Supabase (기록 저장)

1. [supabase.com](https://supabase.com)에 가입하고 **New project**를 만든다.
   - Region은 **Northeast Asia (Seoul)** 을 고른다.
   - Database password는 엔진이 쓰지 않지만 잃어버리지 않게 따로 적어 둔다.
2. 왼쪽 **SQL Editor** → 새 쿼리를 열고, 이 저장소의 `supabase/schema.sql` 내용을 전부 붙여 넣어 **Run**.
   - "Success. No rows returned"가 나오면 된다. 여러 번 실행해도 괜찮다.
   - **2026-11-10 변경**: 주문 표(`orders`)에 신호 근거 칸(`reason`)이 생겼다. 이미 실행했어도 한 번 더
     실행한다. **이 변경이 든 엔진 코드를 main에 올리기 전에 해야 한다.** 칸이 없는 채로 엔진이 돌면 주문은
     나갔는데 기록 저장이 실패한다. 4번 연결 확인에 `주문 이유 칸(reason) 확인`이 나오면 된다.
3. 접속 정보를 `.env`에 넣는다.
   - **Project URL** (`https://xxxx.supabase.co`): 프로젝트 화면의 **Connect** 버튼이나 Project Settings > Data API에 있다.
   - **secret 키** (`sb_secret_...`): Project Settings > API Keys에서 만들거나 확인한다.
     이 키는 모든 데이터를 읽고 쓰는 관리자 키다. `.env`와 GitHub Secrets 말고는 어디에도 두지 않는다.
     (publishable 키 `sb_publishable_...`는 6장 웹앱에서 쓰는 다른 키다)

   ```
   SUPABASE_URL=https://xxxx.supabase.co
   SUPABASE_SERVICE_KEY=sb_secret_...
   ```
4. 연결을 확인한다.

   ```bash
   uv run python -m trading.storage.check
   ```

   마지막 줄에 `Supabase 연결 확인 완료`가 나오면 된다.

### 1-1. 표 구조 자동 적용 (GitHub Actions)

`supabase/schema.sql`이 바뀐 코드가 main에 올라오면(PR 머지) `db-schema` 워크플로가 Supabase에 바로 적용한다.
배포할 때마다 할 일은 없고, 아래 Secret을 한 번만 넣으면 된다. 넣기 전에는 워크플로가 경고만 남기고 넘어가니
그동안은 위 2번처럼 SQL Editor에서 직접 실행한다.

1. Supabase 프로젝트 화면 위쪽 **Connect** → **Session pooler**의 연결 문자열을 복사한다
   (`postgresql://postgres.xxxx:[YOUR-PASSWORD]@aws-…pooler.supabase.com:5432/postgres`).
   GitHub 러너는 IPv4만 써서 Direct connection은 안 된다.
2. `[YOUR-PASSWORD]`를 프로젝트를 만들 때 적어 둔 Database password로 바꾼다. 잊었으면 Project Settings >
   Database > Reset database password로 새로 만든다(엔진과 웹앱은 이 비밀번호를 쓰지 않아 영향이 없다).
3. GitHub 저장소 **Settings > Secrets and variables > Actions > Secrets**에 `SUPABASE_DB_URL` 이름으로 넣는다.
4. **Actions > db-schema > Run workflow**(main)로 한 번 돌려 초록색이 되는지 본다.

- 저장소가 공개여도 이 Secret은 보이지 않는다. 워크플로는 main에 올라온 코드로만 돌고(PR이나 포크에서는 돌지
  않는다), 접속 주소를 로그에 찍지 않는다(GitHub이 Secret 값을 `***`로 가린다).
- 한 트랜잭션으로 돌려서 중간에 오류가 나면 아무것도 바뀌지 않고 실행이 빨간색이 된다.

참고: 무료 프로젝트는 일주일 동안 아무 요청이 없으면 일시 중지된다. 4장의 매일 자동 실행이 돌기 시작하면 걱정하지 않아도 된다.
그 전에 멈췄다면 대시보드에서 다시 켜면 된다.

## 2. 웹 푸시 키 (알림)

1. 키를 만든다. 개인키는 화면에 보이지 않고 `.env`에 바로 들어간다.

   ```bash
   uv run python -m trading.notify.vapid
   ```

   화면에 나오는 `VAPID_PUBLIC_KEY=...`는 6장 웹앱에서 쓴다(알려져도 괜찮은 값). 다시 실행하면 언제든 다시 볼 수 있다.
2. `.env`에 연락처를 넣는다. 푸시 서비스(구글)가 문제가 있을 때 연락하는 주소다.

   ```
   VAPID_SUBJECT=mailto:본인메일주소
   ```
3. 실제로 폰에 알림이 오는지는 6장에서 웹앱을 폰에 설치하고 알림을 켠 뒤에 확인한다.
   그 전에는 실행 결과가 Supabase `runs` 표와 GitHub Actions 로그에 남는다.
   - VAPID 개인키가 있는데 알림을 받을 기기가 없으면 실행이 실패로 끝난다(알림을 못 보낸 것도 실패로 친다).
     그래서 GitHub Secrets의 `VAPID_PRIVATE_KEY`는 6-5에서 폰 알림을 확인한 뒤에 넣는다.

## 3. KIS 모의투자 키 (증권사 연결)

모의투자 키만 이 PC에 넣는다. 실전 키는 이 PC에 두지 않는다(4장처럼 GitHub Secrets에만 넣는다).

1. **한국투자증권 계좌**가 있어야 한다. 본인 명의로 한국투자 앱에서 비대면 개설할 수 있다.
2. **모의투자 신청**: 한국투자증권 홈페이지나 앱의 모의투자 메뉴에서 신청하고 **모의투자 계좌번호**(8자리-2자리)를 확인한다.
   - 미국 주식도 모의투자로 해 보려면 해외주식 모의투자가 함께 되는지 확인하고, 따로 신청해야 하면 신청한다.
   - 모의투자에는 이용 기간이 있을 수 있다. 만료되면 다시 신청한다.
3. **KIS Developers 키 발급**: [apiportal.koreainvestment.com](https://apiportal.koreainvestment.com)
   - 회원가입 → 메일 인증 → 로그인 → 개인 메뉴의 인증키(앱키) 발급
   - 투자 환경을 **모의투자**로 고르고, 2번의 모의투자 계좌를 연결한다
   - **APP SECRET은 발급 직후에만 보인다.** 바로 `.env`에 넣는다(잃어버리면 지우고 다시 발급)
4. `.env`에 넣는다.

   ```
   TRADING_MODE=paper
   KIS_APP_KEY=발급받은_앱키
   KIS_APP_SECRET=발급받은_시크릿
   KIS_ACCOUNT_NO=모의투자계좌번호(예: 50123456-01)
   ```
5. 연결을 확인한다. 조회만 하고 주문은 내지 않는다.

   ```bash
   uv run python -m trading.brokers.kis_check
   ```

   국내/미국 각각 잔고, 보유, 현재가, 일봉, 휴장일 여부, 오늘 주문이 나오고 마지막에 `완료`가 나오면 된다.
   처음 실행하면 토큰을 발급받으면서 카카오 알림톡이 온다(정상). 토큰은 `.cache/`에 저장돼 하루 동안 다시 쓴다.
6. (선택) 장중에 주문 접수까지 확인한다. 체결되지 않을 가격(현재가 -20%)으로 1주 매수 주문을 낸다.

   ```bash
   uv run python -m trading.brokers.kis_check --order-test KR   # 국내 장중(09:00~15:30)
   uv run python -m trading.brokers.kis_check --order-test US   # 미국 정규장 중
   ```

   시험한 종목은 같은 날 중복 주문 방지 규칙 때문에 그날 엔진이 주문하지 않는다.
7. (선택) 실제 모의계좌 시세로 엔진을 시험 실행한다. 신호와 주문 계획까지 계산하고 주문은 내지 않는다.

   ```bash
   BROKER=kis uv run python -m trading.engine --market KR --dry-run
   ```

결과(성공/실패 메시지)를 알려 주면, KIS 응답이 예상과 다른 부분을 고친다. 키나 계좌번호는 붙여 넣지 않는다.

## 4. GitHub Actions (매일 자동 실행)

엔진은 GitHub Actions에서 시장마다 하루 두 번 돈다.
- 국내(`trade-kr`): 한국 시간 월~금 08:40, 백업 09:47
- 미국(`trade-us`): 한국 시간 월~금 23:40(뉴욕 장 시작 뒤), 백업 화~토 01:17
- 장 마감 전 손절 확인(`stoploss-kr`, `stoploss-us`): 국내는 한국 시간 월~금 13:17, 미국은 뉴욕 12:47(겨울)
  또는 13:47(서머타임), 한국 시간 화~토 02:47. 보유 종목을 지금 가격으로 보고 손절 매도만 낸다(매수 안 함).
  알릴 일(주문, 체결, 경고, 오류)이 없으면 알림을 보내지 않고 기록만 남긴다.

GitHub 예약 실행은 몇 분에서 몇 시간 늦게 시작할 수 있고, 붐빌 때는 건너뛰기도 한다(2026-10-26에는
2~6시간 늦어 미국 실행이 모두 장 마감 뒤에 돌았다). 그래서 5장의 Cloudflare 예약이 같은 시각에 정시로
부르고, GitHub 예약은 대비용으로 남겨 두었다. 아침 실행은 한 시간쯤 뒤에 백업으로 한 번 더 부른다.
예약 실행은 그날 먼저 도는 쪽이 완료(또는 일시정지)로 끝났거나 휴장일, 장 마감 후라 더 할 일이 없으면
나머지는 기록과 알림 없이 끝나고, 오류로 끝났으면 다음 실행이 다시 돈다. 손으로 실행은 이와 상관없이
언제나 돈다. 엔진이 휴장일과 장 시간을 다시 확인하고, 같은 날 같은 종목에는 주문을 한 번만 낸다.

먼저 1장(Supabase)을 끝낸다. 표가 하나 늘었으니(`engine_cache`), 이미 실행했어도 `supabase/schema.sql`을
한 번 더 실행한다. 여러 번 실행해도 괜찮다.

### 4-1. 비밀값 넣기

저장소 화면 **Settings > Secrets and variables > Actions > Secrets** 탭에서 **New repository secret**으로
하나씩 넣는다. 이름은 아래와 똑같이 쓴다.

| 이름 | 값 | 언제 |
|---|---|---|
| `KIS_PAPER_APP_KEY` | `.env`의 `KIS_APP_KEY` | 지금 |
| `KIS_PAPER_APP_SECRET` | `.env`의 `KIS_APP_SECRET` | 지금 |
| `KIS_PAPER_ACCOUNT_NO` | `.env`의 `KIS_ACCOUNT_NO` | 지금 |
| `SUPABASE_URL` | `.env`의 `SUPABASE_URL` | 지금 |
| `SUPABASE_SERVICE_KEY` | `.env`의 `SUPABASE_SERVICE_KEY` | 지금 |
| `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` | `.env`의 같은 이름 값 | 6-5에서 폰 알림을 확인한 뒤 |
| `KIS_LIVE_APP_KEY`, `KIS_LIVE_APP_SECRET`, `KIS_LIVE_ACCOUNT_NO` | 실전 키와 실전 계좌번호 | 실전 전환 때 |

- 실전 키는 이 PC의 파일에 저장하지 않는다. KIS 사이트에서 복사해 GitHub 화면에 바로 붙여 넣는다.
- 한 번 넣은 값은 GitHub에서도 다시 볼 수 없다(바꾸기만 된다). 로그에는 `***`로 가려진다.

### 4-2. 변수 넣기

같은 화면의 **Variables** 탭에서 **New repository variable**로 넣는다. 비밀값이 아닌 설정이다.

| 이름 | 값 | 뜻 |
|---|---|---|
| `SCHEDULE_ENABLED` | `true` | 매일 예약 실행(GitHub 예약, Cloudflare 예약)을 켠다. 없거나 다른 값이면 예약 실행이 돌지 않는다(손으로 실행은 된다) |
| `TRADING_MODE` | 넣지 않는다 | 없으면 모의투자다. 정확히 `live`일 때만 실전 키로 실전 계좌에 주문한다 |
| `MAX_DAILY_BUY_KRW`, `MAX_DAILY_BUY_USD` | 예: `3000000`, `2000` | (선택) 하루 매수 금액 상한 |

`SCHEDULE_ENABLED`는 4-3의 시험 실행이 성공한 뒤에 넣는다. 넣은 값은 실행 기록의 **설정 표시** 단계에서
볼 수 있다.

### 4-3. 손으로 시험 실행

1. 저장소 **Actions** 탭 → 왼쪽 **trade-kr** → 오른쪽 **Run workflow**
2. **시험 실행** 체크가 켜진 그대로(**예약 실행으로 돌리기**는 꺼진 그대로) **Run workflow**를 누른다. 장 시간과 상관없이 잔고, 시세, 신호,
   주문 계획까지 계산하고 주문은 내지 않는다. 기록도 Supabase에 남기지 않는다.
3. 실행을 눌러 **run / 실행 (모의투자)** 단계의 로그를 본다. `[모의투자] 국내 · 완료 · 시험 실행`과
   신호(없으면 "오늘은 신호가 없습니다")가 나오고 녹색 체크면 된다.
   Supabase를 아직 준비하지 않았어도 시험 실행은 된다(KIS 키 3개만 있으면 된다).
4. **trade-us**도 똑같이 해 본다.
5. 잘 되면 4-2의 `SCHEDULE_ENABLED=true`를 넣는다. 다음 평일부터 모의투자로 실제 주문이 나간다.

### 4-4. 실패 알림(메일)

실행이 실패하면(오류, 설정 누락, 알림 실패) GitHub이 메일을 보낸다. 받으려면 GitHub 오른쪽 위 프로필 →
**Settings > Notifications > System > Actions**에서 실패한 워크플로 알림을 이메일로 받게 둔다(보통 기본값).
예약 실행 실패 메일은 워크플로 파일의 예약 시각을 마지막으로 바꾼 계정으로 간다.

### 4-5. 멈추기

- 주문만 멈추기: 웹앱 **설정 → 일시정지 켜기**(6장). 웹앱을 못 쓸 때는 Supabase **Table Editor**의
  `trading_switch`에서 `paused`를 `true`로. 손절 매도는 계속 나간다.
- 실행 자체를 멈추기: 변수 `SCHEDULE_ENABLED`를 지우거나 `false`로 바꾼다. Cloudflare 예약이 불러도
  돌지 않는다.

### 4-6. 실전 전환 (6장까지 끝낸 뒤에)

1. 4-1 표의 실전 키 3개를 넣는다.
2. 변수 `TRADING_MODE`를 `live`로 넣는다. 처음에는 `MAX_DAILY_BUY_KRW`/`MAX_DAILY_BUY_USD`를 작게 둔다.
3. 되돌리기: `TRADING_MODE` 변수를 지우면 바로 다음 실행부터 모의투자다.

실전은 main 브랜치 코드로만 돈다. 모의투자와 실전 기록은 Supabase에 따로 저장되고, KIS 토큰도 모드별로
따로 암호화해 Supabase `engine_cache`에 둔다(토큰은 하루 유효, 실행마다 새로 받지 않는다).

GitHub 무료 요금제의 비공개 저장소는 Actions를 한 달 2,000분까지 쓸 수 있다. 실행 한 번에 1~2분이고,
예약 실행이 하루 최대 12번(Cloudflare 6번, GitHub 6번. 이미 끝났으면 바로 끝남)이라 한 달 400분 안쪽이다.

## 5. Cloudflare 예약 (정시 실행)

GitHub 예약은 몇 시간씩 늦을 때가 있어서, Cloudflare Worker(`scheduler/`)가 4장의 예약 시각에 맞춰
GitHub에 실행을 요청한다. 손으로 **Run workflow**를 누른 것과 같아서 바로 시작한다. Cloudflare 무료
요금제로 충분하다. 4장을 먼저 끝낸다.

### 5-1. GitHub 토큰 만들기

Worker가 실행을 요청할 때 쓰는 토큰이다. 이 저장소의 Actions 실행 권한만 준다.

1. GitHub 오른쪽 위 프로필 → **Settings > Developer settings > Personal access tokens > Fine-grained
   tokens** → **Generate new token**
2. **Token name**: `stocktrade-scheduler`, **Expiration**: 1년 정도. 만료일을 달력에 적어 둔다
   (만료되면 Cloudflare 예약이 실패하고, 늦을 수 있는 GitHub 예약만 돈다).
3. **Repository access**: **Only select repositories** → 이 저장소 하나
4. **Permissions > Repository permissions > Actions**: **Read and write**. 다른 권한은 주지 않는다
   (Metadata 읽기는 자동으로 붙는다).
5. **Generate token**을 누르고 토큰을 복사해 바로 5-2의 Cloudflare 화면에 붙여 넣는다. 파일이나 채팅에는
   붙여 넣지 않는다.

### 5-2. Worker 올리기

1. 이 PC에서 Cloudflare에 로그인한다. 브라우저가 열리면 허용을 누른다.
   ```bash
   cd scheduler
   npx wrangler login
   ```
2. 올린다. 예약(`wrangler.toml`의 `crons`)과 저장소 이름(`GITHUB_REPO`)도 함께 올라간다.
   ```bash
   npx wrangler deploy
   ```
3. Cloudflare 화면 **Workers & Pages** → `stocktrade-scheduler` → **Settings > Variables and Secrets** →
   **Add** → Type **Secret**, 이름 `GITHUB_TOKEN`, 값은 5-1의 토큰 → **Deploy**.
   비밀값은 다시 `wrangler deploy`를 해도 지워지지 않는다.

Worker는 인터넷 주소 없이 예약으로만 돈다. 실행 시각들의 '분'(17, 40, 47분)마다 깨어나서 시각이 맞을
때만 GitHub에 요청한다(무료 요금제는 계정당 예약이 5개까지라 실행마다 따로 두지 않았다).
예약 시각을 바꿀 때는 워크플로 파일의 cron과 `scheduler/worker.js`의 표를 같이 바꾸고 다시 올린다
(테스트가 둘이 같은지 확인한다).

### 5-3. 확인

- 다음 예약 시각이 지나면 GitHub **Actions** 탭에 그 시각에 시작한 실행이 생긴다. 이벤트는
  `workflow_dispatch`이고, **설정 표시** 단계에 `실행 방식: workflow_dispatch --once-per-day`가 나온다.
  나중에 GitHub 예약이 늦게 돌면 "오늘 이미 실행해서 건너뜁니다"로 바로 끝난다.
- Cloudflare 기록: Worker 화면 **Observability**(또는 **Logs**)에 `trade-kr.yml 실행 요청`이 남는다.
  `실행 요청 실패: HTTP 401`은 토큰 만료, `HTTP 403`이나 `404`는 토큰 권한이나 저장소 선택이 틀린 것이다.

### 5-4. 멈추기

- 예약 실행 전체를 멈추려면 4-5처럼 `SCHEDULE_ENABLED`를 끈다. Cloudflare가 요청해도 실행이 돌지 않는다.
- Cloudflare 예약만 멈추려면 Worker 화면 **Settings > Trigger Events**에서 Cron을 지우거나 Worker를
  지운다. GitHub 예약은 그대로 돈다.

## 6. 웹앱 (폰 앱)

엔진 기록을 폰에서 보고, 일시정지를 켜고 끄고, 실행 알림을 받는 앱이다(`webapp/`). Cloudflare Pages에
올려 무료로 쓴다. 1~5장을 먼저 끝낸다.

### 6-1. 로그인 계정 만들기 (Supabase)

1. **Authentication > Users > Add user > Create new user**: 앱에 로그인할 이메일과 비밀번호를 넣고
   **Auto Confirm User**를 켠다.
2. **SQL Editor**에서 그 계정을 소유자로 등록한다(이메일만 바꾼다).

   ```sql
   insert into public.owners (user_id)
   select id from auth.users where email = '내이메일@example.com'
   on conflict do nothing;
   ```
3. **Authentication > Sign In / Providers > Email**에서 **Allow new users to sign up**을 끈다.
   웹앱에는 가입 화면이 없고, 소유자가 아닌 계정은 로그인해도 아무것도 볼 수 없다(RLS).

### 6-2. 웹앱 설정 (`webapp/.env.local`)

`webapp/.env.example`을 `webapp/.env.local`로 복사하고 값을 넣는다. 셋 다 화면에 실리는 공개용 값이다.

| 이름 | 값 |
|---|---|
| `VITE_SUPABASE_URL` | 1장의 Project URL (`https://xxxx.supabase.co`) |
| `VITE_SUPABASE_PUBLISHABLE_KEY` | Project Settings > API Keys의 publishable 키 (`sb_publishable_...`) |
| `VITE_VAPID_PUBLIC_KEY` | `uv run python -m trading.notify.vapid`가 보여 주는 공개키(2장) |

secret 키(`sb_secret_...`)를 잘못 넣으면 웹앱이 실행을 거부한다. 웹 푸시 개인키는 저장소 루트 `.env`에만 둔다.

`VITE_VAPID_PUBLIC_KEY`는 GitHub Secrets에 넣을 `VAPID_PRIVATE_KEY`와 **짝이어야 한다**. 개인키가 든 `.env`가
있는 PC(6-5의 시험 알림과 GitHub Secrets를 넣을 PC)에서 `uv run python -m trading.notify.vapid`를 실행해 나온
공개키를 넣는다. 짝이 안 맞으면 푸시 서비스가 알림을 거부한다(403). 키를 바꾸면 폰에서 알림을 껐다 다시 켠다.

### 6-3. 이 PC에서 확인

```bash
cd webapp
npm ci
npm run dev    # http://localhost:5173 을 윈도 크롬에서 연다(실제 기록, 6-1 계정으로 로그인)
npm run demo   # 키 없이 데모 데이터로 화면만 본다
npm test       # 계산(비중, 성적, 시간 표기) 시험
```

### 6-4. Cloudflare Pages에 올리기

1. 처음 한 번: 5장과 같은 Cloudflare 계정으로 로그인하고 프로젝트를 만든다.

   ```bash
   cd webapp
   npx wrangler@4.153.0 login
   npx wrangler@4.153.0 pages project create stocktrade --production-branch main
   ```
2. 올린다. 빌드한 뒤 올리며, `.env.local` 값은 빌드할 때 들어가므로 Cloudflare 화면에 넣을 것은 없다.

   ```bash
   npm run deploy
   ```
3. 주소는 `https://stocktrade.pages.dev`다(같은 이름이 이미 있으면 올릴 때 나오는 주소를 쓴다).
   웹앱을 고치면 다시 `npm run deploy`.

#### 자동으로 올리기 (GitHub Actions, 권장)

한 번 설정해 두면 웹앱이 바뀌어 main에 머지될 때마다 `webapp-deploy` 워크플로가 시험하고 빌드해서 올린다.
위의 1~2단계(PC에서 로그인과 `npm run deploy`)는 하지 않아도 되고, Pages 프로젝트가 없으면 처음 실행 때 만든다.
PR(포크 포함)에서는 돌지 않는다.

1. Cloudflare 토큰 만들기: Cloudflare 화면 오른쪽 위 사람 아이콘 > **My Profile > API Tokens > Create Token >
   Custom token**. 권한은 **Account / Cloudflare Pages / Edit** 하나만 주고, Account Resources는 내 계정만 고른다.
   만든 토큰은 한 번만 보이니 바로 2번에 넣는다. 채팅이나 파일에 붙여 넣지 않는다.
2. GitHub 저장소 **Settings > Secrets and variables > Actions**:
   - **Secrets** 탭에 `CLOUDFLARE_API_TOKEN` = 1번 토큰
   - **Variables** 탭에 아래 넷. 모두 화면에 실리는 공개용 값이라 Variables에 둬도 된다.

   | 이름 | 값 |
   |---|---|
   | `CLOUDFLARE_ACCOUNT_ID` | Cloudflare 화면 **Workers & Pages** 오른쪽의 Account ID |
   | `VITE_SUPABASE_URL` | 6-2 표와 같다 |
   | `VITE_SUPABASE_PUBLISHABLE_KEY` | 6-2 표와 같다(`sb_publishable_...`, secret 키를 넣으면 워크플로가 멈춘다) |
   | `VITE_VAPID_PUBLIC_KEY` | 6-2 표와 같다(GitHub Secrets의 `VAPID_PRIVATE_KEY`와 짝) |
3. **Actions > webapp-deploy > Run workflow**로 한 번 돌린다. 값이 빠져 있으면 경고만 남기고 올리지 않는다.
   끝나면 로그 마지막 줄에 주소가 나온다.

### 6-5. 폰에 설치하고 알림 켜기 (안드로이드 크롬)

1. 폰 크롬에서 주소를 열고 메뉴(⋮) > **앱 설치**(또는 **홈 화면에 추가**).
2. 설치한 앱을 열고 6-1의 이메일과 비밀번호로 로그인한다.
3. 오른쪽 위 톱니(설정) > **이 기기 알림** > **알림 켜기** > 허용.
4. 이 PC에서 시험 알림을 보낸다. 루트 `.env`에 `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`,
   `VAPID_SUBJECT=mailto:본인메일주소`가 있어야 한다.

   ```bash
   uv run python -m trading.notify.check
   ```

   폰에 "[시험] 푸시 알림 확인"이 오고, 누르면 앱이 열리면 된다. 안 오면 폰 **설정 > 애플리케이션 >
   (설치한 앱 또는 Chrome) > 알림**이 허용인지, 배터리 절전 예외인지 본다. 앱 설정에서 알림을 껐다 켜도 된다.
5. GitHub Secrets에 `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT`를 넣는다(4-1 표, 값은 루트 `.env`).
   다음 실행부터 엔진이 폰으로 알림을 보낸다. 알림을 한 기기에도 못 보내면 실행이 실패로 끝나
   GitHub 메일이 온다.

### 6-6. 화면

- **홈**: 오늘 일어난 일(실행마다 체결·주문·주문 안 함·확인 필요), 시장별 총평가와 평가손익, 전략 비중
  (추세/반등/현금, 목표 50/50). 숫자 옆 기준 시각은 마지막 실행 시각이다. 웹앱은 시세를 따로 받지 않는다.
- **보유**: 종목별 평가손익과 산 이유. **주문**: 날짜별 주문, 상태, 체결, 근거, 증권사 메시지.
- **알림함**: 실행 기록. 알림을 누르면 그 실행이 열리고, 주문 근거와 주문하지 않은 이유가 나온다.
- **성적**: 계좌와 지수(KODEX 200, SPY)를 같은 날 100으로 맞춘 수익 곡선, 전략별 성적표.
  잔고와 지수 기록은 2026-11-10부터 쌓인다.
- **설정**: 일시정지(주문만 멈춤, 손절 매도는 계속), 이 기기 알림, 모의투자/실전 기록 보기.

## 7. LLM 보조 기능 (선택)

실행 요약과 매수 후보 악재 판정(섀도 모드)을 켠다. 둘 다 주문과 리스크 규칙에는 손대지 않고, 실패하면
LLM이 없는 것처럼 지나간다. 변수가 없으면 지금처럼 돈다.

### 7-1. 웹앱 설정 화면에서 넣기 (권장)

1. `supabase/schema.sql`을 적용한다(설정 표 `engine_settings`, Vault 함수, 보기 `llm_screens`가 생긴다).
   1-1의 Secret을 넣었으면 머지할 때 자동으로, 아니면 SQL Editor에서 한 번 더 실행한다.
2. 웹앱 **설정 > AI 기능**에서
   - **Anthropic API 키**: [Claude Console](https://platform.claude.com) > API Keys에서 만든 키(`sk-ant-...`)
   - (악재 판정, 국내) **OpenDART 인증키**: [OpenDART](https://opendart.fss.or.kr) 가입 후 신청(무료, 40자리)
   - **AI 사용**을 Claude로, 필요하면 **악재 판정**을 '기록만'으로, 미국 공시를 보려면 **SEC 연락처**
     (`stocktrade 본인메일주소`, SEC가 요청마다 연락처를 요구한다)를 넣고 **설정 저장**.
3. 다음 실행부터 쓴다.

- 키는 Supabase Vault에 암호화해 저장된다. 웹앱은 넣기와 지우기만 하고 다시 읽지 못한다(끝 4자리와 시각만 보인다).
  꺼내는 함수는 엔진의 secret 키로만 부를 수 있다.
- 모델은 비우면 `claude-opus-5-5`. 비용을 줄이려면 `claude-haiku-5-5`.
- 비용(대략): 실행마다 요약 1번 + 매수 신호 종목 수만큼 판정이라 하루 몇 번이다. `claude-opus-5-5`로 한 달
  몇 달러, `claude-haiku-5-5`로 몇십 센트 수준이다. Console의 Usage 화면에서 실제 사용량을 본다.
- 판정은 주문 앞에서 돌기 때문에 2분을 넘기면 남은 종목은 '판정 못 함'으로 두고 주문으로 넘어간다.
- 키가 없거나 설정이 틀려도 매매는 멈추지 않고 LLM 기능만 꺼진다(Actions 로그에 이유가 남는다).

### 7-1b. GitHub에 넣기 (대신 쓸 수 있음)

GitHub에 값이 있으면 웹앱 값보다 먼저 쓴다. 4-1, 4-2와 같은 화면에서 넣는다.

| 종류 | 이름 | 값 |
|---|---|---|
| Secret | `LLM_API_KEY` | Anthropic API 키 |
| Variable | `LLM_PROVIDER` | `anthropic` |
| Variable | `LLM_MODEL` | (선택) 모델 이름 |
| Variable | `LLM_SCREEN` | `shadow`(악재 판정 기록) 또는 넣지 않음 |
| Secret | `DART_API_KEY` | OpenDART 인증키 |
| Variable | `SEC_USER_AGENT` | `stocktrade 본인메일주소` |

저장소가 공개라서 Actions 로그는 누구나 볼 수 있다. Secrets는 로그에서 가려지지만 Variables에는 비밀값을 넣지 않는다.

### 7-2. 확인

1. 4-3처럼 손으로 시험 실행(dry_run)을 돌린다. 웹앱에서 넣었다면 **실행** 단계 로그에 `웹앱에서 넣은 설정을
   씁니다: llm_api_key, llm_provider, …`가 나온다(GitHub에 넣었다면 **설정 표시** 단계에 보인다).
   매수 신호가 있던 날이면 알림 맨 앞에 `요약(AI):` 줄과 `악재 판정(기록만, …)` 줄이 붙는다.

### 7-3. 섀도 모드 결과 보기

```sql
select started_at, market, symbol, strategy, verdict, reason
from llm_screens where mode = 'paper' order by started_at desc;
```

4~8주 모은 뒤 `VETO`(거부했을 것) 종목과 나머지의 이후 수익(주문 기록과 실행 기록의 가격)을 비교한다.
거부했을 종목이 뚜렷하게 나빴을 때만 판정을 주문에 반영하는 단계를 따로 만든다. 그 전까지는 기록만 한다.

### 7-4. 끄기

웹앱 **설정 > AI 기능**에서 끈다(GitHub에 넣었다면 `LLM_SCREEN`을 지우면 판정을, `LLM_PROVIDER`를 지우면
LLM 기능 전체를 끈다).
