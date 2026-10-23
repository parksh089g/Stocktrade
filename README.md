# stocktrade

국내/미국 주식 일봉 기반 자동 매매. 규칙은 [CLAUDE.md](CLAUDE.md), 진행 상황은 [docs/ROADMAP.md](docs/ROADMAP.md).

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

## 하루 실행 (지금은 fake 증권사)

```bash
uv run python -m trading.engine --market KR
uv run python -m trading.engine --market US --ignore-hours --seed 11   # 장 시간과 상관없이 흐름 확인
```

흐름: 스위치 확인 → 지난 주문 체결 반영 → (일시정지/휴장/장 마감이면 끝) → 계좌 조회 → 시세와 신호
→ 리스크(배분/한도/중복) → 지정가 주문 → 기록 → 알림. 종목은 `config/universe.toml`, 리스크 값은
`trading/risk/config.py`, 하루 매수 상한은 `.env`의 `MAX_DAILY_BUY_KRW`/`MAX_DAILY_BUY_USD`.
