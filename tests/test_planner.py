from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from trading.core.models import Balance, Holding, Market, OrderType, Position, Side, Signal
from trading.risk.config import RiskConfig
from trading.risk.planner import ALREADY_ORDERED, DUPLICATE, plan_orders

D = Decimal
KR = Market.KR
TODAY = date(2026, 10, 26)
CONFIG = RiskConfig(strategy_weights={"a": D("0.5"), "b": D("0.5")})


def sig(symbol="005930", side=Side.BUY, strategy="a", price=70_000, market=KR) -> Signal:
    return Signal(
        market=market,
        symbol=symbol,
        side=side,
        strategy=strategy,
        price=D(str(price)),
        bar_date=date(2026, 10, 23),
        reason="test",
    )


def pos(symbol="005930", strategy="a", qty=10, avg=60_000) -> Position:
    return Position(
        market=KR,
        symbol=symbol,
        strategy=strategy,
        qty=qty,
        avg_price=D(avg),
        opened_at=datetime(2026, 10, 1, tzinfo=UTC),
    )


def plan(signals, *, positions=(), holdings=(), cash=10_000_000, equity=None, **kw):
    return plan_orders(
        market=kw.pop("market", KR),
        trade_date=TODAY,
        signals=signals,
        positions=positions,
        holdings=holdings,
        balance=Balance(
            market=kw.pop("market_b", KR), cash=D(cash), total_equity=D(equity or cash)
        ),
        prices=kw.pop("prices", {}),
        ordered_today=kw.pop("ordered_today", ()),
        bought_today=kw.pop("bought_today", D(0)),
        config=kw.pop("config", CONFIG),
        daily_buy_cap=kw.pop("daily_buy_cap", None),
    )


FEE_MARGIN = D("0.003")


def qty_for(amount, limit) -> int:
    return int(D(amount) / (D(limit) * (1 + FEE_MARGIN)))


def test_buy_sized_to_strategy_slot():
    # 총 1천만 → 전략 a 500만 → 1종목 목표 125만, 지정가 70,000×1.01 = 70,700
    result = plan([sig()])
    [planned] = result.orders
    o = planned.order
    assert o.order_type is OrderType.LIMIT
    assert o.limit_price == D(70_700)
    assert o.qty == qty_for(1_250_000, 70_700) == 17
    assert o.strategy == "a"
    assert o.client_order_id == "20261026-KR-005930-BUY-a"
    assert result.skipped == []


@pytest.mark.parametrize("side", [Side.BUY, Side.SELL])
def test_order_carries_signal_reason(side):
    # 주문 기록에 신호 근거를 남겨 웹앱에서 왜 샀는지/팔았는지 보여 준다
    s = sig(side=side).model_copy(update={"reason": "골든크로스: 20일선 71,000, 60일선 70,000"})
    result = plan(
        [s],
        positions=[pos()] if side is Side.SELL else [],
        holdings=[Holding(market=KR, symbol="005930", qty=10, avg_price=D(60_000))],
    )
    [planned] = result.orders
    assert planned.order.reason == s.reason


def test_symbol_limit_counts_other_strategy_holdings():
    # 전략 b가 이미 150만원어치 보유 → 종목 한도 200만 중 50만 남음
    result = plan(
        [sig(strategy="a")],
        positions=[pos(strategy="b", qty=20, avg=75_000)],
        holdings=[Holding(market=KR, symbol="005930", qty=20, avg_price=D(75_000))],
        prices={"005930": D(75_000)},
        cash=8_500_000,
        equity=10_000_000,
    )
    assert result.orders[0].order.qty == qty_for(500_000, 70_700) == 7


def test_cash_limits_buy():
    result = plan([sig()], cash=300_000, equity=10_000_000)
    assert result.orders[0].order.qty == qty_for(300_000, 70_700) == 4


def test_strategy_budget_limits_buy():
    # 전략 a가 이미 450만원어치 보유 → 남은 예산 50만
    positions = [pos(symbol=s, qty=15, avg=100_000) for s in ("A1", "A2", "A3")]
    result = plan([sig()], positions=positions, cash=5_500_000, equity=10_000_000)
    assert result.orders[0].order.qty == qty_for(500_000, 70_700)


def test_too_small_amount_is_skipped_with_reason():
    result = plan([sig()], cash=50_000, equity=10_000_000)
    assert result.orders == []
    [skipped] = result.skipped
    assert "금액 부족" in skipped.reason
    assert "주문 가능 현금" in skipped.reason


def test_daily_buy_cap():
    result = plan([sig()], daily_buy_cap=D(500_000), bought_today=D(400_000))
    assert result.orders[0].order.qty == qty_for(100_000, 70_700) == 1


def test_cash_is_reserved_across_buys_in_same_run():
    result = plan([sig("005930"), sig("000660", price=70_000)], cash=1_500_000, equity=10_000_000)
    first, second = (p.order for p in result.orders)
    assert first.qty == 17
    left = D(1_500_000) - 17 * D(70_700) * D("1.003")
    assert second.qty == qty_for(left, 70_700)


def test_one_order_per_symbol_first_strategy_wins():
    result = plan([sig(strategy="a"), sig(strategy="b")])
    assert [p.order.strategy for p in result.orders] == ["a"]
    [skipped] = result.skipped
    assert (skipped.signal.strategy, skipped.reason) == ("b", DUPLICATE)


def test_sell_beats_buy_on_same_symbol():
    result = plan(
        [sig(strategy="a", side=Side.BUY), sig(strategy="b", side=Side.SELL)],
        positions=[pos(strategy="b")],
        holdings=[Holding(market=KR, symbol="005930", qty=10, avg_price=D(60_000))],
    )
    [planned] = result.orders
    assert (planned.order.side, planned.order.strategy) == (Side.SELL, "b")
    assert result.skipped[0].signal.side is Side.BUY


def test_symbol_already_ordered_today_is_skipped():
    result = plan([sig()], ordered_today={"005930"})
    assert result.orders == []
    assert result.skipped[0].reason == ALREADY_ORDERED


def test_sell_whole_position_at_limit_below_close():
    result = plan(
        [sig(side=Side.SELL, price=70_000)],
        positions=[pos(qty=10)],
        holdings=[Holding(market=KR, symbol="005930", qty=10, avg_price=D(60_000))],
    )
    o = result.orders[0].order
    assert (o.side, o.qty) == (Side.SELL, 10)
    assert o.limit_price == D(67_900)  # 70,000 × 0.97


def test_sell_refused_when_account_holds_less():
    result = plan(
        [sig(side=Side.SELL)],
        positions=[pos(qty=10)],
        holdings=[Holding(market=KR, symbol="005930", qty=6, avg_price=D(60_000))],
    )
    assert result.orders == []
    assert "6주" in result.skipped[0].reason
    assert result.warnings


def test_sell_without_position_is_skipped():
    result = plan([sig(side=Side.SELL)])
    assert result.orders == []
    assert "보유 기록" in result.skipped[0].reason


def test_buy_when_strategy_already_holds_is_skipped():
    result = plan(
        [sig()],
        positions=[pos()],
        holdings=[Holding(market=KR, symbol="005930", qty=10, avg_price=D(60_000))],
    )
    assert result.orders == []
    assert "이미 보유" in result.skipped[0].reason


def test_unknown_strategy_is_skipped():
    result = plan([sig(strategy="zzz")])
    assert result.orders == []
    assert "자금 배분" in result.skipped[0].reason


def test_us_limit_price_in_cents():
    result = plan(
        [sig(symbol="AAPL", price="180.55", market=Market.US)],
        market=Market.US,
        market_b=Market.US,
        cash=10_000,
    )
    o = result.orders[0].order
    assert o.limit_price == D("182.35")  # 180.55 × 1.01 = 182.3555 → 내림
    assert o.qty == qty_for(1_250, "182.35") == 6


def test_risk_config_validation():
    with pytest.raises(ValidationError, match="합"):
        RiskConfig(strategy_weights={"a": D("0.7"), "b": D("0.5")})
    with pytest.raises(ValidationError):
        RiskConfig(strategy_weights={"a": D("0")})
    assert sum(RiskConfig().strategy_weights.values()) == 1
