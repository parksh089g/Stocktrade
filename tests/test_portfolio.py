"""계좌 전체 백테스트(실제 엔진을 과거 날짜마다 돌리기)."""

from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from trading.backtest.portfolio import (
    HistoricalBroker,
    PortfolioResult,
    combine,
    run_portfolio_backtest,
    yearly_returns,
)
from trading.backtest.simulator import Costs
from trading.brokers.fake import make_random_bars
from trading.core.models import Instrument, Market, Side
from trading.risk.config import RiskConfig
from trading.strategies import RsiRebound, TrendMA
from trading.strategies.base import Strategy

KR = Market.KR
NO_COSTS = Costs(fee_rate=0.0, sell_tax_rate=0.0)
DAYS = pd.bdate_range("2026-01-05", periods=12)


def flat_bars(price=100.0, changes=None) -> pd.DataFrame:
    """매일 시가=고가=저가=종가=price. changes={i: (open, high, low, close)}로 날마다 바꾼다."""
    changes = changes or {}
    rows = []
    for i in range(len(DAYS)):
        o, h, low, c = changes.get(i, (price, price, price, price))
        rows.append({"open": o, "high": h, "low": low, "close": c, "volume": 1000})
    return pd.DataFrame(rows, index=DAYS)


class Dated(Strategy):
    """(종목, 일봉 날짜)에 정해 둔 신호를 낸다."""

    lookback = 3

    def __init__(self, sid: str, actions: dict[tuple[str, int], Side]) -> None:
        self.id = sid
        self.actions = {(s, DAYS[i].date()): side for (s, i), side in actions.items()}

    def _evaluate(self, market, symbol, bars, position):
        side = self.actions.get((symbol, bars.index[-1].date()))
        return self._signal(market, symbol, side, bars, "시험") if side else None


def run(bars: dict[str, pd.DataFrame], actions, cash=1_000_000.0) -> PortfolioResult:
    return run_portfolio_backtest(
        KR,
        bars,
        [Instrument(market=KR, symbol=s) for s in bars],
        [Dated("a", actions), Dated("b", {})],
        initial_cash=cash,
        costs=NO_COSTS,
        risk=RiskConfig(strategy_weights={"a": Decimal("0.5"), "b": Decimal("0.5")}),
    )


def test_historical_broker_shows_only_past_bars():
    broker = HistoricalBroker(KR, {"AAA": flat_bars()}, 1_000_000, NO_COSTS)
    broker.start_day(DAYS[5])
    bars = broker.get_daily_bars(KR, "AAA", 99)
    assert bars.index[-1] == DAYS[4]
    assert len(bars) == 5
    assert broker.is_trading_day(KR, DAYS[5].date())
    assert not broker.is_trading_day(KR, date(2026, 1, 10))  # 토요일
    assert broker.now().hour == 8 and broker.now().minute == 40


def test_buy_at_next_open_then_sell():
    # 3일째 봉을 보고 매수 → 4일째 시가(100)에 체결, 6일째 봉을 보고 매도 → 7일째 시가(120)
    changes = {6: (100, 120, 100, 120)} | {i: (120, 120, 120, 120) for i in range(7, 12)}
    bars = {"AAA": flat_bars(100, changes)}
    result = run(bars, {("AAA", 3): Side.BUY, ("AAA", 6): Side.SELL})

    # 1,000,000 × 0.5 / 4 = 125,000 → 지정가 101(호가 1원)로 1,233주(수수료 여유 0.3%)
    qty = int(Decimal(125_000) / (Decimal(101) * Decimal("1.003")))
    assert result.strategy_buys == {"a": 1, "b": 0}
    assert result.strategy_pnl["a"] == pytest.approx(qty * 20)  # 100에 사서 120에 팖
    assert result.equity.iloc[-1] == pytest.approx(1_000_000 + qty * 20)
    assert result.filled == 2 and result.expired == 0
    assert result.equity.index[0] == DAYS[3]  # 지표 준비 기간(3일)은 뺀다
    assert result.invested.max() > 0.1


def test_gap_up_then_intraday_touch_fills_at_limit():
    # 신호 다음 날 시가 105로 지정가(101)보다 높게 시작했지만 장중 저가 99 → 101에 체결
    bars = {"AAA": flat_bars(100, {4: (105, 106, 99, 104)})}
    result = run(bars, {("AAA", 3): Side.BUY})
    assert result.filled == 1
    assert result.strategy_pnl["a"] == pytest.approx(
        int(Decimal(125_000) / (Decimal(101) * Decimal("1.003"))) * (100 - 101)
    )


def test_limit_never_touched_expires():
    bars = {"AAA": flat_bars(100, {i: (110, 112, 108, 110) for i in range(4, 12)})}
    result = run(bars, {("AAA", 3): Side.BUY})
    assert (result.filled, result.expired) == (0, 1)
    assert result.equity.iloc[-1] == pytest.approx(1_000_000)


def test_real_strategies_run_on_random_portfolio():
    bars = {f"S{i}": make_random_bars(400, start_price=50_000, seed=i) for i in range(4)}
    result = run_portfolio_backtest(
        KR,
        bars,
        [Instrument(market=KR, symbol=s) for s in bars],
        [TrendMA(), RsiRebound()],
        initial_cash=20_000_000,
    )
    assert len(result.equity) == 400 - RsiRebound().lookback
    assert (result.equity > 0).all()
    # 현금보다 많이 사지 않는다(미수 없음)
    assert result.invested.min() >= 0
    assert result.invested.max() <= 1
    assert result.filled > 0
    assert result.rejected == 0


def test_yearly_returns_and_combine():
    idx = pd.to_datetime(["2025-12-30", "2025-12-31", "2026-06-30", "2026-12-31"])
    equity = pd.Series([100.0, 110.0, 99.0, 121.0], index=idx)
    yearly = yearly_returns(equity, 100.0)
    assert yearly[2025] == pytest.approx(0.10)
    assert yearly[2026] == pytest.approx(0.10)

    kr = PortfolioResult(KR, 100.0, equity, equity * 0, {}, {}, 0, 0, 0)
    us = PortfolioResult(Market.US, 10.0, pd.Series([10.0, 10.0], index=idx[2:]), equity * 0,
                         {}, {}, 0, 0, 0)  # fmt: skip
    curve = combine([kr, us], [0.5, 0.5])
    assert curve.iloc[0] == pytest.approx(0.5 * 1.0 + 0.5 * 1.0)  # 미국은 늦게 시작 → 1.0으로 채움
    assert curve.iloc[-1] == pytest.approx(0.5 * 1.21 + 0.5 * 1.0)


def test_two_symbols_same_day_are_both_bought():
    bars = {"AAA": flat_bars(100), "BBB": flat_bars(100)}
    result = run(bars, {("AAA", 3): Side.BUY, ("BBB", 3): Side.BUY})
    assert result.filled == 2
    assert result.strategy_buys["a"] == 2


# ---- 현금을 지수 ETF로 굴리기(시험용) ----


def run_parked(bars, actions, park, cash=1_000_000.0, **kw) -> PortfolioResult:
    return run_portfolio_backtest(
        KR,
        bars,
        [Instrument(market=KR, symbol=s) for s in bars],
        [Dated("a", actions), Dated("b", {})],
        initial_cash=cash,
        costs=Costs(fee_rate=0.001, sell_tax_rate=0.0),
        risk=RiskConfig(strategy_weights={"a": Decimal("0.5"), "b": Decimal("0.5")}),
        park=park,
        **kw,
    )


def test_idle_cash_follows_etf():
    etf = flat_bars(1_000, {i: (1_000, 1_100, 1_000, 1_100) for i in range(5, 12)})
    result = run_parked({"AAA": flat_bars(100)}, {}, etf)
    # 첫날 종가 1,000에 거의 전부 ETF로 → ETF가 10% 오르면 계좌도 거의 10% 오른다
    assert result.parked.iloc[0] > 0.99
    assert result.equity.iloc[-1] == pytest.approx(1_000_000 * 1.1, rel=0.003)
    assert result.invested.max() == 0


def test_strategy_buy_is_funded_by_selling_etf():
    etf = flat_bars(1_000)
    result = run_parked({"AAA": flat_bars(100)}, {("AAA", 4): Side.BUY}, etf)
    assert (result.filled, result.rejected) == (1, 0)
    assert result.strategy_buys["a"] == 1
    assert result.invested.iloc[-1] == pytest.approx(0.125, abs=0.01)  # 한 번 매수 = 12.5%
    assert result.parked.iloc[-1] == pytest.approx(0.875, abs=0.01)


def test_period_runs_as_a_fresh_account():
    result = run({"AAA": flat_bars(100)}, {}, cash=500_000)
    late = run_portfolio_backtest(
        KR,
        {"AAA": flat_bars(100)},
        [Instrument(market=KR, symbol="AAA")],
        [Dated("a", {}), Dated("b", {})],
        initial_cash=500_000,
        costs=NO_COSTS,
        risk=RiskConfig(strategy_weights={"a": Decimal("0.5"), "b": Decimal("0.5")}),
        start=DAYS[8],
    )
    assert late.equity.index[0] == DAYS[8]
    assert late.equity.iloc[0] == result.equity.iloc[0] == 500_000


def test_compare_tool_picks_by_first_period_only(tmp_path, monkeypatch, capsys):
    import trading.backtest.compare as compare
    from trading.brokers.fake import FakeBroker

    monkeypatch.chdir(tmp_path)
    broker = FakeBroker()
    for i, symbol in enumerate(["S1", "S2", "069500"]):
        broker.set_bars(KR, symbol, make_random_bars(320, start_price=50_000, seed=i))
    monkeypatch.setattr(compare, "build_kis_broker", lambda settings, universe: broker)
    monkeypatch.setattr(
        compare,
        "load_universe",
        lambda path: {
            KR: [Instrument(market=KR, symbol="S1"), Instrument(market=KR, symbol="S2")],
            Market.US: [],
        },
    )
    split = make_random_bars(320).index[220].date().isoformat()
    assert compare.main(["--market", "KR", "--days", "320", "--split", split]) == 0
    out = capsys.readouterr().out
    assert out.count("◀ 고름") == 1
    assert "KODEX 200 보유" in out
