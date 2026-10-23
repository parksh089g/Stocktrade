# 준비 안내

직접 해 주셔야 하는 준비 작업이다. 비밀값은 이 PC의 `.env`(그리고 M6에서 GitHub Secrets)에만 넣는다.
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

참고: 무료 프로젝트는 일주일 동안 아무 요청이 없으면 일시 중지된다. M6에서 매일 자동 실행이 돌기 시작하면 걱정하지 않아도 된다.
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

## 3. KIS 모의투자 키 (증권사 연결)

모의투자 키만 이 PC에 넣는다. 실전 키는 이 PC에 두지 않는다(M6에서 GitHub Secrets에만 넣는다).

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
