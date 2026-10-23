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
