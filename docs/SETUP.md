# 준비 안내

직접 해 주셔야 하는 준비 작업이다. 비밀값은 이 PC의 `.env`와 GitHub Secrets에만 넣는다.
대화창이나 다른 곳에 붙여 넣지 않는다. 대시보드 메뉴 이름은 Supabase가 바꿀 수 있으니 비슷한 이름을 찾으면 된다.

## 1. Supabase (기록 저장)

1. [supabase.com](https://supabase.com)에 가입하고 **New project**를 만든다.
   - Region은 **Northeast Asia (Seoul)** 을 고른다.
   - Database password는 엔진이 쓰지 않지만 잃어버리지 않게 따로 적어 둔다.
2. 왼쪽 **SQL Editor** → 새 쿼리를 열고, 이 저장소의 `supabase/schema.sql` 내용을 전부 붙여 넣어 **Run**.
   - "Success. No rows returned"가 나오면 된다. 여러 번 실행해도 괜찮다.
3. 접속 정보를 `.env`에 넣는다.
   - **Project URL** (`https://xxxx.supabase.co`): 프로젝트 화면의 **Connect** 버튼이나 Project Settings > Data API에 있다.
   - **secret 키** (`sb_secret_...`): Project Settings > API Keys에서 만들거나 확인한다.
     이 키는 모든 데이터를 읽고 쓰는 관리자 키다. `.env`와 GitHub Secrets 말고는 어디에도 두지 않는다.
     (publishable 키 `sb_publishable_...`는 M7 웹앱에서 쓰는 다른 키다)

   ```
   SUPABASE_URL=https://xxxx.supabase.co
   SUPABASE_SERVICE_KEY=sb_secret_...
   ```
4. 연결을 확인한다.

   ```bash
   uv run python -m trading.storage.check
   ```

   마지막 줄에 `Supabase 연결 확인 완료`가 나오면 된다.

참고: 무료 프로젝트는 일주일 동안 아무 요청이 없으면 일시 중지된다. 4장의 매일 자동 실행이 돌기 시작하면 걱정하지 않아도 된다.
그 전에 멈췄다면 대시보드에서 다시 켜면 된다.

## 2. 웹 푸시 키 (알림)

1. 키를 만든다. 개인키는 화면에 보이지 않고 `.env`에 바로 들어간다.

   ```bash
   uv run python -m trading.notify.vapid
   ```

   화면에 나오는 `VAPID_PUBLIC_KEY=...`는 M7 웹앱에서 쓴다(알려져도 괜찮은 값). 다시 실행하면 언제든 다시 볼 수 있다.
2. `.env`에 연락처를 넣는다. 푸시 서비스(구글)가 문제가 있을 때 연락하는 주소다.

   ```
   VAPID_SUBJECT=mailto:본인메일주소
   ```
3. 실제로 폰에 알림이 오는지는 M7에서 웹앱을 폰에 설치하고 알림을 켠 뒤에 확인한다.
   그 전에는 실행 결과가 Supabase `runs` 표와 GitHub Actions 로그에 남는다.
   - VAPID 개인키가 있는데 알림을 받을 기기가 없으면 실행이 실패로 끝난다(알림을 못 보낸 것도 실패로 친다).
     그래서 GitHub Secrets의 `VAPID_PRIVATE_KEY`는 M7에서 폰 알림을 켠 뒤에 넣는다.

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

엔진은 GitHub Actions에서 하루 한 번씩 돈다.
- 국내(`trade-kr`): 한국 시간 월~금 08:40
- 미국(`trade-us`): 한국 시간 월~금 23:40(뉴욕 장 시작 뒤)

GitHub 예약 실행은 몇 분에서 수십 분 늦게 시작할 수 있고, 아주 붐빌 때는 건너뛰기도 한다.
엔진이 휴장일과 장 시간을 다시 확인하고, 건너뛴 날의 신호는 다음 실행에서 대부분 따라잡는다.

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
| `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` | `.env`의 같은 이름 값 | M7에서 폰 알림을 켠 뒤 |
| `KIS_LIVE_APP_KEY`, `KIS_LIVE_APP_SECRET`, `KIS_LIVE_ACCOUNT_NO` | 실전 키와 실전 계좌번호 | 실전 전환 때 |

- 실전 키는 이 PC의 파일에 저장하지 않는다. KIS 사이트에서 복사해 GitHub 화면에 바로 붙여 넣는다.
- 한 번 넣은 값은 GitHub에서도 다시 볼 수 없다(바꾸기만 된다). 로그에는 `***`로 가려진다.

### 4-2. 변수 넣기

같은 화면의 **Variables** 탭에서 **New repository variable**로 넣는다. 비밀값이 아닌 설정이다.

| 이름 | 값 | 뜻 |
|---|---|---|
| `SCHEDULE_ENABLED` | `true` | 매일 예약 실행을 켠다. 없거나 다른 값이면 예약 실행이 돌지 않는다(손으로 실행은 된다) |
| `TRADING_MODE` | 넣지 않는다 | 없으면 모의투자다. 정확히 `live`일 때만 실전 키로 실전 계좌에 주문한다 |
| `MAX_DAILY_BUY_KRW`, `MAX_DAILY_BUY_USD` | 예: `3000000`, `2000` | (선택) 하루 매수 금액 상한 |

`SCHEDULE_ENABLED`는 4-3의 시험 실행이 성공한 뒤에 넣는다.

### 4-3. 손으로 시험 실행

1. 저장소 **Actions** 탭 → 왼쪽 **trade-kr** → 오른쪽 **Run workflow**
2. **시험 실행** 체크가 켜진 그대로 **Run workflow**를 누른다. 장 시간과 상관없이 잔고, 시세, 신호,
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

- 주문만 멈추기: 일시정지 스위치(M7 웹앱. 그 전에는 Supabase **Table Editor**의 `trading_switch`에서
  `paused`를 `true`로). 손절 매도는 계속 나간다.
- 실행 자체를 멈추기: 변수 `SCHEDULE_ENABLED`를 지우거나 `false`로 바꾼다.

### 4-6. 실전 전환 (M7 뒤에)

1. 4-1 표의 실전 키 3개를 넣는다.
2. 변수 `TRADING_MODE`를 `live`로 넣는다. 처음에는 `MAX_DAILY_BUY_KRW`/`MAX_DAILY_BUY_USD`를 작게 둔다.
3. 되돌리기: `TRADING_MODE` 변수를 지우면 바로 다음 실행부터 모의투자다.

실전은 main 브랜치 코드로만 돈다. 모의투자와 실전 기록은 Supabase에 따로 저장되고, KIS 토큰도 모드별로
따로 암호화해 Supabase `engine_cache`에 둔다(토큰은 하루 유효, 실행마다 새로 받지 않는다).

GitHub 무료 요금제의 비공개 저장소는 Actions를 한 달 2,000분까지 쓸 수 있다. 실행 한 번에 1~2분이라
하루 두 번이면 한 달 100분 안쪽이다.
