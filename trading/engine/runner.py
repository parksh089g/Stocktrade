"""하루 실행 흐름.

스위치 확인 → 체결 동기화 → (휴장/장 마감이면 여기서 끝) → 계좌 조회 → 시세 → 신호
→ 리스크 → 주문(주문마다 스위치 재확인) → 기록 → 알림

일시정지 중에는 손절 매도만 주문하고 나머지 신호는 '주문 안 함'으로 알린다.

손절 확인 실행(stop_loss_only)은 장 마감 전에 한 번 더 돌아 보유 종목의 손절만 본다.
확정된 일봉 뒤에 지금 가격으로 만든 오늘 봉을 붙여 전략에 넘기고, 손절 매도 신호만 쓴다.
매수와 일반 매도는 확정된 종가로만 판단하므로 내지 않는다. 알릴 일이 없으면 알림을 보내지 않는다.

실행 기록에는 웹앱(비중, 평가손익, 수익 곡선)을 위해 잔고, 이번에 쓴 가격, 비교 지수 종가도 남긴다.
비교 지수는 참고용이라 받지 못해도 경고 없이 로그만 남긴다(경고가 있으면 알림이 '긴급'이 된다).

하루 한 번(once_per_day)은 예약 실행을 여러 곳(Cloudflare 예약, GitHub 예약)에서 여러 번
부르기 위한 것이다. 오늘 같은 종류의 실행이 완료(또는 일시정지)로 끝났거나 휴장일, 장 마감 후라
더 할 일이 없으면 다음 실행은 기록과 알림 없이 끝낸다. 오류로 끝났으면 다시 돈다.

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
    Instrument,
    Market,
    Order,
    OrderStatus,
    Position,
    Side,
    Signal,
    SkippedSignal,
)
from trading.engine.report import (
    BenchmarkClose,
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
from trading.strategies.base import NO_CONTEXT, MarketContext, Strategy
from trading.universe import Universe

logger = logging.getLogger(__name__)

# 마지막 일봉 뒤로 빠진 거래일이 이보다 많으면 시세가 멈춘 것(거래정지 등)으로 보고 건너뛴다.
# 달력 날짜가 아니라 거래일로 세야 긴 연휴(예: 2025년 추석 7일 휴장) 다음 날에도 정상 동작한다.
MAX_MISSING_TRADING_DAYS = 2
PAUSED_SKIP = "일시정지 중이라 주문하지 않았습니다(손절 매도만 나갑니다)"
# 비교 지수는 마지막 확정 종가 하나만 쓴다. 오늘 봉이 섞여 와도 남도록 몇 개 받는다.
BENCHMARK_BARS = 5


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
        stop_loss_only: bool = False,
        once_per_day: bool = False,
        index_symbols: Mapping[Market, str] | None = None,
        benchmarks: Mapping[Market, Instrument] | None = None,
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
        self.stop_loss_only = stop_loss_only
        self.once_per_day = once_per_day
        # 시장 지수(ETF) 종목. 주면 그 일봉을 MarketContext로 전략에 함께 넘긴다.
        self.index_symbols = dict(index_symbols or {})
        # 계좌 성적을 견줄 지수 ETF. 매매 실행마다 종가를 기록만 하고 전략에는 넘기지 않는다.
        self.benchmarks = dict(benchmarks or {})
        self.clock = clock

    def run(self, market: Market) -> RunReport:
        report = RunReport(
            market=market,
            mode=self.mode,
            started_at=self.clock(),
            dry_run=self.dry_run,
            stop_loss_only=self.stop_loss_only,
        )
        today = trading_date(market, report.started_at)
        if self._done_today(market, today):
            logger.info("오늘(%s) 이미 실행해서 건너뜁니다", today)
            report.status = RunStatus.SKIPPED
            report.message = f"오늘({today}) 이미 실행했습니다"
            return report
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
        if report.status in (RunStatus.OK, RunStatus.PAUSED) or report.day_over:
            self._mark_done(market, today)

        if self.stop_loss_only and not self.dry_run and _nothing_to_tell(report):
            logger.info("손절 확인: 알릴 일이 없어 알림을 보내지 않습니다")
            return report
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
                report.day_over = True
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
                report.day_over = True
                return

        balance = self.broker.get_balance(market)
        report.balance = balance
        holdings = self.broker.get_holdings(market)
        positions = self.store.get_positions(market)
        report.warnings += _reconcile(positions, holdings)

        if self.stop_loss_only:
            signals, prices = self._collect_stop_losses(market, today, positions, report)
        else:
            signals, prices = self._collect_signals(market, today, positions, report)
            report.benchmark = self._benchmark_close(market, today)
        report.signals = signals
        report.prices = prices
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
        context = self._market_context(market, today, count, report)

        signals: list[Signal] = []
        prices: dict[str, Decimal] = {}
        for symbol in universe + held_only:
            strategies = [
                s for s in self.strategies if symbol in in_universe or (symbol, s.id) in pos_by_key
            ]
            try:
                bars = self._confirmed_bars(market, symbol, today, count, report)
                if bars is None:
                    continue
                found = [
                    sig
                    for s in strategies
                    if (
                        sig := s.evaluate(
                            market, symbol, bars, pos_by_key.get((symbol, s.id)), context
                        )
                    )
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

    def _collect_stop_losses(
        self, market: Market, today: date, positions: list[Position], report: RunReport
    ) -> tuple[list[Signal], dict[str, Decimal]]:
        """손절 확인 실행: 보유 종목만 지금 가격을 오늘 봉으로 붙여 손절 신호만 본다."""
        by_id = {s.id: s for s in self.strategies}
        held: dict[str, list[Position]] = {}
        for p in positions:
            if p.strategy in by_id:
                held.setdefault(p.symbol, []).append(p)
        count = max(s.lookback for s in self.strategies) + 1

        signals: list[Signal] = []
        prices: dict[str, Decimal] = {}
        for symbol, symbol_positions in held.items():
            try:
                bars = self._confirmed_bars(market, symbol, today, count, report)
                if bars is None:
                    continue
                price = self.broker.get_price(market, symbol)
                bars = _with_today(bars, today, price)
                found = [
                    sig
                    for p in symbol_positions
                    if (sig := by_id[p.strategy].evaluate(market, symbol, bars, p, NO_CONTEXT))
                    and sig.stop_loss
                ]
            except Exception as e:
                logger.exception("%s 손절 확인 실패", symbol)
                report.warnings.append(
                    self._clean(
                        f"{symbol}: 시세 오류로 손절 확인을 건너뜁니다 ({type(e).__name__}: {e})"
                    )
                )
                continue
            prices[symbol] = price
            signals += found
        return signals, prices

    def _confirmed_bars(
        self, market: Market, symbol: str, today: date, count: int, report: RunReport
    ) -> pd.DataFrame | None:
        """오늘(미완성) 봉을 뺀 일봉. 없거나 시세가 멈췄으면 경고를 남기고 None."""
        bars = self.broker.get_daily_bars(market, symbol, count)
        bars = bars[bars.index < pd.Timestamp(today)]
        if bars.empty:
            report.warnings.append(f"{symbol}: 일봉이 없어 건너뜁니다")
            return None
        last_day = bars.index[-1].date()
        missing = self._missing_trading_days(market, last_day, today)
        if missing > MAX_MISSING_TRADING_DAYS:
            report.warnings.append(
                f"{symbol}: 마지막 일봉({last_day}) 뒤로 {missing}거래일치가 없어 "
                "건너뜁니다(거래정지 확인)"
            )
            return None
        return bars

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

    def _done_key(self, market: Market) -> str:
        kind = "stoploss" if self.stop_loss_only else "trade"
        return f"run_done_{self.mode}_{market}_{kind}"

    def _done_today(self, market: Market, today: date) -> bool:
        if not self.once_per_day or self.dry_run:
            return False
        try:
            return self.store.get_cache(self._done_key(market)) == today.isoformat()
        except Exception:
            # 모르면 실행한다. 같은 날 같은 종목 중복 주문은 따로 막는다.
            logger.warning("오늘 실행 여부를 읽지 못해 그대로 실행합니다", exc_info=True)
            return False

    def _mark_done(self, market: Market, today: date) -> None:
        if not self.once_per_day or self.dry_run:
            return
        try:
            self.store.put_cache(self._done_key(market), today.isoformat())
        except Exception:
            # 백업 예약 실행이 한 번 더 돌 뿐이다(중복 주문은 따로 막는다)
            logger.warning("오늘 실행 완료 표시를 저장하지 못했습니다", exc_info=True)

    def _market_context(
        self, market: Market, today: date, count: int, report: RunReport
    ) -> MarketContext:
        symbol = self.index_symbols.get(market)
        if symbol is None:
            return NO_CONTEXT
        try:
            bars = self.broker.get_daily_bars(market, symbol, count)
        except Exception as e:
            logger.exception("시장 지수 일봉 실패")
            report.warnings.append(
                self._clean(
                    f"시장 지수({symbol}) 일봉 오류로 시장 필터를 쓰는 전략은 새로 사지 않습니다 "
                    f"({type(e).__name__}: {e})"
                )
            )
            return MarketContext(index_bars=None)
        return MarketContext(index_bars=bars[bars.index < pd.Timestamp(today)])

    def _benchmark_close(self, market: Market, today: date) -> BenchmarkClose | None:
        """비교 지수의 마지막 확정 종가. 못 받으면 로그만 남기고 None(웹앱에서는 빈 날)."""
        inst = self.benchmarks.get(market)
        if inst is None:
            return None
        try:
            bars = self.broker.get_daily_bars(market, inst.symbol, BENCHMARK_BARS)
        except Exception:
            logger.warning("비교 지수(%s) 일봉을 받지 못했습니다", inst.symbol, exc_info=True)
            return None
        bars = bars[bars.index < pd.Timestamp(today)]
        if bars.empty:
            logger.warning("비교 지수(%s)의 확정된 일봉이 없습니다", inst.symbol)
            return None
        return BenchmarkClose(
            symbol=inst.symbol,
            name=inst.name,
            close=Decimal(str(bars["close"].iloc[-1])),
            bar_date=bars.index[-1].date(),
        )

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


def _with_today(bars: pd.DataFrame, today: date, price: Decimal) -> pd.DataFrame:
    """지금 가격으로 만든 오늘 봉(시가=고가=저가=종가)을 붙인다."""
    value = float(price)
    row = {c: [0 if c == "volume" else value] for c in bars.columns}
    return pd.concat([bars, pd.DataFrame(row, index=pd.DatetimeIndex([pd.Timestamp(today)]))])


def _nothing_to_tell(report: RunReport) -> bool:
    """주문, 체결, 경고, 오류가 하나도 없는지. 손절 확인 실행은 이럴 때 알림을 보내지 않는다."""
    if report.status is RunStatus.ERROR:
        return False
    return not (
        report.orders or report.planned or report.fills or report.skipped or report.warnings
    )


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
