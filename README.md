# stocktrade

국내/미국 주식 일봉 기반 자동 매매. 규칙은 [CLAUDE.md](CLAUDE.md), 진행 상황은 [docs/ROADMAP.md](docs/ROADMAP.md).

## 로컬 준비

```bash
uv sync
cp .env.example .env   # 모의투자 키만 넣는다. 기본값은 paper + fake 증권사
uv run pytest
uv run ruff check
```
