"""실행 요약: 이미 정해진 실행 결과를 LLM이 2~3줄 한국어로 풀어 쓴다.

입력은 알림 본문과 같은 결과 문장뿐이다(계좌번호, 잔고, 비밀값은 넣지 않는다).
LLM이 결과에 없는 숫자(가격, 수량)를 지어내면 요약을 버린다. 실패하면 None을 돌려주고
알림은 요약 없이 지금처럼 나간다.
"""

import logging
import re

from trading.engine.report import RunReport, RunStatus
from trading.llm.base import LLMClient, LLMError

logger = logging.getLogger(__name__)

MAX_SUMMARY_CHARS = 300
MAX_LINES = 3

SYSTEM = """\
당신은 주식 자동 매매 시스템의 실행 결과를 폰 알림 맨 앞에 붙일 요약으로 바꿉니다.
- 한국어 2~3줄, 줄마다 한 문장. 300자 이내. 머리말, 목록 기호, 마크다운 없이 문장만 씁니다.
- 무엇을 사고팔았는지(또는 주문하지 않았는지)와 그 이유를 결과에 적힌 근거로 설명합니다.
- '확인 필요'나 '악재 판정' 항목이 있으면 꼭 언급합니다.
- 결과에 없는 사실, 숫자, 전망, 투자 조언은 쓰지 않습니다.
- 가격과 수량은 결과에 적힌 그대로만 씁니다."""

# 세 자리 이상 숫자(가격, 수량, 날짜). 결과 문장에 그대로 있어야 한다.
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def wants_summary(report: RunReport) -> bool:
    """요약할 만한 일이 있었는지. 오류, 건너뜀, 아무 일 없는 날은 지금 알림으로 충분하다."""
    if report.status not in (RunStatus.OK, RunStatus.PAUSED):
        return False
    return bool(report.orders or report.planned or report.fills or report.skipped or report.screens)


def summarize(llm: LLMClient, title: str, body: str) -> str | None:
    """알림 제목과 본문으로 요약 문장을 만든다. 실패하거나 믿을 수 없으면 None."""
    prompt = f"<실행결과>\n{title}\n{body}\n</실행결과>"
    try:
        answer = llm.complete(system=SYSTEM, prompt=prompt, max_tokens=2000)
    except LLMError as e:
        logger.warning("실행 요약 실패: %s", e)
        return None
    lines = [" ".join(line.split()) for line in answer.strip().splitlines()]
    lines = [line.lstrip("-•· ") for line in lines if line]
    text = "\n".join(lines[:MAX_LINES])[:MAX_SUMMARY_CHARS].strip()
    if not text:
        logger.warning("실행 요약이 비어 있습니다")
        return None
    if invented := _invented_numbers(text, prompt):
        logger.warning("실행 요약에 결과에 없는 숫자가 있어 버립니다: %s", invented)
        return None
    return text


def _invented_numbers(summary: str, source: str) -> list[str]:
    known = {n.replace(",", "") for n in _NUMBER.findall(source)}
    found = [n.replace(",", "") for n in _NUMBER.findall(summary)]
    return [n for n in found if len(n.split(".")[0]) >= 3 and n not in known]
