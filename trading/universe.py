"""매매 대상 종목 목록(config/universe.toml) 읽기."""

import tomllib
from pathlib import Path

from trading.core.models import Instrument, Market

Universe = dict[Market, list[Instrument]]

# 계좌 성적을 견줄 시장 지수 ETF. 엔진은 매매 실행마다 종가를 기록하고(웹앱 수익 곡선),
# 백테스트는 비교 기준으로 쓴다. 매매 대상은 아니다.
BENCHMARKS: dict[Market, Instrument] = {
    Market.KR: Instrument(market=Market.KR, symbol="069500", name="KODEX 200"),
    Market.US: Instrument(market=Market.US, symbol="SPY", name="S&P500(SPY)", exchange="AMEX"),
}


def load_universe(path: str | Path) -> Universe:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    unknown = set(data) - {m.value for m in Market}
    if unknown:
        raise ValueError(f"{path}: 알 수 없는 시장 {sorted(unknown)}")

    universe: Universe = {}
    for market in Market:
        items = [Instrument(market=market, **item) for item in data.get(market.value, [])]
        symbols = [i.symbol for i in items]
        if len(symbols) != len(set(symbols)):
            raise ValueError(f"{path}: {market} 종목이 중복됐습니다")
        universe[market] = items
    return universe
