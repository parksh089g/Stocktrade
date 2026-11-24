"""국내 공시: 금융감독원 OpenDART(https://opendart.fss.or.kr). 무료 API 키가 필요하다.

공시 검색(list.json)은 종목코드가 아니라 DART 고유번호(corp_code)로 찾는다.
고유번호 표(corpCode.xml, zip)는 처음 필요할 때 한 번 받아 실행 동안만 쓴다.
API 키는 주소의 crtfc_key에 들어가므로 오류 문장에 주소를 넣지 않는다.
"""

import io
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime

import httpx

from trading.core.models import Instrument
from trading.llm.models import Disclosure
from trading.llm.screen import DisclosureSource

BASE_URL = "https://opendart.fss.or.kr/api"
VIEW_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={}"
_NO_DATA = "013"  # 조회된 데이터가 없습니다


class DartError(Exception):
    pass


class DartSource(DisclosureSource):
    def __init__(self, api_key: str, *, transport: httpx.BaseTransport | None = None) -> None:
        self._key = api_key
        self._client = httpx.Client(base_url=BASE_URL, timeout=30, transport=transport)
        self._corp_codes: dict[str, str] | None = None

    def recent(self, instrument: Instrument, since: date, until: date) -> list[Disclosure]:
        corp_code = self._corp_code(instrument.symbol)
        data = self._get_json(
            "/list.json",
            {
                "corp_code": corp_code,
                "bgn_de": since.strftime("%Y%m%d"),
                "end_de": until.strftime("%Y%m%d"),
                "page_count": "100",
            },
        )
        status = data.get("status")
        if status == _NO_DATA:
            return []
        if status != "000":
            raise DartError(f"DART 공시 검색 실패: {status} {data.get('message', '')}")
        items = [
            Disclosure(
                title=_title(row),
                filed=datetime.strptime(row["rcept_dt"], "%Y%m%d").date(),
                source="DART",
                url=VIEW_URL.format(row["rcept_no"]),
            )
            for row in data.get("list", [])
        ]
        return sorted(items, key=lambda d: d.filed, reverse=True)

    def _corp_code(self, symbol: str) -> str:
        if self._corp_codes is None:
            self._corp_codes = self._load_corp_codes()
        try:
            return self._corp_codes[symbol]
        except KeyError:
            raise DartError(f"DART 고유번호를 찾지 못했습니다: {symbol}") from None

    def _load_corp_codes(self) -> dict[str, str]:
        resp = self._get("/corpCode.xml")
        try:
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                root = ET.fromstring(zf.read(zf.namelist()[0]))
        except (zipfile.BadZipFile, ET.ParseError, IndexError) as e:
            # 키가 틀리면 zip 대신 오류 XML/JSON이 온다
            raise DartError(f"DART 고유번호 표를 읽지 못했습니다({type(e).__name__})") from None
        codes = {}
        for item in root.iter("list"):
            stock = (item.findtext("stock_code") or "").strip()
            if stock:
                codes[stock] = (item.findtext("corp_code") or "").strip()
        return codes

    def _get_json(self, path: str, params: dict[str, str]) -> dict:
        try:
            return self._get(path, params).json()
        except ValueError:
            raise DartError("DART 응답이 JSON이 아닙니다") from None

    def _get(self, path: str, params: dict[str, str] | None = None) -> httpx.Response:
        try:
            resp = self._client.get(path, params={"crtfc_key": self._key, **(params or {})})
        except httpx.HTTPError as e:
            raise DartError(f"DART 연결 실패({type(e).__name__})") from None
        if resp.status_code != 200:
            raise DartError(f"DART 응답 {resp.status_code}")
        return resp


def _title(row: dict) -> str:
    title = " ".join(str(row.get("report_nm", "")).split())
    filer = str(row.get("flr_nm", "")).strip()
    note = str(row.get("rm", "")).strip()  # 유: 유가증권시장본부 소관, 정: 정정 등 비고
    extra = ", ".join(
        x for x in (f"제출인 {filer}" if filer else "", f"비고 {note}" if note else "") if x
    )
    return f"{title} ({extra})" if extra else title
