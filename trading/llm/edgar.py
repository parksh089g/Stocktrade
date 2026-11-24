"""미국 공시: SEC EDGAR(https://www.sec.gov). 키는 필요 없지만 연락처가 든 User-Agent를 요구한다.

티커 → CIK 표(company_tickers.json)는 처음 필요할 때 한 번 받는다. 종목별 최근 제출 목록
(submissions/CIK##########.json)에서 기간 안의 것만 쓴다. 8-K는 항목 번호를 뜻과 함께 넘긴다.
SEC 요청 한도(초당 10회)보다 훨씬 적게 부른다.
"""

from datetime import date

import httpx

from trading.core.models import Instrument
from trading.llm.models import Disclosure
from trading.llm.screen import DisclosureSource

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{:010d}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{}/{}/{}"

# 8-K 항목 중 판정에 중요한 것. 나머지는 번호만 넘긴다.
ITEMS_8K = {
    "1.01": "중요 계약 체결",
    "1.02": "중요 계약 해지",
    "1.03": "파산·회생 절차",
    "2.02": "실적 발표",
    "2.04": "채무 조기상환 사유 발생",
    "2.05": "구조조정 비용",
    "2.06": "중요 손상차손",
    "3.01": "상장 유지 기준 미달·상장폐지 통지",
    "3.02": "미등록 증권 발행",
    "4.01": "감사인 변경",
    "4.02": "과거 재무제표 신뢰 불가",
    "5.02": "임원·이사 변동",
    "7.01": "공정공시(Reg FD)",
    "8.01": "기타 사건",
    "9.01": "재무제표·첨부",
}


class EdgarError(Exception):
    pass


class EdgarSource(DisclosureSource):
    def __init__(self, user_agent: str, *, transport: httpx.BaseTransport | None = None) -> None:
        self._client = httpx.Client(
            headers={"User-Agent": user_agent}, timeout=30, transport=transport
        )
        self._ciks: dict[str, int] | None = None

    def recent(self, instrument: Instrument, since: date, until: date) -> list[Disclosure]:
        cik = self._cik(instrument.symbol)
        recent = self._get(SUBMISSIONS_URL.format(cik)).get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        items = []
        for i, form in enumerate(forms):
            filed = date.fromisoformat(recent["filingDate"][i])
            if not since <= filed <= until:
                continue
            accession = recent["accessionNumber"][i]
            doc = _at(recent, "primaryDocument", i)
            items.append(
                Disclosure(
                    title=_title(
                        form, _at(recent, "primaryDocDescription", i), _at(recent, "items", i)
                    ),
                    filed=filed,
                    source="SEC",
                    url=ARCHIVE_URL.format(cik, accession.replace("-", ""), doc) if doc else "",
                )
            )
        return sorted(items, key=lambda d: d.filed, reverse=True)

    def _cik(self, symbol: str) -> int:
        if self._ciks is None:
            data = self._get(TICKERS_URL)
            self._ciks = {str(row["ticker"]).upper(): int(row["cik_str"]) for row in data.values()}
        try:
            return self._ciks[symbol.upper()]
        except KeyError:
            raise EdgarError(f"SEC CIK를 찾지 못했습니다: {symbol}") from None

    def _get(self, url: str) -> dict:
        try:
            resp = self._client.get(url)
        except httpx.HTTPError as e:
            raise EdgarError(f"SEC 연결 실패({type(e).__name__})") from None
        if resp.status_code != 200:
            raise EdgarError(f"SEC 응답 {resp.status_code}")
        try:
            return resp.json()
        except ValueError:
            raise EdgarError("SEC 응답이 JSON이 아닙니다") from None


def _at(recent: dict, key: str, i: int) -> str:
    values = recent.get(key) or []
    return str(values[i]) if i < len(values) and values[i] else ""


def _title(form: str, description: str, items: str) -> str:
    parts = [form]
    if description and description.upper() != form.upper():
        parts.append(description)
    codes = [c.strip() for c in items.split(",") if c.strip()]
    if codes:
        parts.append("항목 " + ", ".join(f"{c} {ITEMS_8K.get(c, '')}".strip() for c in codes))
    return " / ".join(parts)
