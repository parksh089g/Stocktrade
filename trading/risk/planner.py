"""신호를 실제 주문으로 바꾼다. 수량, 가격, 한도를 여기서 정한다.

규칙
- 같은 날 같은 종목은 한 번만 주문한다(전략이 달라도). 매도 신호가 매수 신호보다 먼저다.
  오늘 이미 주문(거부 제외)이 있는 종목도 주문하지 않는다.
- 매수 금액은 아래 중 가장 작은 값:
  전략 1종목 목표(전략 배분 / slots), 전략 남은 예산, 종목당 한도(전략 합산),
  주문 가능 현금, 하루 매수 한도
- 매도는 그 전략의 보유분 전부. 계좌 보유 수량이 기록보다 적으면 팔지 않고 경고한다.
- 모든 주문은 지정가: 기준가(전날 종가) × (1 ± 버퍼)를 호가 단위로 내림.
- 매도 대금은 체결 전이라 같은 실행의 매수 자금으로 쓰지 않는다.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from trading.core.models import (
    Balance,
    Holding,
    Market,
    Order,
    OrderType,
    Position,
    Side,
    Signal,
    SkippedSignal,
)
from trading.core.ticks import floor_to_tick
from trading.risk.config import RiskConfig

DUPLICATE = "같은 날 같은 종목은 한 번만 주문합니다"
ALREADY_ORDERED = "오늘 이미 이 종목 주문이 있습니다"


@dataclass(frozen=True)
class PlannedOrder:
    order: Order
    signal: Signal


@dataclass
class OrderPlan:
    orders: list[PlannedOrder] = field(default_factory=list)
    skipped: list[SkippedSignal] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def skip(self, signal: Signal, reason: str) -> None:
        self.skipped.append(SkippedSignal(signal=signal, reason=reason))


def plan_orders(
    *,
    market: Market,
    trade_date: date,
    signals: Iterable[Signal],
    positions: Iterable[Position],
    holdings: Iterable[Holding],
    balance: Balance,
    prices: Mapping[str, Decimal],
    ordered_today: Iterable[str],
    bought_today: Decimal,
    config: RiskConfig,
    daily_buy_cap: Decimal | None = None,
) -> OrderPlan:
    plan = OrderPlan()
    fmt = market.format_price
    positions = [p for p in positions if p.market == market]
    pos_by_key = {(p.symbol, p.strategy): p for p in positions}
    held_qty = {h.symbol: h.qty for h in holdings if h.market == market}

    def price_of(symbol: str, fallback: Decimal) -> Decimal:
        return prices.get(symbol, fallback)

    # 평가금액: 종목별은 계좌 기준(수동으로 산 것 포함), 전략별은 기록 기준
    symbol_value: dict[str, Decimal] = {
        h.symbol: h.qty * price_of(h.symbol, h.avg_price) for h in holdings if h.market == market
    }
    strategy_value: dict[str, Decimal] = {}
    for p in positions:
        value = p.qty * price_of(p.symbol, p.avg_price)
        strategy_value[p.strategy] = strategy_value.get(p.strategy, Decimal(0)) + value

    equity = balance.total_equity
    cash_left = balance.cash
    cap_left = daily_buy_cap - bought_today if daily_buy_cap is not None else None

    # 1) 종목당 신호 하나만 남긴다
    chosen: dict[str, Signal] = {}
    for sig in signals:
        current = chosen.get(sig.symbol)
        if current is None:
            chosen[sig.symbol] = sig
        elif sig.side is Side.SELL and current.side is Side.BUY:
            plan.skip(current, DUPLICATE)
            chosen[sig.symbol] = sig
        else:
            plan.skip(sig, DUPLICATE)

    blocked = set(ordered_today)
    # 2) 매도 먼저, 그다음 매수 (각각 원래 순서 유지)
    for sig in sorted(chosen.values(), key=lambda s: s.side is Side.BUY):
        if sig.symbol in blocked:
            plan.skip(sig, ALREADY_ORDERED)
            continue

        if sig.side is Side.SELL:
            pos = pos_by_key.get((sig.symbol, sig.strategy))
            if pos is None:
                plan.skip(sig, "이 전략의 보유 기록이 없습니다")
                continue
            held = held_qty.get(sig.symbol, 0)
            if held < pos.qty:
                plan.skip(sig, f"계좌 보유 {held}주가 전략 기록 {pos.qty}주보다 적습니다")
                plan.warnings.append(
                    f"{sig.symbol}: 계좌 {held}주 < {sig.strategy} 기록 {pos.qty}주. "
                    "수량이 맞지 않아 매도하지 않았습니다"
                )
                continue
            limit = floor_to_tick(market, sig.price * (1 - config.sell_limit_buffer))
            plan.orders.append(PlannedOrder(_order(sig, pos.qty, limit, trade_date), sig))
            continue

        # 매수
        if (sig.symbol, sig.strategy) in pos_by_key:
            plan.skip(sig, "이 전략이 이미 보유 중입니다")
            continue
        weight = config.strategy_weights.get(sig.strategy)
        if weight is None:
            plan.skip(sig, "자금 배분이 정해지지 않은 전략입니다")
            continue
        limit = floor_to_tick(market, sig.price * (1 + config.buy_limit_buffer))
        if limit <= 0:
            plan.skip(sig, "주문 가격을 정할 수 없습니다")
            continue

        budget = equity * weight
        limits = {
            "전략 1종목 목표 금액": budget / config.slots_per_strategy,
            "전략 남은 예산": budget - strategy_value.get(sig.strategy, Decimal(0)),
            "종목당 한도": equity * config.max_symbol_weight - symbol_value.get(sig.symbol, 0),
            "주문 가능 현금": cash_left,
        }
        if cap_left is not None:
            limits["하루 매수 한도"] = cap_left
        bound, amount = min(limits.items(), key=lambda kv: kv[1])
        qty = int(amount / (limit * (1 + config.fee_margin))) if amount > 0 else 0
        if qty < 1:
            plan.skip(sig, f"금액 부족({bound} {fmt(max(amount, Decimal(0)))})")
            continue

        spend = qty * limit
        cash_left -= spend * (1 + config.fee_margin)
        if cap_left is not None:
            cap_left -= spend
        strategy_value[sig.strategy] = strategy_value.get(sig.strategy, Decimal(0)) + spend
        symbol_value[sig.symbol] = symbol_value.get(sig.symbol, Decimal(0)) + spend
        plan.orders.append(PlannedOrder(_order(sig, qty, limit, trade_date), sig))

    return plan


def _order(sig: Signal, qty: int, limit: Decimal, trade_date: date) -> Order:
    return Order(
        market=sig.market,
        symbol=sig.symbol,
        side=sig.side,
        qty=qty,
        order_type=OrderType.LIMIT,
        limit_price=limit,
        strategy=sig.strategy,
        client_order_id=f"{trade_date:%Y%m%d}-{sig.market}-{sig.symbol}-{sig.side}-{sig.strategy}",
    )
