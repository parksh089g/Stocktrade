import pytest

from trading.core.models import Market
from trading.universe import load_universe


def test_project_universe_loads():
    universe = load_universe("config/universe.toml")
    assert universe[Market.KR]
    assert universe[Market.US]
    assert all(i.exchange for i in universe[Market.US])


def test_universe_file(tmp_path):
    path = tmp_path / "u.toml"
    path.write_text(
        '[[KR]]\nsymbol = "005930"\nname = "삼성전자"\n\n'
        '[[US]]\nsymbol = "AAPL"\nexchange = "NASDAQ"\n'
    )
    universe = load_universe(path)
    [kr] = universe[Market.KR]
    assert (kr.market, kr.symbol, kr.name) == (Market.KR, "005930", "삼성전자")
    assert universe[Market.US][0].exchange == "NASDAQ"


def test_universe_rejects_duplicates(tmp_path):
    path = tmp_path / "u.toml"
    path.write_text('[[KR]]\nsymbol = "005930"\n\n[[KR]]\nsymbol = "005930"\n')
    with pytest.raises(ValueError, match="중복"):
        load_universe(path)


def test_universe_rejects_unknown_market(tmp_path):
    path = tmp_path / "u.toml"
    path.write_text('[[JP]]\nsymbol = "7203"\n')
    with pytest.raises(ValueError, match="JP"):
        load_universe(path)
