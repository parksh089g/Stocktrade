# stocktrade

국내/미국 주식 일봉 기반 자동 매매. 규칙은 [CLAUDE.md](CLAUDE.md), 진행 상황은 [docs/ROADMAP.md](docs/ROADMAP.md),
Supabase/웹 푸시 준비는 [docs/SETUP.md](docs/SETUP.md).

## 로컬 준비

```bash
uv sync
cp .env.example .env   # 모의투자 키만 넣는다. 기본값은 paper + fake 증권사
uv run pytest
uv run ruff check
```

## 백테스트

```bash
uv run python -m trading.backtest                    # 가짜 데이터로 두 전략 동작 확인
uv run python -m trading.backtest --csv 005930.csv --market KR --strategy trend_ma --trades
```

CSV는 `date,open,high,low,close,volume` 컬럼이 있으면 된다. 신호는 다음 날 시가에 체결하고,
수수료/세금은 `trading/backtest/simulator.py`의 `DEFAULT_COSTS`(대략값)를 쓴다.

실제 시세(KIS, `data/bars/`에 저장해 다시 씀):

```bash
uv run python -m trading.backtest --source kis --days 1500               # 종목별
uv run python -m trading.backtest --source kis --portfolio --days 1500   # 계좌 전체(실제 엔진 규칙)
uv run python -m trading.backtest.compare                                # 개선안 비교(앞 기간에서 고르고 뒤 기간에서 확인)
```

시험 결과와 결론은 [docs/EXPERIMENTS.md](docs/EXPERIMENTS.md)에 남긴다.

## 하루 실행

GitHub Actions가 평일마다 시장별로 돌린다(`.github/workflows/trade-kr.yml`, `trade-us.yml`,
장 마감 전 손절 확인 `stoploss-kr.yml`, `stoploss-us.yml`, 공통 단계는 `engine.yml`). 준비는 [docs/SETUP.md](docs/SETUP.md) 4장. 모드는 저장소 변수 `TRADING_MODE`로
정하고, 없으면 모의투자다. 올릴 때마다 `ci.yml`이 테스트와 린트를 돌린다.

GitHub 예약은 몇 시간씩 늦을 때가 있어서, Cloudflare Worker(`scheduler/`)가 정해진 시각에 이 워크플로들을
부른다. GitHub 예약은 대비용으로 남겨 두고, 엔진이 하루 한 번만 돌게 막는다. 준비는 SETUP.md 5장.
Worker 테스트는 `node --test scheduler/worker.test.js`.

로컬에서는 fake 증권사로 흐름을 확인한다.

```bash
uv run python -m trading.engine --market KR
uv run python -m trading.engine --market US --ignore-hours --seed 11   # 장 시간과 상관없이 흐름 확인
uv run python -m trading.engine --market KR --ignore-hours --dry-run --stop-loss-only  # 손절 확인
```

흐름: 스위치 확인 → 지난 주문 체결 반영 → (일시정지/휴장/장 마감이면 끝) → 계좌 조회 → 시세와 신호
→ 리스크(배분/한도/중복) → 지정가 주문 → 기록 → 알림. 종목은 `config/universe.toml`, 리스크 값은
`trading/risk/config.py`, 하루 매수 상한은 `.env`의 `MAX_DAILY_BUY_KRW`/`MAX_DAILY_BUY_USD`.

## 기록과 알림

- 기록: Supabase(`supabase/schema.sql`). 모의투자/실전 기록은 따로 저장된다. 연결 확인 `uv run python -m trading.storage.check`
- 알림: 웹앱 푸시. 키 만들기 `uv run python -m trading.notify.vapid`, 폰으로 시험 알림 `uv run python -m trading.notify.check`.
  알림을 한 기기에도 못 보내면 실행이 실패로 끝나 GitHub Actions 실패 메일이 백업 알림이 된다.
- fake 증권사로 실행할 때는 가짜 주문이 실제 기록에 섞이지 않도록 메모리 저장소와 화면 출력만 쓴다.

## LLM 보조 기능(선택)

`trading/llm/`. LLM은 주문을 내거나 리스크 규칙을 바꾸지 않는다. 설정은 [docs/SETUP.md](docs/SETUP.md) 7장.
- 실행 요약: 주문·체결·주문 안 함이 있던 실행의 결과를 2~3줄로 풀어 알림 맨 앞에 붙인다. 결과에 없는 가격이나
  수량을 쓰면 요약을 버린다.
- 매수 후보 악재 판정(섀도 모드): 매수 신호 종목의 최근 2주 공시(국내 DART, 미국 SEC EDGAR)를 보고 유상증자,
  거래정지, 감사의견 거절 같은 악재가 있는지 판정해 기록만 한다. 주문은 판정과 상관없이 그대로 나간다.
  Supabase 보기 `llm_screens`로 모아 보고, 몇 주 뒤 효과를 확인해 반영할지 정한다. 매도와 손절은 판정하지 않는다.
- LLM이나 공시 조회가 실패하면 LLM이 없는 것처럼 지나간다(경고나 긴급 알림을 만들지 않는다).

## 웹앱(폰)

`webapp/`: Vite + TypeScript(Preact) PWA. 기록 보기(홈, 보유, 주문, 알림함, 성적), 일시정지 스위치, 푸시 알림.
Supabase Auth로 로그인하고 publishable 키로 읽는다(RLS: 소유자만 읽기, 바꾸는 것은 스위치와 자기 푸시 구독뿐).
준비와 배포는 [docs/SETUP.md](docs/SETUP.md) 6장.

```bash
cd webapp
npm ci
npm run demo     # 키 없이 데모 데이터로 화면 보기
npm run dev      # 실제 기록(webapp/.env.local 필요)
npm test         # 계산 시험, npm run typecheck로 타입 검사
npm run deploy   # Cloudflare Pages에 올리기
```

## 증권사(KIS) 연결

- 준비: [docs/SETUP.md](docs/SETUP.md)의 "KIS 모의투자 키"
- 연결 확인(조회만): `uv run python -m trading.brokers.kis_check`
- 시험 실행(주문 없이 계산만): `BROKER=kis uv run python -m trading.engine --market KR --dry-run`
- 미국 주식은 KIS 일반 주문이 정규장에만 되므로 장 시작 뒤(뉴욕 09:30~)에 실행한다.
