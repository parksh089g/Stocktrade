"""백테스트용 일봉 불러오기."""

from pathlib import Path

import pandas as pd

from trading.brokers.base import BrokerAdapter
from trading.core.models import BAR_COLUMNS, Market, validate_bars

# 증권사에서 받은 일봉 저장 위치(git에 올리지 않는다)
CACHE_DIR = Path("data/bars")


def load_bars_csv(path: str | Path) -> pd.DataFrame:
    """date, open, high, low, close, volume 컬럼이 있는 CSV를 일봉 DataFrame으로 읽는다."""
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    if "date" not in df.columns:
        raise ValueError(f"{path}: date 컬럼이 없습니다")
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    return validate_bars(df[list(BAR_COLUMNS)])


def fetch_bars_cached(
    broker: BrokerAdapter,
    market: Market,
    symbol: str,
    days: int,
    *,
    cache_dir: Path = CACHE_DIR,
    refresh: bool = False,
) -> tuple[pd.DataFrame, bool]:
    """증권사에서 일봉을 받아 CSV로 저장해 두고 다시 쓴다. (일봉, 저장본을 썼는지)"""
    path = cache_dir / f"{market}_{symbol}.csv"
    if path.exists() and not refresh:
        cached = load_bars_csv(path)
        if len(cached) >= days:
            return cached.tail(days), True
    bars = broker.get_daily_bars(market, symbol, days)
    path.parent.mkdir(parents=True, exist_ok=True)
    bars.to_csv(path, index_label="date")
    return bars, False
