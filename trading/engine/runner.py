"""하루 실행 흐름.

스위치 확인 → 체결 동기화 → (휴장/장 마감이면 여기서 끝) → 계좌 조회 → 시세 → 신호
→ 리스크 → 주문(주문마다 스위치 재확인) → 기록 → 알림

일시정지 중에는 손절 매도만 주문하고 나머지 신호는 '주문 안 함'으로 알린다.

오류 처리
- 스위치, 계좌, 주문 내역 조회가 실패하면 주문하지 않고 오류 알림만 보낸다.
- 한 종목의 시세나 신호 계산이 실패하면 그 종목만 건너뛰고 경고로 알린다.
- 주문 중 오류가 나면 남은 주문을 내지 않는다. 접수 여부가 불확실하니 증권사 앱 확인을 안내한다.
- 주문은 나갔는데 기록 저장이 실패해도 남은 주문을 내지 않고 알린다.
- 실행 기록 저장이나 알림이 실패해도 실행 결과는 돌려준다. 알림 실패는 notify_failed로 표시한다.
"""

import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal

import pandas as pd

from trading.brokers.base import BrokerAdapter
from trading.core.clock import now_utc, trading_date
from trading.core.log import redact
from trading.core.models import (
    Holding,
    Market,
    Order,
    OrderStatus,
    Position,
    Side,
    Signal,
    SkippedSignal,
)
from trading.engine.report import (
    RunReport,
    RunStatus,
    format_report,
    report_body,
    report_title,
)
from trading.engine.sync import record_new_order, sync_open_orders
from trading.notify.base import Notification, Notifier
from trading.risk.config import RiskConfig
from trading.risk.planner import PlannedOrder, plan_orders
from trading.storage.base import Store
from trading.strategies.base import Strategy
from trading.universe import Universe

logger = logging.getLogger(__name__)

# 마지막 일봉 뒤로 빠진 거래일이 이보다 많으면 시세가 멈춘 것(거래정지 등)으로 보고 건너뛴다.
# 달력 날짜가 아니라 거래일로 세야 긴 연휴(예: 2025년 추석 7일 휴장) 다음 날에도 정상 동작한다.
MAX_MISSING_TRADING_DAYS = 2
PAUSED_SKIP = "일시정지 중이라 주문하지 않았습니다(손절 매도만 나갑니다)"


class OrderPlacementError(Exception):
    def __init__(self, order: Order, cause: Exception) -> None:
        super().__init__(
            f"{order.symbol} 주문 중 오류({type(cause).__name__}: {cause}). "
            "남은 주문은 내지 않았습니다. 이 주문이 접수됐는지 증권사 앱에서 확인하세요"
        )


class OrderRecordError(Exception):
    def __init__(self, order: Order, cause: Exception) -> None:
        super().__init__(
            f"{order.symbol} 주문은 증권사에 냈지만 기록 저장에 실패했습니다"
            f"({type(cause).__name__}: {cause}). 남은 주문은 내지 않았습니다. "
            "증권사 앱에서 주문을 확인하세요"
        )


class Engine:
    def __init__(
        self,
        *,
        broker: BrokerAdapter,
        store: Store,
        notifier: Notifier,
        strategies: Sequence[Strategy],
        universe: Universe,
        risk: RiskConfig | None = None,
        mode: str = "paper",
        daily_buy_caps: Mapping[Market, Decimal | None] | None = None,
        secrets: Iterable[str] = (),
        check_market_hours: bool = True,
        dry_run: bool = False,
        clock: Callable[[], datetime] = now_utc,
    ) -> None:
        if not strategies:
            raise ValueError("전략이 하나 이상 필요합니다")
        self.broker = broker
        self.store = store
        self.notifier = notifier
        self.strategies = list(strategies)
        self.universe = universe
        self.risk = risk or RiskConfig()
        self.mode = mode
        self.daily_buy_caps = dict(daily_buy_caps or {})
        self.secrets = list(secrets)
        self.check_market_hours = check_market_hours
        self.dry_run = dry_run
        self.clock = clock

    def run(self, market: Market) -> RunReport:
        report = RunReport(
            market=market, mode=self.mode, started_at=self.clock(), dry_run=self.dry_run
        )
        try:
            self._run(market, report)
        except (OrderPlacementError, OrderRecordError) as e:
            logger.exception("주문 단계 오류")
            report.status = RunStatus.ERROR
            report.error = self._clean(str(e))
        except Exception as e:
            logger.exception("실행 중 오류")
            report.status = RunStatus.ERROR
            report.error = self._clean(f"{type(e).__name__}: {e}. 주문하지 않았습니다")
        report.finished_at = self.clock()

        names = self._names(market)
        run_id = None
        try:
            run_id = self.store.save_run(report, self._clean(format_report(report, names)))
        except Exception as e:
            logger.exception("실행 기록 저장 실패")
            report.warnings.append(self._clean(f"실행 기록 저장 실패: {type(e).__name__}: {e}"))

        notification = Notification(
            title=report_title(report),
            body=self._clean(report_body(report, names)),
            url=f"/runs/{run_id}" if run_id else "/",
            urgent=report.status is RunStatus.ERROR or bool(report.warnings),
        )
        try:
            self.notifier.send(notification)
        except Exception:
            logger.exception("알림 전송 실패")
            report.notify_failed = True
        return report

    # ---- 단계 ----

    def _run(self, market: Market, report: RunReport) -> None:
        now = self.clock()
        today = trading_date(market, now)

        pause = self.store.get_pause()

        synced = sync_open_orders(market, self.broker, self.store, today)
        report.fills = synced.fills
        report.warnings += synced.warnings

        if pause.paused:
            _mark_paused(report, pause.reason)
        if self.check_market_hours:
            if not self.broker.is_trading_day(market, today):
                report.status = RunStatus.SKIPPED
                report.message = f"휴장일입니다({today})"
                return
            local_time = now.astimezone(market.timezone).time()
            if local_time < market.order_start_time:
                report.status = RunStatus.SKIPPED
                report.message = (
                    f"주문 가능 시간 전입니다(현지 {market.order_start_time:%H:%M}부터)"
                )
                return
            if local_time >= market.close_time:
                report.status = RunStatus.SKIPPED
                report.message = "장 마감 후라 주문하지 않습니다"
                return

        balance = self.broker.get_balance(market)
        holdings = self.broker.get_holdings(market)
        positions = self.store.get_positions(market)
        report.warnings += _reconcile(positions, holdings)

        signals, prices = self._collect_signals(market, today, positions, report)
        report.signals = signals
        if pause.paused:
            report.skipped += [
                SkippedSignal(signal=s, reason=PAUSED_SKIP) for s in signals if not s.stop_loss
            ]
            signals = [s for s in signals if s.stop_loss]

        ordered_today, bought_today = self._orders_today(market, today)
        plan = plan_orders(
            market=market,
            trade_date=today,
            signals=signals,
            positions=positions,
            holdings=holdings,
            balance=balance,
            prices=prices,
            ordered_today=ordered_today,
            bought_today=bought_today,
            config=self.risk,
            daily_buy_cap=self.daily_buy_caps.get(market),
        )
        report.skipped += plan.skipped
        report.warnings += plan.warnings
        self._place_orders(plan.orders, report)

    def _collect_signals(
        self, market: Market, today: date, positions: list[Position], report: RunReport
    ) -> tuple[list[Signal], dict[str, Decimal]]:
        universe = [i.symbol for i in self.universe.get(market, [])]
        in_universe = set(universe)
        pos_by_key = {(p.symbol, p.strategy): p for p in positions}
        # 목록에서 빠진 종목도 보유 중이면 팔 수 있게 계속 본다
        held_only = list(dict.fromkeys(p.symbol for p in positions if p.symbol not in in_universe))
        # 오늘 봉이 섞여 들어와도 빼고 lookback개가 남도록 하나 더 받는다
        count = max(s.lookback for s in self.strategies) + 1

        signals: list[Signal] = []
        prices: dict[str, Decimal] = {}
        for symbol in universe + held_only:
            strategies = [
                s for s in self.strategies if symbol in in_universe or (symbol, s.id) in pos_by_key
            ]
            try:
                bars = self.broker.get_daily_bars(market, symbol, count)
                bars = bars[bars.index < pd.Timestamp(today)]  # 오늘(미완성) 봉 제외
                if bars.empty:
                    report.warnings.append(f"{symbol}: 일봉이 없어 건너뜁니다")
                    continue
                last_day = bars.index[-1].date()
                missing = self._missing_trading_days(market, last_day, today)
                if missing > MAX_MISSING_TRADING_DAYS:
                    report.warnings.append(
                        f"{symbol}: 마지막 일봉({last_day}) 뒤로 {missing}거래일치가 없어 "
                        "건너뜁니다(거래정지 확인)"
                    )
                    continue
                found = [
                    sig
                    for s in strategies
                    if (sig := s.evaluate(market, symbol, bars, pos_by_key.get((symbol, s.id))))
                ]
            except Exception as e:
                logger.exception("%s 신호 계산 실패", symbol)
                report.warnings.append(
                    self._clean(f"{symbol}: 시세/신호 오류로 건너뜁니다 ({type(e).__name__}: {e})")
                )
                continue
            prices[symbol] = Decimal(str(bars["close"].iloc[-1]))
            signals += found
        return signals, prices

    def _orders_today(self, market: Market, today: date) -> tuple[set[str], Decimal]:
        """오늘 이미 주문이 있는 종목과 오늘 매수 주문 금액. 저장소와 증권사를 모두 본다."""
        records = self.store.get_orders(market, today) + self.broker.get_orders(market, today)
        active = {}
        for r in records:
            if r.status is not OrderStatus.REJECTED:
                active[r.broker_order_id or r.order.client_order_id] = r
        symbols = {r.order.symbol for r in active.values()}
        bought = sum(
            (
                r.order.qty * (r.order.limit_price or r.avg_fill_price or Decimal(0))
                for r in active.values()
                if r.order.side is Side.BUY
            ),
            Decimal(0),
        )
        return symbols, bought

    def _place_orders(self, planned: list[PlannedOrder], report: RunReport) -> None:
        if self.dry_run:
            report.planned = [p.order for p in planned]
            return
        for p in planned:
            pause = self.store.get_pause()
            if pause.paused and not p.signal.stop_loss:
                if report.status is not RunStatus.PAUSED:
                    _mark_paused(report, pause.reason)
                    report.warnings.append("주문 도중 일시정지가 켜졌습니다")
                report.skipped.append(SkippedSignal(signal=p.signal, reason=PAUSED_SKIP))
                continue
            try:
                result = self.broker.place_order(p.order)
            except Exception as e:
                raise OrderPlacementError(p.order, e) from e
            report.orders.append(result)
            try:
                recorded = record_new_order(p.order.market, self.store, result)
            except Exception as e:
                raise OrderRecordError(p.order, e) from e
            report.fills += recorded.fills
            report.warnings += recorded.warnings

    # ---- 도움 함수 ----

    def _missing_trading_days(self, market: Market, last_day: date, today: date) -> int:
        """last_day와 today 사이(둘 다 빼고)의 개장일 수."""
        count, day = 0, last_day + timedelta(days=1)
        while day < today and count <= MAX_MISSING_TRADING_DAYS:
            count += self.broker.is_trading_day(market, day)
            day += timedelta(days=1)
        return count

    def _names(self, market: Market) -> dict[str, str]:
        return {i.symbol: i.name for i in self.universe.get(market, []) if i.name}

    def _clean(self, text: str) -> str:
        return redact(text, self.secrets)


def _mark_paused(report: RunReport, reason: str) -> None:
    report.status = RunStatus.PAUSED
    report.message = (reason or "일시정지 스위치가 켜져 있습니다") + " (손절 매도만 냅니다)"


def _reconcile(positions: list[Position], holdings: list[Holding]) -> list[str]:
    """전략 기록 합계와 실제 계좌 수량이 다른 종목을 알려준다."""
    recorded: dict[str, int] = {}
    for p in positions:
        recorded[p.symbol] = recorded.get(p.symbol, 0) + p.qty
    actual = {h.symbol: h.qty for h in holdings}
    warnings = []
    for symbol in sorted(set(recorded) | set(actual)):
        a, b = recorded.get(symbol, 0), actual.get(symbol, 0)
        if a != b:
            warnings.append(f"{symbol}: 전략 기록 {a}주, 계좌 {b}주로 다릅니다")
    return warnings
