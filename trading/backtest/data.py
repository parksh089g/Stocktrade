"""백테스트용 일봉 불러오기."""

from pathlib import Path

import pandas as pd

from trading.core.models import BAR_COLUMNS, validate_bars


def load_bars_csv(path: str | Path) -> pd.DataFrame:
    """date, open, high, low, close, volume 컬럼이 있는 CSV를 일봉 DataFrame으로 읽는다."""
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    if "date" not in df.columns:
        raise ValueError(f"{path}: date 컬럼이 없습니다")
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    return validate_bars(df[list(BAR_COLUMNS)])
