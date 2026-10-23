from datetime import UTC, date, datetime
from decimal import Decimal

from trading.core.models import (
    Market,
    Order,
    OrderResult,
    OrderStatus,
    OrderType,
    Side,
    Signal,
    SkippedSignal,
)
from trading.engine.report import FillUpdate, RunReport, RunStatus, format_report

D = Decimal
STARTED = datetime(2026, 10, 25, 23, 40, tzinfo=UTC)  # 한국 10/26 08:40


def order(symbol="005930", side=Side.BUY, qty=17, limit=70_700, strategy="trend_ma") -> Order:
    return Order(
        market=Market.KR,
        symbol=symbol,
        side=side,
        qty=qty,
        order_type=OrderType.LIMIT,
        limit_price=D(limit),
        strategy=strategy,
        client_order_id="x",
    )


def signal(symbol="005930", side=Side.BUY, strategy="trend_ma", reason="골든크로스") -> Signal:
    return Signal(
        market=Market.KR,
        symbol=symbol,
        side=side,
        strategy=strategy,
        price=D(70_000),
        bar_date=date(2026, 10, 23),
        reason=reason,
    )


def test_full_report_text():
    report = RunReport(
        market=Market.KR,
        mode="live",
        started_at=STARTED,
        fills=[
            FillUpdate(order=order(symbol="000660", side=Side.SELL, qty=3), qty=3, price=D(201_000))
        ],
        signals=[signal(), signal("035420", strategy="rsi_rebound", reason="RSI 과매도 탈출")],
        orders=[
            OrderResult(order=order(), broker_order_id="1", status=OrderStatus.SUBMITTED),
        ],
        skipped=[
            SkippedSignal(signal=signal("035420", strategy="rsi_rebound"), reason="금액 부족")
        ],
        warnings=["005930: 전략 기록 0주, 계좌 10주로 다릅니다"],
    )
    text = format_report(report, {"005930": "삼성전자", "000660": "SK하이닉스"})

    assert text.splitlines()[0] == "[실전] 국내 · 완료 · 2026-10-26 08:40 KST"
    assert " · 매도 SK하이닉스(000660) 3주 @ 201,000 [trend_ma]" in text
    assert " · 매수 삼성전자(005930) 17주 지정가 70,700 [trend_ma] → 접수" in text
    assert "   (골든크로스)" in text
    assert " · 매수 035420 [rsi_rebound]: 금액 부족" in text
    assert "확인 필요" in text
    assert "신호가 없습니다" not in text


def test_error_report_text():
    report = RunReport(
        market=Market.US,
        mode="paper",
        started_at=STARTED,
        status=RunStatus.ERROR,
        error="BrokerError: timeout. 주문하지 않았습니다",
    )
    text = format_report(report)
    assert text.splitlines()[0].startswith("[모의투자] 미국 · 오류 · ")
    assert "오류: BrokerError: timeout" in text


def test_rejected_order_shows_reason():
    report = RunReport(
        market=Market.KR,
        mode="paper",
        started_at=STARTED,
        orders=[
            OrderResult(
                order=order(),
                broker_order_id=None,
                status=OrderStatus.REJECTED,
                message="주문 가능 금액이 부족합니다",
            )
        ],
    )
    assert "→ 거부: 주문 가능 금액이 부족합니다" in format_report(report)


def test_title_and_body_split():
    from trading.engine.report import report_body, report_title

    report = RunReport(
        market=Market.KR,
        mode="paper",
        started_at=STARTED,
        status=RunStatus.PAUSED,
        message="점검 중 (손절 매도만 냅니다)",
    )
    assert report_title(report) == "[모의투자] 국내 · 일시정지"
    assert report_body(report) == "점검 중 (손절 매도만 냅니다)"
