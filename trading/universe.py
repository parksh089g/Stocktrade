"""매매 대상 종목 목록(config/universe.toml) 읽기."""

import tomllib
from pathlib import Path

from trading.core.models import Instrument, Market

Universe = dict[Market, list[Instrument]]


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
