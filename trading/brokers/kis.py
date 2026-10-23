"""KIS(한국투자증권) 증권사 어댑터.

사용하는 API (참고: github.com/koreainvestment/open-trading-api, 2026-10 기준)
국내
- 현재가        GET  /uapi/domestic-stock/v1/quotations/inquire-price               FHKST01010100
- 일봉(100개)   GET  /uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice FHKST03010100
- 주문(현금)    POST /uapi/domestic-stock/v1/trading/order-cash      매수 TTTC0012U 매도 TTTC0011U
- 잔고          GET  /uapi/domestic-stock/v1/trading/inquire-balance              TTTC8434R
- 매수가능      GET  /uapi/domestic-stock/v1/trading/inquire-psbl-order           TTTC8908R
- 일별주문체결  GET  /uapi/domestic-stock/v1/trading/inquire-daily-ccld           TTTC0081R
미국
- 현재가        GET  /uapi/overseas-price/v1/quotations/price                     HHDFS00000300
- 일봉(100개)   GET  /uapi/overseas-price/v1/quotations/dailyprice                HHDFS76240000
- 주문(지정가)  POST /uapi/overseas-stock/v1/trading/order           매수 TTTT1002U 매도 TTTT1006U
- 잔고          GET  /uapi/overseas-stock/v1/trading/inquire-balance              TTTS3012R
- 매수가능금액  GET  /uapi/overseas-stock/v1/trading/inquire-psamount             TTTS3007R
- 주문체결내역  GET  /uapi/overseas-stock/v1/trading/inquire-ccnl                 TTTS3035R
모의투자 TR ID는 앞 글자가 V다. 단 미국 매도는 VTTT1001U.

모의투자 제약: 미국은 지정가만, 미국 잔고는 거래소별로 따로 조회, 미국 주문체결내역은
종목/거래소/구분을 전체("")로만 조회, 국내 주문체결은 한 번에 15건, 잔고는 20건씩.
미국 일반 주문은 정규장 시간에만 받는다(그 밖의 시간은 예약주문 API가 따로 있다).
"""

import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from trading.brokers.base import BrokerAdapter, BrokerError
from trading.brokers.kis_client import KisApiError, KisClient, as_list
from trading.core.calendar import is_trading_day
from trading.core.clock import KST, now_utc, trading_date
from trading.core.models import (
    BAR_COLUMNS,
    Balance,
    Holding,
    Instrument,
    Market,
    Order,
    OrderResult,
    OrderStatus,
    OrderType,
    Side,
)
from trading.universe import Universe

logger = logging.getLogger(__name__)

# 종목 목록(universe)의 거래소 이름 → KIS 주문/잔고용 코드
ORDER_EXCHANGE = {"NASDAQ": "NASD", "NYSE": "NYSE", "AMEX": "AMEX"}
# KIS 주문/잔고용 코드 → 시세용 코드
QUOTE_EXCHANGE = {"NASD": "NAS", "NYSE": "NYS", "AMEX": "AMS"}
_US_EXCHANGES = ("NASD", "NYSE", "AMEX")

_KR = "/uapi/domestic-stock/v1"
_US_PRICE = "/uapi/overseas-price/v1/quotations"
_US_TRADE = "/uapi/overseas-stock/v1/trading"
_PAGE = 100  # 일봉 한 번에 받는 개수


class TR:
    """(실전, 모의) TR ID."""

    KR_PRICE = ("FHKST01010100", "FHKST01010100")
    KR_DAILY = ("FHKST03010100", "FHKST03010100")
    KR_BUY = ("TTTC0012U", "VTTC0012U")
    KR_SELL = ("TTTC0011U", "VTTC0011U")
    KR_BALANCE = ("TTTC8434R", "VTTC8434R")
    KR_BUYABLE = ("TTTC8908R", "VTTC8908R")
    KR_ORDERS = ("TTTC0081R", "VTTC0081R")
    US_PRICE = ("HHDFS00000300", "HHDFS00000300")
    US_DAILY = ("HHDFS76240000", "HHDFS76240000")
    US_BUY = ("TTTT1002U", "VTTT1002U")
    US_SELL = ("TTTT1006U", "VTTT1001U")
    US_BALANCE = ("TTTS3012R", "VTTS3012R")
    US_BUYABLE = ("TTTS3007R", "VTTS3007R")
    US_ORDERS = ("TTTS3035R", "VTTS3035R")


def parse_account(account_no: str) -> tuple[str, str]:
    """'12345678-01' → ('12345678', '01')"""
    digits = account_no.replace("-", "").strip()
    if len(digits) != 10 or not digits.isdigit():
        raise ValueError("KIS 계좌번호는 12345678-01 형식(숫자 10자리)이어야 합니다")
    return digits[:8], digits[8:]


class KisBroker(BrokerAdapter):
    name = "kis"

    def __init__(
        self,
        client: KisClient,
        account_no: str,
        universe: Universe,
        *,
        clock: Callable[[], datetime] = now_utc,
    ) -> None:
        self._client = client
        self._paper = client.paper
        self._cano, self._prdt = parse_account(account_no)
        self._clock = clock
        # 미국 종목의 거래소. 목록에서 빠진 보유 종목은 잔고 조회 때 알게 된다.
        self._exchange: dict[str, str] = {}
        for inst in universe.get(Market.US, []):
            if inst.exchange:
                if inst.exchange not in ORDER_EXCHANGE:
                    raise ValueError(f"{inst.symbol}: 알 수 없는 거래소 {inst.exchange}")
                self._exchange[inst.symbol] = ORDER_EXCHANGE[inst.exchange]
        kr = universe.get(Market.KR, [])
        # 매수 가능 금액 조회에 종목 코드가 필요해 목록의 첫 종목을 쓴다
        self._kr_probe = kr[0].symbol if kr else "005930"
        self._us_probe = next(iter(self._exchange), "AAPL")
        self._exchange.setdefault(self._us_probe, "NASD")

    def add_instrument(self, instrument: Instrument) -> None:
        """목록(universe)에 없는 미국 종목의 거래소를 알려 준다(예: 비교용 SPY)."""
        if instrument.market is Market.US and instrument.exchange:
            self._exchange[instrument.symbol] = ORDER_EXCHANGE[instrument.exchange]

    # ---- BrokerAdapter ----

    def is_trading_day(self, market: Market, day: date) -> bool:
        return is_trading_day(market, day)

    def supported_order_types(self, market: Market) -> frozenset[OrderType]:
        if market is Market.KR:
            return frozenset({OrderType.MARKET, OrderType.LIMIT})
        return frozenset({OrderType.LIMIT})

    def get_price(self, market: Market, symbol: str) -> Decimal:
        if market is Market.KR:
            out = self._get(
                f"{_KR}/quotations/inquire-price",
                TR.KR_PRICE,
                {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol},
            ).get("output", {})
            return _positive(out.get("stck_prpr"), f"{symbol} 현재가")
        out = self._get(
            f"{_US_PRICE}/price",
            TR.US_PRICE,
            {"AUTH": "", "EXCD": self._quote_exchange(symbol), "SYMB": symbol},
            overseas=True,
        ).get("output", {})
        # 장 시작 전에는 현재가가 비어 있을 수 있어 전일 종가를 쓴다
        last = _dec(out.get("last"))
        return last if last > 0 else _positive(out.get("base"), f"{symbol} 현재가")

    def get_daily_bars(self, market: Market, symbol: str, count: int) -> pd.DataFrame:
        today = trading_date(market, self._clock())
        rows: dict[str, dict[str, Any]] = {}
        end = today
        for _ in range(count // _PAGE + 2):
            page = (
                self._kr_bar_page(symbol, end)
                if market is Market.KR
                else self._us_bar_page(symbol, end, first=not rows)
            )
            new = {d: r for d, r in page.items() if d not in rows}
            if not new:
                break
            rows.update(new)
            if len(rows) >= count:
                break
            end = datetime.strptime(min(new), "%Y%m%d").date() - timedelta(days=1)
        if not rows:
            raise BrokerError(f"KIS: {market} {symbol} 일봉이 없습니다")
        df = pd.DataFrame.from_dict(rows, orient="index", columns=list(BAR_COLUMNS))
        df.index = pd.to_datetime(df.index, format="%Y%m%d")
        df = df.sort_index()
        return df.tail(count)

    def get_balance(self, market: Market) -> Balance:
        if market is Market.KR:
            _, summary = self._kr_balance()
            buyable = self._get(
                f"{_KR}/trading/inquire-psbl-order",
                TR.KR_BUYABLE,
                self._account()
                | {
                    "PDNO": self._kr_probe,
                    "ORD_UNPR": "0",
                    "ORD_DVSN": "01",
                    "CMA_EVLU_AMT_ICLD_YN": "N",
                    "OVRS_ICLD_YN": "N",
                },
            ).get("output", {})
            # 미수 없이 살 수 있는 금액만 쓴다(미수 매수를 하지 않는다)
            return Balance(
                market=market,
                cash=_dec(buyable.get("nrcvb_buy_amt")),
                total_equity=_dec(summary.get("tot_evlu_amt")),
            )
        holdings = self._us_holding_rows()
        price = self.get_price(Market.US, self._us_probe)
        buyable = self._get(
            f"{_US_TRADE}/inquire-psamount",
            TR.US_BUYABLE,
            self._account()
            | {
                "OVRS_EXCG_CD": self._exchange[self._us_probe],
                "OVRS_ORD_UNPR": format(price, "f"),
                "ITEM_CD": self._us_probe,
            },
            overseas=True,
        ).get("output", {})
        cash = _dec(buyable.get("ord_psbl_frcr_amt"))
        stocks = sum((_dec(r.get("ovrs_stck_evlu_amt")) for r in holdings), Decimal(0))
        return Balance(market=market, cash=cash, total_equity=cash + stocks)

    def get_holdings(self, market: Market) -> list[Holding]:
        if market is Market.KR:
            rows, _ = self._kr_balance()
            return [
                Holding(
                    market=market,
                    symbol=r["pdno"],
                    qty=int(_dec(r.get("hldg_qty"))),
                    avg_price=_positive(r.get("pchs_avg_pric"), f"{r['pdno']} 평균단가"),
                )
                for r in rows
                if _dec(r.get("hldg_qty")) > 0  # 당일 전량 매도한 종목은 0주로 남는다
            ]
        return [
            Holding(
                market=market,
                symbol=r["ovrs_pdno"],
                qty=int(_dec(r.get("ovrs_cblc_qty"))),
                avg_price=_positive(r.get("pchs_avg_pric"), f"{r['ovrs_pdno']} 평균단가"),
            )
            for r in self._us_holding_rows()
        ]

    def get_orders(self, market: Market, day: date) -> list[OrderResult]:
        ymd = day.strftime("%Y%m%d")
        if market is Market.KR:
            rows, _ = self._client.get_pages(
                f"{_KR}/trading/inquire-daily-ccld",
                self._tr(TR.KR_ORDERS),
                self._account()
                | {
                    "INQR_STRT_DT": ymd,
                    "INQR_END_DT": ymd,
                    "SLL_BUY_DVSN_CD": "00",
                    "PDNO": "",
                    "CCLD_DVSN": "00",
                    "INQR_DVSN": "00",
                    "INQR_DVSN_3": "00",
                    "ORD_GNO_BRNO": "",
                    "ODNO": "",
                    "INQR_DVSN_1": "",
                    "CTX_AREA_FK100": "",
                    "CTX_AREA_NK100": "",
                },
                list_key="output1",
                ctx_keys=("CTX_AREA_FK100", "CTX_AREA_NK100"),
            )
            parse = parse_kr_order
        else:
            everything = "" if self._paper else "%"  # 모의투자는 ""(전체)만 된다
            rows, _ = self._client.get_pages(
                f"{_US_TRADE}/inquire-ccnl",
                self._tr(TR.US_ORDERS),
                self._account()
                | {
                    "PDNO": everything,
                    "ORD_STRT_DT": ymd,
                    "ORD_END_DT": ymd,
                    "SLL_BUY_DVSN": "00",
                    "CCLD_NCCS_DVSN": "00",
                    "OVRS_EXCG_CD": everything,
                    "SORT_SQN": "DS",
                    "ORD_DT": "",
                    "ORD_GNO_BRNO": "",
                    "ODNO": "",
                    "CTX_AREA_NK200": "",
                    "CTX_AREA_FK200": "",
                },
                list_key="output",
                ctx_keys=("CTX_AREA_FK200", "CTX_AREA_NK200"),
                overseas=True,
            )
            parse = parse_us_order
        return [r for row in rows if (r := parse(row)) is not None]

    def _submit_order(self, order: Order) -> OrderResult:
        try:
            if order.market is Market.KR:
                resp = self._client.post(
                    f"{_KR}/trading/order-cash",
                    self._tr(TR.KR_BUY if order.side is Side.BUY else TR.KR_SELL),
                    self._account()
                    | {
                        "PDNO": order.symbol,
                        "ORD_DVSN": "00" if order.order_type is OrderType.LIMIT else "01",
                        "ORD_QTY": str(order.qty),
                        "ORD_UNPR": str(int(order.limit_price)) if order.limit_price else "0",
                        "EXCG_ID_DVSN_CD": "KRX",
                        "SLL_TYPE": "01" if order.side is Side.SELL else "",
                        "CNDT_PRIC": "",
                    },
                )
            else:
                assert order.limit_price is not None  # 미국은 지정가만(base에서 확인)
                resp = self._client.post(
                    f"{_US_TRADE}/order",
                    self._tr(TR.US_BUY if order.side is Side.BUY else TR.US_SELL),
                    self._account()
                    | {
                        "OVRS_EXCG_CD": self._order_exchange(order.symbol),
                        "PDNO": order.symbol,
                        "ORD_QTY": str(order.qty),
                        "OVRS_ORD_UNPR": format(order.limit_price, "f"),
                        "CTAC_TLNO": "",
                        "MGCO_APTM_ODNO": "",
                        "SLL_TYPE": "00" if order.side is Side.SELL else "",
                        "ORD_SVR_DVSN_CD": "0",
                        "ORD_DVSN": "00",
                    },
                    overseas=True,
                )
        except KisApiError as e:
            # 증권사가 받지 않은 주문(잔고 부족, 장 시간 아님 등)
            return OrderResult(
                order=order,
                broker_order_id=None,
                status=OrderStatus.REJECTED,
                message=f"{e.message} ({e.code})",
                created_at=self._clock(),
            )
        out = resp.body.get("output") or {}
        odno = out.get("ODNO") or out.get("odno")
        if not odno:
            raise BrokerError(f"KIS 주문 응답에 주문번호가 없습니다: {order.symbol}")
        return OrderResult(
            order=order,
            broker_order_id=str(odno),
            status=OrderStatus.SUBMITTED,
            message=str(resp.body.get("msg1", "")).strip(),
            created_at=self._clock(),
        )

    # ---- 내부 ----

    def _tr(self, pair: tuple[str, str]) -> str:
        return pair[1] if self._paper else pair[0]

    def _get(
        self, path: str, pair: tuple[str, str], params: dict[str, str], *, overseas: bool = False
    ) -> dict[str, Any]:
        return self._client.get(path, self._tr(pair), params, overseas=overseas).body

    def _account(self) -> dict[str, str]:
        return {"CANO": self._cano, "ACNT_PRDT_CD": self._prdt}

    def _order_exchange(self, symbol: str) -> str:
        exchange = self._exchange.get(symbol)
        if exchange is None:
            raise BrokerError(
                f"KIS: {symbol}의 거래소를 모릅니다. universe.toml에 exchange를 넣어 주세요"
            )
        return exchange

    def _quote_exchange(self, symbol: str) -> str:
        return QUOTE_EXCHANGE[self._order_exchange(symbol)]

    def _kr_balance(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        rows, first = self._client.get_pages(
            f"{_KR}/trading/inquire-balance",
            self._tr(TR.KR_BALANCE),
            self._account()
            | {
                "AFHR_FLPR_YN": "N",
                "OFL_YN": "",
                "INQR_DVSN": "02",
                "UNPR_DVSN": "01",
                "FUND_STTL_ICLD_YN": "N",
                "FNCG_AMT_AUTO_RDPT_YN": "N",
                "PRCS_DVSN": "00",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            },
            list_key="output1",
            ctx_keys=("CTX_AREA_FK100", "CTX_AREA_NK100"),
        )
        summary = first.get("output2")
        summary = summary[0] if isinstance(summary, list) and summary else summary or {}
        return rows, summary

    def _us_holding_rows(self) -> list[dict[str, Any]]:
        # 실전은 NASD가 미국 전체, 모의투자는 거래소별로 따로 조회해야 한다
        exchanges = _US_EXCHANGES if self._paper else ("NASD",)
        found: dict[str, dict[str, Any]] = {}
        for exchange in exchanges:
            rows, _ = self._client.get_pages(
                f"{_US_TRADE}/inquire-balance",
                self._tr(TR.US_BALANCE),
                self._account()
                | {
                    "OVRS_EXCG_CD": exchange,
                    "TR_CRCY_CD": "USD",
                    "CTX_AREA_FK200": "",
                    "CTX_AREA_NK200": "",
                },
                list_key="output1",
                ctx_keys=("CTX_AREA_FK200", "CTX_AREA_NK200"),
                overseas=True,
            )
            for r in rows:
                symbol = r.get("ovrs_pdno")
                if symbol and _dec(r.get("ovrs_cblc_qty")) > 0:
                    found[symbol] = r
                    if r.get("ovrs_excg_cd") in QUOTE_EXCHANGE:
                        self._exchange[symbol] = r["ovrs_excg_cd"]
        return list(found.values())

    def _kr_bar_page(self, symbol: str, end: date) -> dict[str, dict[str, Any]]:
        body = self._get(
            f"{_KR}/quotations/inquire-daily-itemchartprice",
            TR.KR_DAILY,
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": symbol,
                # 100거래일보다 넉넉한 달력 기간을 준다. 응답은 끝 날짜부터 최대 100개.
                "FID_INPUT_DATE_1": (end - timedelta(days=160)).strftime("%Y%m%d"),
                "FID_INPUT_DATE_2": end.strftime("%Y%m%d"),
                "FID_PERIOD_DIV_CODE": "D",
                "FID_ORG_ADJ_PRC": "0",  # 수정주가
            },
        )
        return {
            r["stck_bsop_date"]: {
                "open": float(_dec(r.get("stck_oprc"))),
                "high": float(_dec(r.get("stck_hgpr"))),
                "low": float(_dec(r.get("stck_lwpr"))),
                "close": float(_dec(r.get("stck_clpr"))),
                "volume": int(_dec(r.get("acml_vol"))),
            }
            for r in as_list(body.get("output2"))
            if r.get("stck_bsop_date") and _dec(r.get("stck_clpr")) > 0
        }

    def _us_bar_page(self, symbol: str, end: date, *, first: bool) -> dict[str, dict[str, Any]]:
        body = self._get(
            f"{_US_PRICE}/dailyprice",
            TR.US_DAILY,
            {
                "AUTH": "",
                "EXCD": self._quote_exchange(symbol),
                "SYMB": symbol,
                "GUBN": "0",  # 일봉
                "BYMD": "" if first else end.strftime("%Y%m%d"),  # 비우면 오늘부터
                "MODP": "1",  # 수정주가
            },
            overseas=True,
        )
        return {
            r["xymd"]: {
                "open": float(_dec(r.get("open"))),
                "high": float(_dec(r.get("high"))),
                "low": float(_dec(r.get("low"))),
                "close": float(_dec(r.get("clos"))),
                "volume": int(_dec(r.get("tvol"))),
            }
            for r in as_list(body.get("output2"))
            if r.get("xymd") and _dec(r.get("clos")) > 0
        }


# ---- 응답 해석 (순수 함수: 테스트하기 쉽게 밖으로 뺐다) ----


def parse_kr_order(row: dict[str, Any]) -> OrderResult | None:
    """국내 일별주문체결 한 줄 → OrderResult. 정정/취소 주문 줄은 건너뛴다."""
    if str(row.get("orgn_odno", "")).strip():
        return None
    qty = int(_dec(row.get("ord_qty")))
    if qty <= 0:
        return None
    filled = int(_dec(row.get("tot_ccld_qty")))
    if filled >= qty:
        status = OrderStatus.FILLED
    elif _dec(row.get("rjct_qty")) > 0 and filled == 0:
        status = OrderStatus.REJECTED
    elif str(row.get("cncl_yn", "")).upper() == "Y" or _dec(row.get("cnc_cfrm_qty")) > 0:
        status = OrderStatus.CANCELLED
    elif filled > 0:
        status = OrderStatus.PARTIALLY_FILLED
    else:
        status = OrderStatus.SUBMITTED
    price = _dec(row.get("ord_unpr"))
    return _result(
        market=Market.KR,
        odno=row["odno"],
        symbol=row["pdno"],
        side=Side.SELL if row.get("sll_buy_dvsn_cd") == "01" else Side.BUY,
        qty=qty,
        limit=price if price > 0 and row.get("ord_dvsn_cd") != "01" else None,
        status=status,
        filled=filled,
        avg=_dec(row.get("avg_prvs")),
        created_at=_kst(row.get("ord_dt"), row.get("ord_tmd")),
    )


def parse_us_order(row: dict[str, Any]) -> OrderResult | None:
    """미국 주문체결내역 한 줄 → OrderResult. 정정/취소 주문 줄은 건너뛴다."""
    if str(row.get("orgn_odno", "")).strip():
        return None
    qty = int(_dec(row.get("ft_ord_qty")))
    if qty <= 0:
        return None
    filled = int(_dec(row.get("ft_ccld_qty")))
    state = str(row.get("prcs_stat_name", ""))
    if filled >= qty:
        status = OrderStatus.FILLED
    elif str(row.get("rjct_rson", "")).strip() or "거부" in state:
        status = OrderStatus.REJECTED if filled == 0 else OrderStatus.PARTIALLY_FILLED
    elif "취소" in state:
        status = OrderStatus.CANCELLED
    elif filled > 0:
        status = OrderStatus.PARTIALLY_FILLED
    else:
        status = OrderStatus.SUBMITTED
    return _result(
        market=Market.US,
        odno=row["odno"],
        symbol=row["pdno"],
        side=Side.SELL if row.get("sll_buy_dvsn_cd") == "01" else Side.BUY,
        qty=qty,
        limit=_dec(row.get("ft_ord_unpr3")) or None,
        status=status,
        filled=filled,
        avg=_dec(row.get("ft_ccld_unpr3")),
        # 국내 주문일시(KST)가 있으면 그것을, 없으면 현지 주문일시를 쓴다
        created_at=_kst(row.get("dmst_ord_dt"), row.get("thco_ord_tmd"))
        or _local(Market.US, row.get("ord_dt"), row.get("ord_tmd")),
        message=str(row.get("rjct_rson_name") or row.get("rjct_rson") or "").strip(),
    )


def _result(
    *,
    market: Market,
    odno: str,
    symbol: str,
    side: Side,
    qty: int,
    limit: Decimal | None,
    status: OrderStatus,
    filled: int,
    avg: Decimal,
    created_at: datetime | None,
    message: str = "",
) -> OrderResult:
    order = Order(
        market=market,
        symbol=symbol,
        side=side,
        qty=qty,
        order_type=OrderType.LIMIT if limit else OrderType.MARKET,
        limit_price=limit,
        strategy="",  # 증권사는 어느 전략의 주문인지 모른다
        client_order_id=f"KIS-{odno}",
    )
    return OrderResult(
        order=order,
        broker_order_id=str(odno),
        status=status,
        filled_qty=min(filled, qty),
        avg_fill_price=avg if filled > 0 and avg > 0 else None,
        message=message,
        created_at=created_at or now_utc(),
    )


def _dec(value: Any) -> Decimal:
    if value is None:
        return Decimal(0)
    try:
        return Decimal(str(value).strip().replace(",", "") or "0")
    except InvalidOperation:
        return Decimal(0)


def _positive(value: Any, what: str) -> Decimal:
    number = _dec(value)
    if number <= 0:
        raise BrokerError(f"KIS: {what} 값이 없습니다")
    return number


def _kst(ymd: Any, hms: Any) -> datetime | None:
    return _parse_time(ymd, hms, KST)


def _local(market: Market, ymd: Any, hms: Any) -> datetime | None:
    return _parse_time(ymd, hms, market.timezone)


def _parse_time(ymd: Any, hms: Any, tz: ZoneInfo) -> datetime | None:
    ymd, hms = str(ymd or "").strip(), str(hms or "").strip().ljust(6, "0")[:6]
    if len(ymd) != 8:
        return None
    try:
        return datetime.strptime(ymd + hms, "%Y%m%d%H%M%S").replace(tzinfo=tz)
    except ValueError:
        return None
