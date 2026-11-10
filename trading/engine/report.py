"""하루 실행 결과. 저장소에 기록하고 알림으로 보낸다."""

from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field

from trading.core.clock import format_kst
from trading.core.models import (
    Balance,
    Market,
    Order,
    OrderResult,
    OrderStatus,
    Side,
    Signal,
    SkippedSignal,
    UtcDatetime,
)


class RunStatus(StrEnum):
    OK = "OK"
    PAUSED = "PAUSED"  # 일시정지 스위치
    SKIPPED = "SKIPPED"  # 휴장일, 장 마감 후
    ERROR = "ERROR"


class FillUpdate(BaseModel):
    """이번 실행에서 새로 확인한 체결."""

    order: Order
    qty: int
    price: Decimal


class BenchmarkClose(BaseModel):
    """계좌 성적을 견줄 지수 ETF의 마지막 확정 종가."""

    symbol: str
    name: str = ""
    close: Decimal
    bar_date: date  # 그 시장 현지 날짜


class RunReport(BaseModel):
    market: Market
    mode: str  # paper | live
    started_at: UtcDatetime
    finished_at: UtcDatetime | None = None
    status: RunStatus = RunStatus.OK
    message: str = ""
    error: str | None = None
    fills: list[FillUpdate] = Field(default_factory=list)
    signals: list[Signal] = Field(default_factory=list)
    orders: list[OrderResult] = Field(default_factory=list)
    skipped: list[SkippedSignal] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    dry_run: bool = False  # 시험 실행: 계산만 하고 주문은 내지 않았다
    planned: list[Order] = Field(default_factory=list)  # 시험 실행에서 냈을 주문
    stop_loss_only: bool = False  # 장 마감 전 손절 확인 실행: 보유 종목의 손절 매도만 봤다
    day_over: bool = False  # 휴장일이나 장 마감 후라 오늘은 더 할 일이 없다
    notify_failed: bool = False  # 알림을 못 보냈으면 실행 자체를 실패로 표시해 GitHub이 알리게 한다
    # 웹앱의 비중, 평가손익, 수익 곡선용 기록. 알림 본문에는 쓰지 않는다.
    balance: Balance | None = None  # 주문 전 계좌 잔고(현금, 총평가)
    # 이번 실행에서 쓴 종목 가격. 매매 실행은 전날 종가, 손절 확인은 지금 가격
    prices: dict[str, Decimal] = Field(default_factory=dict)
    benchmark: BenchmarkClose | None = None  # 비교 지수 종가(매매 실행만, 못 받으면 없음)


_MODE = {"paper": "모의투자", "live": "실전"}
_STATUS = {
    RunStatus.OK: "완료",
    RunStatus.PAUSED: "일시정지",
    RunStatus.SKIPPED: "건너뜀",
    RunStatus.ERROR: "오류",
}
_SIDE = {Side.BUY: "매수", Side.SELL: "매도"}
_ORDER_STATUS = {
    OrderStatus.SUBMITTED: "접수",
    OrderStatus.PARTIALLY_FILLED: "부분체결",
    OrderStatus.FILLED: "체결",
    OrderStatus.REJECTED: "거부",
    OrderStatus.CANCELLED: "취소",
    OrderStatus.EXPIRED: "만료",
}


def report_title(report: RunReport) -> str:
    """알림 제목. 예: [모의투자] 국내 · 완료, [모의투자] 미국 · 완료 · 손절 확인"""
    mode = _MODE.get(report.mode, report.mode)
    title = f"[{mode}] {report.market.label} · {_STATUS[report.status]}"
    if report.stop_loss_only:
        title += " · 손절 확인"
    return f"{title} · 시험 실행" if report.dry_run else title


def report_body(report: RunReport, names: Mapping[str, str] | None = None) -> str:
    """알림 본문."""
    names = names or {}
    fmt = report.market.format_price

    def label(symbol: str) -> str:
        name = names.get(symbol)
        return f"{name}({symbol})" if name else symbol

    lines = []
    if report.message:
        lines.append(report.message)
    if report.error:
        lines.append(f"오류: {report.error}")

    if report.fills:
        lines.append("새 체결")
        for f in report.fills:
            o = f.order
            lines.append(
                f" · {_SIDE[o.side]} {label(o.symbol)} {f.qty}주 @ {fmt(f.price)} [{o.strategy}]"
            )

    reasons = {(s.symbol, s.strategy): s.reason for s in report.signals}
    if report.orders:
        lines.append(f"주문 {len(report.orders)}건")
        for r in report.orders:
            o = r.order
            price = f"지정가 {fmt(o.limit_price)}" if o.limit_price else "시장가"
            status = _ORDER_STATUS[r.status] + (f": {r.message}" if r.message else "")
            lines.append(
                f" · {_SIDE[o.side]} {label(o.symbol)} {o.qty}주 {price} [{o.strategy}] → {status}"
            )
            if reason := reasons.get((o.symbol, o.strategy)):
                lines.append(f"   ({reason})")

    if report.planned:
        lines.append(f"주문 예정 {len(report.planned)}건 (시험 실행이라 내지 않음)")
        for o in report.planned:
            price = f"지정가 {fmt(o.limit_price)}" if o.limit_price else "시장가"
            lines.append(f" · {_SIDE[o.side]} {label(o.symbol)} {o.qty}주 {price} [{o.strategy}]")
            if reason := reasons.get((o.symbol, o.strategy)):
                lines.append(f"   ({reason})")

    if report.skipped:
        lines.append(f"주문 안 함 {len(report.skipped)}건")
        for s in report.skipped:
            sig = s.signal
            lines.append(f" · {_SIDE[sig.side]} {label(sig.symbol)} [{sig.strategy}]: {s.reason}")

    if report.warnings:
        lines.append("확인 필요")
        lines.extend(f" · {w}" for w in report.warnings)

    if report.status is RunStatus.OK and not report.signals and not report.fills:
        lines.append(
            "손절할 종목이 없습니다." if report.stop_loss_only else "오늘은 신호가 없습니다."
        )
    return "\n".join(lines)


def format_report(report: RunReport, names: Mapping[str, str] | None = None) -> str:
    """기록(웹앱 알림함)용 전체 문장. 시간은 한국 시간으로 보여준다."""
    header = f"{report_title(report)} · {format_kst(report.started_at)}"
    body = report_body(report, names)
    return f"{header}\n{body}" if body else header
