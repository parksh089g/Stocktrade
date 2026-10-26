"""테스트용 가짜 KIS 서버. KisClient/KisBroker가 쓰는 API만 흉내 낸다.

공식 예제(github.com/koreainvestment/open-trading-api)에서 확인한 경로, TR ID,
요청/응답 항목을 따른다.
- 일봉은 최신 날짜부터 최대 100개를 돌려준다
- 잔고/주문체결은 모의투자처럼 몇 건씩 끊어서 주고, tr_cont 헤더와 ctx_area 값으로
  다음 페이지를 알린다
- 모의투자 TR ID(V로 시작)인지, 토큰/앱키 헤더가 맞는지 확인한다
"""

import json
from datetime import date, timedelta
from typing import Any

import httpx

APP_KEY = "PSfake-app-key-1234"
APP_SECRET = "fake-app-secret-5678"


def kr_bar_rows(start: date, closes: list[int]) -> list[dict[str, str]]:
    """평일마다 한 줄씩, 최신 날짜가 앞에 오는 국내 일봉 응답(output2)."""
    rows, day = [], start
    for close in closes:
        while day.weekday() >= 5:
            day += timedelta(days=1)
        rows.append(
            {
                "stck_bsop_date": day.strftime("%Y%m%d"),
                "stck_oprc": str(close - 100),
                "stck_hgpr": str(close + 200),
                "stck_lwpr": str(close - 300),
                "stck_clpr": str(close),
                "acml_vol": "123456",
            }
        )
        day += timedelta(days=1)
    return list(reversed(rows))


def us_bar_rows(start: date, closes: list[float]) -> list[dict[str, str]]:
    rows, day = [], start
    for close in closes:
        while day.weekday() >= 5:
            day += timedelta(days=1)
        rows.append(
            {
                "xymd": day.strftime("%Y%m%d"),
                "open": f"{close - 1:.4f}",
                "high": f"{close + 2:.4f}",
                "low": f"{close - 3:.4f}",
                "clos": f"{close:.4f}",
                "tvol": "1000",
            }
        )
        day += timedelta(days=1)
    return list(reversed(rows))


class FakeKis:
    def __init__(self, *, paper: bool = True) -> None:
        self.paper = paper
        self.token = "fake-token-0001"
        self.token_expired_at = "2026-10-27 08:00:00"
        self.tokens_issued = 0
        self.requests: list[httpx.Request] = []
        # 앞에서부터 꺼내 쓴다: Response면 그대로, dict면 그 응답(HTTP 200), int면 그 상태 코드,
        # 예외면 던진다
        self.inject: list[Any] = []
        self.reject_token = False  # True면 다음 조회에 토큰 만료 응답

        self.kr_prices = {"005930": "70000", "000660": "200000"}
        self.kr_bars: dict[str, list[dict[str, str]]] = {}
        self.kr_holdings: list[dict[str, str]] = []
        self.kr_summary = {"tot_evlu_amt": "10000000", "dnca_tot_amt": "10000000"}
        self.kr_buyable = "10000000"
        self.kr_orders: list[dict[str, str]] = []

        self.us_prices = {"AAPL": {"last": "180.5000", "base": "179.0000"}}
        self.us_bars: dict[str, list[dict[str, str]]] = {}
        self.us_holdings: dict[str, list[dict[str, str]]] = {}  # 거래소 코드별
        self.us_buyable = "10000.00"
        self.us_orders: list[dict[str, str]] = []

        self.reject_orders_with: str | None = None
        self.order_date = "20261026"  # 접수한 주문에 붙일 주문일자
        self.page_size = 2  # 잔고/주문체결 한 번에 주는 건수(연속 조회 시험용)
        self._next_odno = 1

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def tr_ids(self) -> list[str]:
        return [r.headers.get("tr_id", "") for r in self.requests if "tr_id" in r.headers]

    # ---- 라우팅 ----

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.inject:
            item = self.inject.pop(0)
            if isinstance(item, Exception):
                raise item
            if isinstance(item, httpx.Response):
                return item
            if isinstance(item, int):
                return httpx.Response(item, json={"rt_cd": "1", "msg_cd": "X", "msg1": "error"})
            return httpx.Response(200, json=item)

        path = request.url.path
        if path == "/oauth2/tokenP":
            body = json.loads(request.content)
            assert body == {
                "grant_type": "client_credentials",
                "appkey": APP_KEY,
                "appsecret": APP_SECRET,
            }
            self.tokens_issued += 1
            return httpx.Response(
                200,
                json={
                    "access_token": self.token,
                    "access_token_token_expired": self.token_expired_at,
                    "token_type": "Bearer",
                    "expires_in": 86400,
                },
            )

        h = request.headers
        assert h["appkey"] == APP_KEY and h["appsecret"] == APP_SECRET
        assert h["custtype"] == "P"
        if self.reject_token or h["authorization"] != f"Bearer {self.token}":
            self.reject_token = False
            return self._fail("EGW00123", "기간이 만료된 token 입니다.", status=500)
        tr_id = h["tr_id"]
        if not tr_id.startswith(("FHK", "HHD")):
            assert tr_id.startswith("V") == self.paper, f"모드에 맞지 않는 TR ID: {tr_id}"

        params = dict(request.url.params)
        routes = {
            "/uapi/domestic-stock/v1/quotations/inquire-price": self._kr_price,
            "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice": self._kr_daily,
            "/uapi/domestic-stock/v1/trading/inquire-balance": self._kr_balance,
            "/uapi/domestic-stock/v1/trading/inquire-psbl-order": self._kr_buyable,
            "/uapi/domestic-stock/v1/trading/order-cash": self._kr_order,
            "/uapi/domestic-stock/v1/trading/inquire-daily-ccld": self._kr_ccld,
            "/uapi/overseas-price/v1/quotations/price": self._us_price,
            "/uapi/overseas-price/v1/quotations/dailyprice": self._us_daily,
            "/uapi/overseas-stock/v1/trading/inquire-balance": self._us_balance,
            "/uapi/overseas-stock/v1/trading/inquire-psamount": self._us_buyable,
            "/uapi/overseas-stock/v1/trading/order": self._us_order,
            "/uapi/overseas-stock/v1/trading/inquire-ccnl": self._us_ccnl,
        }
        body = json.loads(request.content) if request.content else {}
        return routes[path](tr_id, params, body, h.get("tr_cont", ""))

    # ---- 국내 ----

    def _kr_price(self, tr_id, params, body, tr_cont):
        assert tr_id == "FHKST01010100" and params["FID_COND_MRKT_DIV_CODE"] == "J"
        return self._ok({"output": {"stck_prpr": self.kr_prices[params["FID_INPUT_ISCD"]]}})

    def _kr_daily(self, tr_id, params, body, tr_cont):
        assert tr_id == "FHKST03010100"
        assert (params["FID_PERIOD_DIV_CODE"], params["FID_ORG_ADJ_PRC"]) == ("D", "0")
        start, end = params["FID_INPUT_DATE_1"], params["FID_INPUT_DATE_2"]
        rows = [
            r
            for r in self.kr_bars.get(params["FID_INPUT_ISCD"], [])
            if start <= r["stck_bsop_date"] <= end
        ]
        return self._ok({"output1": {"stck_prpr": "0"}, "output2": rows[:100]})

    def _kr_balance(self, tr_id, params, body, tr_cont):
        assert tr_id.endswith("TTC8434R") and params["INQR_DVSN"] == "02"
        return self._page(
            self.kr_holdings, params, "CTX_AREA_NK100", "output1", {"output2": [self.kr_summary]}
        )

    def _kr_buyable(self, tr_id, params, body, tr_cont):
        assert tr_id.endswith("TTC8908R") and params["ORD_DVSN"] == "01"
        return self._ok({"output": {"nrcvb_buy_amt": self.kr_buyable, "ord_psbl_cash": "1"}})

    def _kr_order(self, tr_id, params, body, tr_cont):
        assert set(body) == {
            "CANO", "ACNT_PRDT_CD", "PDNO", "ORD_DVSN", "ORD_QTY", "ORD_UNPR",
            "EXCG_ID_DVSN_CD", "SLL_TYPE", "CNDT_PRIC",
        }  # fmt: skip
        if self.reject_orders_with:
            return self._fail("APBK0013", self.reject_orders_with)
        odno = self._odno()
        self.kr_orders.append(
            {
                "ord_dt": self.order_date,
                "ord_tmd": "084000",
                "odno": odno,
                "orgn_odno": "0000000000",  # 모의투자 실제 응답처럼 새 주문도 0으로 채움
                "pdno": body["PDNO"],
                "sll_buy_dvsn_cd": "02" if tr_id.endswith("0012U") else "01",
                "ord_qty": body["ORD_QTY"],
                "ord_unpr": body["ORD_UNPR"],
                "ord_dvsn_cd": body["ORD_DVSN"],
                "tot_ccld_qty": "0",
                "avg_prvs": "0",
                "rmn_qty": body["ORD_QTY"],
                "rjct_qty": "0",
                "cncl_yn": "",
                "cnc_cfrm_qty": "0",
            }
        )
        output = {"KRX_FWDG_ORD_ORGNO": "91252", "ODNO": odno, "ORD_TMD": "084000"}
        return self._ok({"output": output}, msg="모의투자 매수주문이 완료 되었습니다.")

    def _kr_ccld(self, tr_id, params, body, tr_cont):
        assert tr_id.endswith("TTC0081R")
        day = params["INQR_STRT_DT"]
        assert params["INQR_END_DT"] == day
        rows = [r for r in self.kr_orders if r["ord_dt"] == day]
        return self._page(rows, params, "CTX_AREA_NK100", "output1", {"output2": {}})

    # ---- 미국 ----

    def _us_price(self, tr_id, params, body, tr_cont):
        assert tr_id == "HHDFS00000300" and params["EXCD"] in ("NAS", "NYS", "AMS")
        return self._ok({"output": self.us_prices[params["SYMB"]]})

    def _us_daily(self, tr_id, params, body, tr_cont):
        assert tr_id == "HHDFS76240000" and (params["GUBN"], params["MODP"]) == ("0", "1")
        rows = self.us_bars.get(params["SYMB"], [])
        if params["BYMD"]:
            rows = [r for r in rows if r["xymd"] <= params["BYMD"]]
        return self._ok({"output1": {"zdiv": "4"}, "output2": rows[:100]})

    def _us_balance(self, tr_id, params, body, tr_cont):
        assert tr_id.endswith("TTS3012R") and params["TR_CRCY_CD"] == "USD"
        rows = self.us_holdings.get(params["OVRS_EXCG_CD"], [])
        return self._page(rows, params, "CTX_AREA_NK200", "output1", {"output2": {}})

    def _us_buyable(self, tr_id, params, body, tr_cont):
        assert tr_id.endswith("TTS3007R") and params["ITEM_CD"] and params["OVRS_ORD_UNPR"]
        return self._ok({"output": {"ord_psbl_frcr_amt": self.us_buyable, "tr_crcy_cd": "USD"}})

    def _us_order(self, tr_id, params, body, tr_cont):
        assert body["ORD_DVSN"] == "00" and body["ORD_SVR_DVSN_CD"] == "0"
        if self.reject_orders_with:
            return self._fail("APBK0013", self.reject_orders_with)
        odno = self._odno()
        side = "02" if tr_id.endswith("1002U") else "01"
        self.us_orders.append(
            {
                "ord_dt": self.order_date,
                "ord_tmd": "094000",
                "dmst_ord_dt": self.order_date,
                "thco_ord_tmd": "224000",
                "odno": odno,
                "orgn_odno": "",
                "pdno": body["PDNO"],
                "sll_buy_dvsn_cd": side,
                "ft_ord_qty": body["ORD_QTY"],
                "ft_ord_unpr3": body["OVRS_ORD_UNPR"],
                "ft_ccld_qty": "0",
                "ft_ccld_unpr3": "0",
                "nccs_qty": body["ORD_QTY"],
                "prcs_stat_name": "",
                "rjct_rson": "",
                "ovrs_excg_cd": body["OVRS_EXCG_CD"],
            }
        )
        return self._ok({"output": {"ODNO": odno, "ORD_TMD": "224000"}})

    def _us_ccnl(self, tr_id, params, body, tr_cont):
        assert tr_id.endswith("TTS3035R")
        if self.paper:  # 모의투자는 전체 조회만 된다
            assert params["PDNO"] == "" and params["OVRS_EXCG_CD"] == ""
        rows = [r for r in self.us_orders if r["ord_dt"] == params["ORD_STRT_DT"]]
        return self._page(rows, params, "CTX_AREA_NK200", "output", {})

    # ---- 응답 도움 ----

    def _odno(self) -> str:
        odno = f"{self._next_odno:010d}"
        self._next_odno += 1
        return odno

    def _ok(
        self, extra: dict, *, tr_cont: str = "D", msg: str = "정상처리 되었습니다."
    ) -> httpx.Response:
        body = {"rt_cd": "0", "msg_cd": "MCA00000", "msg1": msg} | extra
        return httpx.Response(200, json=body, headers={"tr_cont": tr_cont})

    def _fail(self, code: str, message: str, *, status: int = 200) -> httpx.Response:
        return httpx.Response(status, json={"rt_cd": "1", "msg_cd": code, "msg1": message})

    def _page(self, rows, params, nk_key, list_key, extra) -> httpx.Response:
        """page_size건씩 잘라 주고, 남았으면 tr_cont=M과 다음 위치(ctx_area_nk)를 알려준다."""
        offset = int(params.get(nk_key) or 0)
        chunk = rows[offset : offset + self.page_size]
        more = offset + self.page_size < len(rows)
        body = {
            list_key: chunk,
            nk_key.lower(): str(offset + self.page_size) if more else "",
            nk_key.lower().replace("nk", "fk"): "fk" if more else "",
        } | extra
        return self._ok(body, tr_cont="M" if more else "D")
