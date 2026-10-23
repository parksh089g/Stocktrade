import pandas as pd


def bars_from_closes(closes: list[float], start: str = "2026-01-05") -> pd.DataFrame:
    """종가만으로 만든 일봉(시가=고가=저가=종가)."""
    close = pd.Series(closes, dtype=float)
    return pd.DataFrame(
        {"open": close, "high": close, "low": close, "close": close, "volume": 1000},
    ).set_index(pd.bdate_range(start, periods=len(closes)))
