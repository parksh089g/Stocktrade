"""GitHub Actions 워크플로 파일 확인: 예약 시각이 장 시간에 맞는지, 실전 전환 조건이 안전한지."""

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from trading.core.models import Market

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
CRON = re.compile(r'cron: "(\d+) (\d+) \* \* (\d)-(\d)"')
# 겨울(표준시)과 여름(서머타임) 한 주씩. 둘 다 일요일(UTC)부터 시작한다.
WEEKS = [datetime(2026, 1, 11, tzinfo=UTC), datetime(2026, 7, 12, tzinfo=UTC)]


def read(name: str) -> str:
    return (WORKFLOWS / name).read_text()


def run_times(text: str, week: datetime) -> list[datetime]:
    """그 주에 예약 실행이 도는 UTC 시각들."""
    times = []
    for minute, hour, first, last in CRON.findall(text):
        for dow in range(int(first), int(last) + 1):  # 0=일요일
            day = week + timedelta(days=dow)
            times.append(day.replace(hour=int(hour), minute=int(minute)))
    return times


@pytest.mark.parametrize(
    ("name", "market"), [("trade-kr.yml", Market.KR), ("trade-us.yml", Market.US)]
)
@pytest.mark.parametrize("week", WEEKS)
def test_schedule_runs_once_each_weekday_inside_order_window(name, market, week):
    times = run_times(read(name), week)
    local = [t.astimezone(market.timezone) for t in times]
    assert sorted(t.weekday() for t in local) == [0, 1, 2, 3, 4]  # 현지 월~금 하루 한 번
    for t in local:
        assert t.time() >= market.order_start_time
        # GitHub 예약 실행이 늦게 시작해도 장 마감 전에 돌 수 있게 여유를 둔다
        close = datetime.combine(t.date(), market.close_time, tzinfo=t.tzinfo)
        assert close - t >= timedelta(hours=5)


@pytest.mark.parametrize(
    ("name", "market"), [("stoploss-kr.yml", Market.KR), ("stoploss-us.yml", Market.US)]
)
@pytest.mark.parametrize("week", WEEKS)
def test_stop_loss_check_runs_well_after_morning_and_before_close(name, market, week):
    local = [t.astimezone(market.timezone) for t in run_times(read(name), week)]
    assert sorted(t.weekday() for t in local) == [0, 1, 2, 3, 4]
    for t in local:
        start = datetime.combine(t.date(), market.order_start_time, tzinfo=t.tzinfo)
        close = datetime.combine(t.date(), market.close_time, tzinfo=t.tzinfo)
        assert t - start >= timedelta(hours=3)  # 아침 실행 뒤 장중 가격을 본다
        assert close - t >= timedelta(hours=2)  # 예약 실행이 늦어도 마감 전에 돌 여유


@pytest.mark.parametrize(
    ("name", "market", "stop_loss_only"),
    [
        ("trade-kr.yml", "KR", False),
        ("trade-us.yml", "US", False),
        ("stoploss-kr.yml", "KR", True),
        ("stoploss-us.yml", "US", True),
    ],
)
def test_workflows_run_engine_for_their_market(name, market, stop_loss_only):
    text = read(name)
    assert "uses: ./.github/workflows/engine.yml" in text
    assert f"market: {market}" in text
    assert "vars.SCHEDULE_ENABLED == 'true'" in text  # 준비 전에는 예약 실행이 돌지 않는다
    assert "default: true" in text  # 손으로 실행은 기본이 시험 실행
    assert "group: trade\n" in text
    assert ("stop_loss_only: true" in text) is stop_loss_only


def test_engine_passes_stop_loss_only_to_both_modes():
    text = read("engine.yml")
    assert "STOP_LOSS_ARGS: ${{ inputs.stop_loss_only && '--stop-loss-only' || '' }}" in text
    assert text.count("$DRY_RUN_ARGS $STOP_LOSS_ARGS\n") == 2


def steps(text: str) -> list[str]:
    return re.split(r"\n      - ", text.split("    steps:", 1)[1])


def test_live_only_when_variable_says_live_and_on_main():
    found = {}
    for step in steps(read("engine.yml")):
        mode = re.search(r"TRADING_MODE: (\w+)", step)
        if mode:
            found[mode.group(1)] = step
    assert set(found) == {"paper", "live"}
    # 변수가 없거나 live가 아니면 모의투자
    assert "if: vars.TRADING_MODE != 'live'\n" in found["paper"]
    assert "secrets.KIS_PAPER_APP_KEY" in found["paper"]
    assert "KIS_LIVE" not in found["paper"]
    assert "if: vars.TRADING_MODE == 'live' && github.ref == 'refs/heads/main'\n" in found["live"]
    assert "secrets.KIS_LIVE_APP_KEY" in found["live"]
    assert "KIS_PAPER" not in found["live"]


def test_settings_step_shows_variables_but_no_secrets():
    shown = [s for s in steps(read("engine.yml")) if s.startswith("name: 설정 표시")]
    assert len(shown) == 1
    assert "vars.SCHEDULE_ENABLED" in shown[0]
    assert "vars.TRADING_MODE" in shown[0]
    assert "secrets." not in shown[0]


def test_workflows_are_read_only_and_pin_actions():
    for path in WORKFLOWS.glob("*.yml"):
        text = path.read_text()
        assert "permissions:\n  contents: read\n" in text, path.name
        assert "write" not in re.sub(r"#.*", "", text), path.name
        for action in re.findall(r"uses: ([\w./-]+@\S+)", text):
            assert re.fullmatch(r"[\w-]+/[\w-]+@[0-9a-f]{40}", action), action
