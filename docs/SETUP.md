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
