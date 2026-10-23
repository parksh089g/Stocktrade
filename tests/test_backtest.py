from datetime import date

import pandas as pd
import pytest

from trading.backtest.__main__ import main
from trading.backtest.data import load_bars_csv
from trading.backtest.simulator import Costs, max_drawdown, run_backtest
from trading.brokers.fake import FakeBroker, make_random_bars
from trading.core.models import Market, Position, Side, Signal
from trading.strategies import RsiRebound, TrendMA
from trading.strategies.base import Strategy

NO_COSTS = Costs(fee_rate=0.0, sell_tax_rate=0.0)


class Scripted(Strategy):
    """정해진 날짜에 정해진 신호를 내고, 넘겨받은 데이터를 기록한다."""

    id = "scripted"

    def __init__(self, actions: dict[date, Side], lookback: int = 3) -> None:
        self.actions = actions
        self._lookback = lookback
        self.seen: list[tuple[date, int, Position | None]] = []

    @property
    def lookback(self) -> int:
        return self._lookback

    def _evaluate(self, market, symbol, bars, position) -> Signal | None:
        day = bars.index[-1].date()
        self.seen.append((day, len(bars), position))
        side = self.actions.get(day)
        return self._signal(market, symbol, side, bars, "scripted") if side else None


@pytest.fixture
def bars() -> pd.DataFrame:
    # i번째 날: 시가 100+i, 종가 100.5+i
    n = 10
    opens = [100.0 + i for i in range(n)]
    closes = [o + 0.5 for o in opens]
    return pd.DataFrame(
        {
            "open": opens,
            "high": [c + 1 for c in closes],
            "low": [o - 1 for o in opens],
            "close": closes,
            "volume": 1000,
        },
        index=pd.bdate_range("2026-01-05", periods=n),
    )


def day(bars: pd.DataFrame, i: int) -> date:
    return bars.index[i].date()


def test_signal_fills_next_open(bars):
    strategy = Scripted({day(bars, 3): Side.BUY, day(bars, 6): Side.SELL})
    result = run_backtest(strategy, bars, market=Market.KR, initial_cash=10_000, costs=NO_COSTS)

    [trade] = result.trades
    assert (trade.entry_date, trade.entry_price) == (day(bars, 4), 104.0)
    assert (trade.exit_date, trade.exit_price) == (day(bars, 7), 107.0)
    assert trade.qty == 96  # 10000 // 104
    assert trade.pnl == pytest.approx(96 * 3)
    assert trade.exit_reason == "scripted"
    assert result.equity.iloc[4] == pytest.approx(16 + 96 * 104.5)
    assert result.equity.iloc[-1] == pytest.approx(10_288)
    assert result.metrics.total_return == pytest.approx(0.0288)
    assert result.metrics.exposure == pytest.approx(0.3)


def test_strategy_never_sees_future(bars):
    strategy = Scripted({day(bars, 3): Side.BUY, day(bars, 6): Side.SELL})
    run_backtest(strategy, bars, market=Market.KR, initial_cash=10_000, costs=NO_COSTS)

    # lookback 3개가 모이는 2번째 날부터, 체결할 다음 날이 있는 8번째 날까지
    assert [d for d, _, _ in strategy.seen] == [day(bars, i) for i in range(2, 9)]
    assert all(n == 3 for _, n, _ in strategy.seen)
    held = [p is not None for _, _, p in strategy.seen]
    assert held == [False, False, True, True, True, False, False]
    pos = strategy.seen[2][2]
    assert pos is not None
    assert (pos.qty, pos.avg_price, pos.strategy) == (96, 104, "scripted")


def test_costs_are_applied(bars):
    strategy = Scripted({day(bars, 3): Side.BUY, day(bars, 6): Side.SELL})
    costs = Costs(fee_rate=0.001, sell_tax_rate=0.002)
    result = run_backtest(strategy, bars, market=Market.KR, initial_cash=10_000, costs=costs)

    [trade] = result.trades
    buy_cost = 96 * 104 * 1.001
    proceeds = 96 * 107 * (1 - 0.003)
    assert trade.pnl == pytest.approx(proceeds - buy_cost)
    assert result.equity.iloc[-1] == pytest.approx(10_000 - buy_cost + proceeds)


def test_open_position_closed_on_last_day(bars):
    strategy = Scripted({day(bars, 7): Side.BUY})
    result = run_backtest(strategy, bars, market=Market.KR, initial_cash=10_000, costs=NO_COSTS)

    [trade] = result.trades
    assert trade.entry_date == day(bars, 8)
    assert (trade.exit_date, trade.exit_price) == (day(bars, 9), 109.5)
    assert trade.exit_reason == "백테스트 종료"


def test_max_drawdown():
    assert max_drawdown(pd.Series([100.0, 120.0, 90.0, 130.0])) == pytest.approx(-0.25)


@pytest.mark.parametrize("strategy", [TrendMA(), RsiRebound()], ids=lambda s: s.id)
def test_real_strategies_run_on_random_data(strategy):
    data = make_random_bars(500, seed=3)
    a = run_backtest(strategy, data, market=Market.KR)
    b = run_backtest(strategy, data, market=Market.KR)

    assert len(a.equity) == 500
    assert (a.equity > 0).all()
    assert a.equity.equals(b.equity)  # 같은 입력이면 같은 결과
    for t in a.trades:
        assert t.entry_date <= t.exit_date
    for prev, nxt in zip(a.trades, a.trades[1:], strict=False):
        assert prev.exit_date < nxt.entry_date


def test_cli_runs_both_strategies(capsys):
    main(["--days", "300"])
    out = capsys.readouterr().out
    assert "[trend_ma]" in out
    assert "[rsi_rebound]" in out
    assert "가짜 데이터" in out


def test_load_bars_csv(tmp_path):
    path = tmp_path / "bars.csv"
    path.write_text(
        "Date,Open,High,Low,Close,Volume\n"
        "2026-01-06,11,12,10,11.5,100\n"
        "2026-01-05,10,11,9,10.5,100\n"
    )
    bars = load_bars_csv(path)
    assert list(bars.index.date) == [date(2026, 1, 5), date(2026, 1, 6)]
    assert bars["close"].tolist() == [10.5, 11.5]

    bad = tmp_path / "bad.csv"
    bad.write_text("open,high,low,close,volume\n1,1,1,1,1\n")
    with pytest.raises(ValueError, match="date"):
        load_bars_csv(bad)


# ---- KIS 실제 일봉 ----


def test_fetch_bars_cached(tmp_path):
    from trading.backtest.data import fetch_bars_cached

    class CountingBroker(FakeBroker):
        calls = 0

        def get_daily_bars(self, market, symbol, count):
            CountingBroker.calls += 1
            return super().get_daily_bars(market, symbol, count)

    broker = CountingBroker()
    broker.set_bars(Market.KR, "005930", make_random_bars(300, seed=1))

    first, cached = fetch_bars_cached(broker, Market.KR, "005930", 200, cache_dir=tmp_path)
    assert not cached and len(first) == 200
    again, cached = fetch_bars_cached(broker, Market.KR, "005930", 150, cache_dir=tmp_path)
    assert cached and len(again) == 150
    assert CountingBroker.calls == 1
    pd.testing.assert_frame_equal(again, first.tail(150), check_freq=False, check_names=False)

    # 저장본보다 많이 원하거나 새로 받으라고 하면 다시 받는다
    fetch_bars_cached(broker, Market.KR, "005930", 250, cache_dir=tmp_path)
    fetch_bars_cached(broker, Market.KR, "005930", 100, cache_dir=tmp_path, refresh=True)
    assert CountingBroker.calls == 3


def test_cli_kis_source(tmp_path, monkeypatch, capsys):
    from pathlib import Path

    import trading.backtest.__main__ as cli
    from trading.core.models import Instrument

    universe_path = Path("config/universe.toml").resolve()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("UNIVERSE_PATH", str(universe_path))
    broker = FakeBroker()
    for i, symbol in enumerate(["005930", "000660"]):
        broker.set_bars(Market.KR, symbol, make_random_bars(300, seed=i))
    monkeypatch.setattr(cli, "build_kis_broker", lambda settings, universe: broker)
    monkeypatch.setattr(
        cli,
        "load_universe",
        lambda path: {
            Market.KR: [
                Instrument(market=Market.KR, symbol="005930", name="삼성전자"),
                Instrument(market=Market.KR, symbol="000660", name="SK하이닉스"),
            ],
            Market.US: [],
        },
    )

    assert cli.main(["--source", "kis", "--market", "KR", "--days", "250"]) == 0
    out = capsys.readouterr().out
    assert "(trend_ma)" in out and "(rsi_rebound)" in out
    assert "삼성전자" in out and "평균" in out
    assert (tmp_path / "data/bars/KR_005930.csv").exists()


def test_cli_portfolio(tmp_path, monkeypatch, capsys):
    import trading.backtest.__main__ as cli
    from trading.core.models import Instrument

    monkeypatch.chdir(tmp_path)
    broker = FakeBroker()
    for i, symbol in enumerate(["005930", "000660", "069500"]):
        broker.set_bars(Market.KR, symbol, make_random_bars(300, start_price=50_000, seed=i))
    monkeypatch.setattr(cli, "build_kis_broker", lambda settings, universe: broker)
    monkeypatch.setattr(
        cli,
        "load_universe",
        lambda path: {
            Market.KR: [
                Instrument(market=Market.KR, symbol="005930"),
                Instrument(market=Market.KR, symbol="000660"),
            ],
            Market.US: [],
        },
    )
    assert cli.main(["--source", "kis", "--portfolio", "--market", "KR", "--days", "300"]) == 0
    out = capsys.readouterr().out
    assert "[국내 계좌]" in out
    assert "KODEX 200 그냥 보유" in out
    assert "전략별 손익" in out
