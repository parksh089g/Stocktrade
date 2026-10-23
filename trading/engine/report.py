"""하루 실행 결과. 저장소에 기록하고 알림으로 보낸다."""

from collections.abc import Mapping
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field

from trading.core.clock import format_kst
from trading.core.models import (
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
    notify_failed: bool = False  # 알림을 못 보냈으면 실행 자체를 실패로 표시해 GitHub이 알리게 한다


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


def format_report(report: RunReport, names: Mapping[str, str] | None = None) -> str:
    """알림용 문장. 시간은 한국 시간으로 보여준다."""
    names = names or {}
    market = report.market
    fmt = market.format_price

    def label(symbol: str) -> str:
        name = names.get(symbol)
        return f"{name}({symbol})" if name else symbol

    lines = [
        f"[{_MODE.get(report.mode, report.mode)}] {market.label} · {format_kst(report.started_at)}",
        f"상태: {_STATUS[report.status]}" + (f" - {report.message}" if report.message else ""),
    ]
    if report.error:
        lines.append(f"오류: {report.error}")

    if report.fills:
        lines.append("새 체결")
        for f in report.fills:
            o = f.order
            lines.append(
                f" · {_SIDE[o.side]} {label(o.symbol)} {f.qty}주 @ {fmt(f.price)} [{o.strategy}]"
            )

    if report.orders:
        reasons = {(s.symbol, s.strategy): s.reason for s in report.signals}
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

    if report.skipped:
        lines.append(f"주문 안 함 {len(report.skipped)}건")
        for s in report.skipped:
            sig = s.signal
            lines.append(f" · {_SIDE[sig.side]} {label(sig.symbol)} [{sig.strategy}]: {s.reason}")

    if report.warnings:
        lines.append("확인 필요")
        lines.extend(f" · {w}" for w in report.warnings)

    if report.status is RunStatus.OK and not report.signals and not report.fills:
        lines.append("오늘은 신호가 없습니다.")
    return "\n".join(lines)
