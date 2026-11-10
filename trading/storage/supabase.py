"""Supabase 저장소. REST API(PostgREST)를 httpx로 직접 부른다.

엔진은 secret 키(sb_secret_...)를 쓰며 RLS를 거치지 않는다. 표 구조는 supabase/schema.sql.
보유분과 주문은 mode(paper/live)별로 나눠 읽고 쓴다.
"""

import json
import logging
import time
from collections.abc import Callable, Sequence
from datetime import date
from decimal import Decimal
from typing import Any

import httpx

from trading.core.clock import trading_date
from trading.core.models import Market, OrderResult, Position
from trading.engine.report import RunReport
from trading.storage.base import PauseState, PushSubscription, Store, StoreError

logger = logging.getLogger(__name__)

_OPEN_STATUSES = "in.(SUBMITTED,PARTIALLY_FILLED)"
_UPSERT = "resolution=merge-duplicates,return=minimal"


class SupabaseStore(Store):
    def __init__(
        self,
        url: str,
        key: str,
        mode: str,
        *,
        transport: httpx.BaseTransport | None = None,
        retries: int = 2,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        headers = {"apikey": key}
        # 새 secret 키는 apikey 헤더에만 넣는다.
        # 예전 service_role(JWT) 키는 Authorization에도 넣는다.
        if not key.startswith("sb_"):
            headers["Authorization"] = f"Bearer {key}"
        self._client = httpx.Client(
            base_url=f"{url.rstrip('/')}/rest/v1",
            headers=headers,
            timeout=15,
            transport=transport,
        )
        self.mode = mode
        self._retries = retries
        self._sleep = sleep

    # ---- 일시정지 스위치 ----

    def get_pause(self) -> PauseState:
        rows = self._request(
            "GET", "trading_switch", params={"select": "paused,reason,updated_at", "id": "eq.1"}
        )
        if not rows:
            raise StoreError(
                "일시정지 스위치가 없습니다. supabase/schema.sql을 실행했는지 확인하세요"
            )
        return PauseState.model_validate(rows[0])

    def set_pause(self, paused: bool, reason: str = "") -> None:
        self._request(
            "PATCH",
            "trading_switch",
            params={"id": "eq.1"},
            body={"paused": paused, "reason": reason},
            prefer="return=minimal",
        )

    # ---- 보유분 ----

    def get_positions(self, market: Market) -> list[Position]:
        rows = self._request("GET", "positions", params=self._filter(market))
        return [Position.model_validate(r) for r in rows]

    def upsert_position(self, position: Position) -> None:
        self._request(
            "POST",
            "positions",
            params={"on_conflict": "mode,market,symbol,strategy"},
            body={
                "mode": self.mode,
                "market": position.market,
                "symbol": position.symbol,
                "strategy": position.strategy,
                "qty": position.qty,
                "avg_price": str(position.avg_price),
                "opened_at": position.opened_at.isoformat(),
            },
            prefer=_UPSERT,
        )

    def delete_position(self, market: Market, symbol: str, strategy: str) -> None:
        params = self._filter(market) | {"symbol": f"eq.{symbol}", "strategy": f"eq.{strategy}"}
        self._request("DELETE", "positions", params=params, prefer="return=minimal")

    # ---- 주문 ----

    def get_orders(self, market: Market, day: date) -> list[OrderResult]:
        params = self._filter(market) | {"trade_date": f"eq.{day.isoformat()}"}
        return [_order_from_row(r) for r in self._request("GET", "orders", params=params)]

    def get_open_orders(self, market: Market) -> list[OrderResult]:
        params = self._filter(market) | {"status": _OPEN_STATUSES}
        return [_order_from_row(r) for r in self._request("GET", "orders", params=params)]

    def save_order(self, result: OrderResult) -> None:
        o = result.order
        self._request(
            "POST",
            "orders",
            params={"on_conflict": "mode,client_order_id"},
            body={
                "mode": self.mode,
                "client_order_id": o.client_order_id,
                "market": o.market,
                "symbol": o.symbol,
                "side": o.side,
                "qty": o.qty,
                "order_type": o.order_type,
                "limit_price": _num(o.limit_price),
                "strategy": o.strategy,
                "reason": o.reason,
                "trade_date": trading_date(o.market, result.created_at).isoformat(),
                "broker_order_id": result.broker_order_id,
                "status": result.status,
                "filled_qty": result.filled_qty,
                "avg_fill_price": _num(result.avg_fill_price),
                "message": result.message,
                "created_at": result.created_at.isoformat(),
            },
            prefer=_UPSERT,
        )

    # ---- 실행 기록 ----

    def save_run(self, report: RunReport, summary: str) -> str | None:
        rows = self._request(
            "POST",
            "runs",
            params={"select": "id"},
            body={
                "mode": report.mode,
                "market": report.market,
                "status": report.status,
                "message": report.message,
                "error": report.error,
                "started_at": report.started_at.isoformat(),
                "finished_at": report.finished_at.isoformat() if report.finished_at else None,
                "summary": summary,
                "report": report.model_dump(mode="json"),
            },
            prefer="return=representation",
            retry=False,  # 다시 보내면 같은 기록이 두 번 생길 수 있다
        )
        return str(rows[0]["id"]) if rows else None

    # ---- 푸시 구독 ----

    def get_push_subscriptions(self) -> list[PushSubscription]:
        rows = self._request("GET", "push_subscriptions", params={"select": "endpoint,p256dh,auth"})
        return [PushSubscription.model_validate(r) for r in rows]

    def delete_push_subscription(self, endpoint: str) -> None:
        self._request(
            "DELETE",
            "push_subscriptions",
            params={"endpoint": f"eq.{endpoint}"},
            prefer="return=minimal",
        )

    # ---- 엔진 캐시 ----

    def get_cache(self, name: str) -> str | None:
        rows = self._request(
            "GET", "engine_cache", params={"select": "value", "name": f"eq.{name}"}
        )
        return str(rows[0]["value"]) if rows else None

    def put_cache(self, name: str, value: str) -> None:
        self._request(
            "POST",
            "engine_cache",
            params={"on_conflict": "name"},
            body={"name": name, "value": value},
            prefer=_UPSERT,
        )

    def delete_cache(self, name: str) -> None:
        self._request(
            "DELETE", "engine_cache", params={"name": f"eq.{name}"}, prefer="return=minimal"
        )

    # ---- 연결 확인 도구용 ----

    def check_columns(self, table: str, columns: Sequence[str]) -> None:
        """표에 칸들이 있는지 본다. 없으면(schema.sql을 다시 실행하기 전) StoreError."""
        try:
            self._request("GET", table, params={"select": ",".join(columns), "limit": "1"})
        except StoreError as e:
            raise StoreError(
                f"{table} 표에서 {', '.join(columns)} 칸을 읽지 못했습니다. "
                f"supabase/schema.sql을 다시 실행했는지 확인하세요 ({e})"
            ) from e

    # ---- 내부 ----

    def _filter(self, market: Market) -> dict[str, str]:
        return {"mode": f"eq.{self.mode}", "market": f"eq.{market}"}

    def _request(
        self,
        method: str,
        table: str,
        *,
        params: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
        prefer: str | None = None,
        retry: bool = True,
    ) -> Any:
        headers = {"Prefer": prefer} if prefer else {}
        attempts = 1 + (self._retries if retry else 0)
        for attempt in range(1, attempts + 1):
            try:
                resp = self._client.request(
                    method, f"/{table}", params=params, json=body, headers=headers
                )
            except httpx.TransportError as e:
                if attempt < attempts:
                    logger.warning("Supabase 연결 오류, 다시 시도 (%s)", type(e).__name__)
                    self._sleep(attempt)
                    continue
                raise StoreError(f"Supabase 연결 실패({table}): {type(e).__name__}") from e
            if resp.status_code >= 500 and attempt < attempts:
                logger.warning("Supabase %s 응답, 다시 시도", resp.status_code)
                self._sleep(attempt)
                continue
            if resp.status_code >= 400:
                raise StoreError(
                    f"Supabase {method} {table} 실패: {resp.status_code} {resp.text[:200]}"
                )
            # 숫자는 Decimal로 읽어 금액이 float 오차로 바뀌지 않게 한다
            return json.loads(resp.text, parse_float=Decimal) if resp.text else None
        raise AssertionError("unreachable")


def _num(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _order_from_row(row: dict[str, Any]) -> OrderResult:
    return OrderResult.model_validate(
        {
            "order": {
                "market": row["market"],
                "symbol": row["symbol"],
                "side": row["side"],
                "qty": row["qty"],
                "order_type": row["order_type"],
                "limit_price": row["limit_price"],
                "strategy": row["strategy"],
                "client_order_id": row["client_order_id"],
                "reason": row.get("reason") or "",
            },
            "broker_order_id": row["broker_order_id"],
            "status": row["status"],
            "filled_qty": row["filled_qty"],
            "avg_fill_price": row["avg_fill_price"],
            "message": row["message"],
            "created_at": row["created_at"],
        }
    )
