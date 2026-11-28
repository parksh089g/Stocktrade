# 주식 자동 매매 시스템

국내와 미국 주식을 일봉 기반 전략으로 자동 매매한다. 엔진은 GitHub Actions에서 하루 1~2번 실행되고, 기록은 Supabase, 알림은 폰 웹앱(PWA)의 웹 푸시, 확인과 일시정지도 같은 웹앱으로 한다. 유료 서버는 쓰지 않는다.

## 기술
- Python 3.12, uv, pandas, httpx, pydantic-settings, pytest, ruff
- 웹앱: Vite + TypeScript 정적 PWA, Cloudflare Pages 배포, Supabase Auth, 웹 푸시(VAPID, 엔진은 pywebpush로 발송)

## 구조
- trading/core: Order, Position, Signal 같은 공통 모델
- trading/brokers: base.py(BrokerAdapter 인터페이스), kis.py, fake.py
- trading/strategies: base.py(Strategy 인터페이스), trend_ma.py(① 추세 추종), rsi_rebound.py(② 단기 반등)
- 전략별 자금 배분: 계좌의 50%씩. 포지션은 어느 전략이 산 것인지 기록하고, 같은 종목의 합산 비중이 종목당 한도를 넘지 않게 한다.
- trading/risk: 리스크 규칙
- trading/engine: 하루 실행 흐름 (스위치 확인 → 시세 → 신호 → 리스크 → 주문 → 기록 → 알림)
- trading/notify, trading/storage, trading/backtest
- webapp/: 폰 웹앱
- scheduler/: 예약 실행을 정시에 부르는 Cloudflare Worker(GitHub 예약이 늦을 때 대비)
- .github/workflows/: trade-kr.yml, trade-us.yml

## 규칙
- 기본 모드는 모의투자(TRADING_MODE=paper). live는 사람이 직접 설정할 때만 켜진다. 코드에서 기본값을 live로 바꾸지 않는다.
- 증권사에 의존하는 코드는 trading/brokers 안에만 둔다. 나중에 미래에셋 어댑터를 추가할 수 있어야 한다.
- 비밀값(API 키, 토큰)은 로컬 .env와 GitHub Secrets에만 둔다. 예외: LLM·DART API 키는 웹앱 설정 화면에서 넣어 Supabase Vault에 암호화해 둘 수 있다(웹앱은 넣기/지우기만, 꺼내는 건 엔진만). 웹 푸시 개인키도 웹앱이 폰 브라우저에서 만들어 Vault에 바로 넣을 수 있다(화면에 보여 주지 않고, 꺼내는 건 엔진만). 절대 커밋하지 않고 로그에도 찍지 않는다.
- 실전 API 키는 이 PC에 두지 않는다. 로컬에서는 모의투자 키와 fake 증권사만 쓴다.
- 모든 주문 전에 일시정지 스위치와 리스크 규칙을 확인한다. 오류가 나면 주문하지 않고 알림만 보낸다.
- 같은 날 같은 종목 중복 주문을 막는다.
- KIS 제약: 해외는 지정가 주문만, 해외 조회는 초당 1회, 토큰은 24시간 유효하니 캐시해서 재사용한다.
- 시간은 UTC로 저장하고 화면과 알림에는 한국 시간으로 보여준다.
- 주문, 리스크, 엔진 코드를 바꾸면 fake 증권사로 테스트를 추가하고 `uv run pytest`와 `uv run ruff check`를 통과시킨다.
- 웹앱(webapp/)을 바꾸면 `npm test`와 `npm run typecheck`를 통과시키고, 화면은 `npm run demo`(데모 데이터)로 확인한다. 웹앱에는 공개용 값(publishable 키, VAPID 공개키)만 넣는다.
- 한 번에 마일스톤 하나만 작업하고, 끝나면 무엇을 했는지와 내가 확인할 것을 요약한다.
