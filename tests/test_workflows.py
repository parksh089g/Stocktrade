"""GitHub Actions 워크플로 파일 확인: 예약 시각이 장 시간에 맞는지, 실전 전환 조건이 안전한지."""

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from trading.core.models import Market

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
SCHEDULER = ROOT / "scheduler"
SCHEDULED_WORKFLOWS = ["trade-kr.yml", "trade-us.yml", "stoploss-kr.yml", "stoploss-us.yml"]
CRON = re.compile(r'cron: "(\d+) (\d+) \* \* (\d)-(\d)"')
# 겨울(표준시)과 여름(서머타임) 한 주씩. 둘 다 일요일(UTC)부터 시작한다.
WEEKS = [datetime(2026, 1, 11, tzinfo=UTC), datetime(2026, 7, 12, tzinfo=UTC)]


def read(name: str) -> str:
    return (WORKFLOWS / name).read_text()


def cron_times(text: str, week: datetime) -> list[list[datetime]]:
    """예약(cron 줄)마다 그 주에 도는 UTC 시각들."""
    result = []
    for minute, hour, first, last in CRON.findall(text):
        times = []
        for dow in range(int(first), int(last) + 1):  # 0=일요일
            day = week + timedelta(days=dow)
            times.append(day.replace(hour=int(hour), minute=int(minute)))
        result.append(times)
    return result


def run_times(text: str, week: datetime) -> list[datetime]:
    """그 주에 예약 실행이 도는 UTC 시각들."""
    return [t for times in cron_times(text, week) for t in times]


def close_of(market: Market, t: datetime) -> datetime:
    return datetime.combine(t.date(), market.close_time, tzinfo=t.tzinfo)


@pytest.mark.parametrize(
    ("name", "market"), [("trade-kr.yml", Market.KR), ("trade-us.yml", Market.US)]
)
@pytest.mark.parametrize("week", WEEKS)
def test_schedule_runs_each_weekday_with_backup_inside_order_window(name, market, week):
    primary, backup = (
        sorted(t.astimezone(market.timezone) for t in times)
        for times in cron_times(read(name), week)
    )
    for times in (primary, backup):
        assert [t.weekday() for t in times] == [0, 1, 2, 3, 4]  # 현지 월~금 하루 한 번
        assert all(t.time() >= market.order_start_time for t in times)
    for p, b in zip(primary, backup, strict=True):
        # GitHub 예약 실행이 늦게 시작해도 장 마감 전에 돌 수 있게 여유를 둔다
        assert close_of(market, p) - p >= timedelta(hours=5)
        # 백업은 같은 혼잡에 같이 걸리지 않게 한참 뒤에, 그래도 마감까지 여유 있게
        assert timedelta(minutes=45) <= b - p <= timedelta(hours=2)
        assert close_of(market, b) - b >= timedelta(hours=3)


@pytest.mark.parametrize(
    ("name", "market"), [("stoploss-kr.yml", Market.KR), ("stoploss-us.yml", Market.US)]
)
@pytest.mark.parametrize("week", WEEKS)
def test_stop_loss_check_runs_well_after_morning_and_before_close(name, market, week):
    local = [t.astimezone(market.timezone) for t in run_times(read(name), week)]
    assert sorted(t.weekday() for t in local) == [0, 1, 2, 3, 4]
    for t in local:
        start = datetime.combine(t.date(), market.order_start_time, tzinfo=t.tzinfo)
        assert t - start >= timedelta(hours=3)  # 아침 실행 뒤 장중 가격을 본다
        assert close_of(market, t) - t >= timedelta(hours=2)  # 예약 실행이 늦어도 마감 전에 돌 여유


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
    # 준비 전에는 예약 실행(GitHub 예약, Cloudflare 예약)이 돌지 않는다
    gate = (
        "if: (github.event_name == 'workflow_dispatch' && inputs.scheduled != true)"
        " || vars.SCHEDULE_ENABLED == 'true'\n"
    )
    assert gate in text
    assert "scheduled: ${{ github.event_name == 'schedule' || inputs.scheduled == true }}" in text
    assert "default: true" in text  # 손으로 실행은 기본이 시험 실행
    assert "group: trade\n" in text
    assert ("stop_loss_only: true" in text) is stop_loss_only


def test_engine_passes_run_options_to_both_modes():
    text = read("engine.yml")
    assert "STOP_LOSS_ARGS: ${{ inputs.stop_loss_only && '--stop-loss-only' || '' }}" in text
    # 하루 한 번은 예약 실행에만. 손으로 실행은 언제나 돈다.
    assert "ONCE_ARGS: ${{ inputs.scheduled && '--once-per-day' || '' }}" in text
    assert text.count("$DRY_RUN_ARGS $STOP_LOSS_ARGS $ONCE_ARGS\n") == 2


# ---- Cloudflare 예약(scheduler/) ----


def test_cloudflare_schedule_matches_github_schedule():
    worker = (SCHEDULER / "worker.js").read_text()
    cloudflare = set(re.findall(r'\["([\w-]+\.yml)", "([^"]+)"\]', worker))
    github = {
        (name, cron)
        for name in SCHEDULED_WORKFLOWS
        for cron in re.findall(r'cron: "([^"]+)"', read(name))
    }
    assert len(github) == 6
    assert cloudflare == github


def test_cloudflare_wakes_up_exactly_at_scheduled_minutes():
    worker = (SCHEDULER / "worker.js").read_text()
    minutes = {int(cron.split()[0]) for cron in re.findall(r'\["[\w-]+\.yml", "([^"]+)"\]', worker)}
    [trigger] = re.findall(r'crons = \["([^"]+)"\]', (SCHEDULER / "wrangler.toml").read_text())
    minute, *rest = trigger.split()
    assert rest == ["*", "*", "*", "*"]
    assert {int(m) for m in minute.split(",")} == minutes


@pytest.mark.parametrize("name", SCHEDULED_WORKFLOWS)
def test_cloudflare_sends_inputs_the_workflow_accepts(name):
    worker = (SCHEDULER / "worker.js").read_text()
    assert 'const INPUTS = { scheduled: "true", dry_run: "false" };' in worker
    assert 'ref: "main"' in worker  # 실전은 main 브랜치에서만 돈다
    dispatch = read(name).split("  workflow_dispatch:\n", 1)[1].split("\npermissions:", 1)[0]
    # 워크플로에 없는 입력값을 보내면 GitHub이 실행 요청을 거부한다
    assert set(re.findall(r"^      (\w+):$", dispatch, re.M)) == {"scheduled", "dry_run"}


def test_cloudflare_worker_has_no_web_address_or_secret_in_config():
    config = (SCHEDULER / "wrangler.toml").read_text()
    assert "workers_dev = false" in config
    assert "GITHUB_TOKEN" not in re.sub(r"#.*", "", config)


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


# ---- 표 구조 자동 적용(db-schema.yml) ----


def test_db_schema_runs_only_on_main_of_this_repo():
    text = read("db-schema.yml")
    trigger = text.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
    # PR(포크 포함)에서는 돌지 않는다: 데이터베이스 비밀번호가 든 Secret을 쓰기 때문
    assert "pull_request" not in trigger
    assert "    branches: [main]\n    paths: [supabase/schema.sql]\n" in trigger
    assert "workflow_dispatch" in trigger
    assert (
        "if: github.repository == 'parksh089g/Stocktrade' && github.ref == 'refs/heads/main'\n"
        in text
    )
    # 접속 주소는 환경변수로만 넘기고 명령줄이나 echo로 찍지 않는다
    assert text.count("secrets.SUPABASE_DB_URL") == 1
    assert 'psql "$SUPABASE_DB_URL"' in text
    assert "--single-transaction" in text and "ON_ERROR_STOP=1" in text
    assert 'echo "$SUPABASE_DB_URL' not in text


def test_no_workflow_runs_untrusted_pr_code_with_secrets():
    for path in WORKFLOWS.glob("*.yml"):
        assert "pull_request_target" not in path.read_text(), path.name


# ---- 웹앱 자동 배포(webapp-deploy.yml) ----


def test_webapp_deploy_runs_only_on_main_of_this_repo():
    text = read("webapp-deploy.yml")
    trigger = text.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
    # PR(포크 포함)에서는 돌지 않는다: Cloudflare 토큰 Secret을 쓰기 때문
    assert "pull_request" not in trigger
    assert "    branches: [main]\n" in trigger and "      - webapp/**\n" in trigger
    assert "workflow_dispatch" in trigger
    assert (
        "if: github.repository == 'parksh089g/Stocktrade' && github.ref == 'refs/heads/main'\n"
        in text
    )


def test_webapp_deploy_builds_only_with_public_values():
    text = read("webapp-deploy.yml")
    # 웹앱 빌드에 들어가는 값은 공개용 Variables뿐이고, Secret은 Cloudflare 토큰 하나다
    for line in re.findall(r"VITE_\w+: \$\{\{ (.+?) \}\}", text):
        assert line.startswith("vars."), line
    assert sorted(set(re.findall(r"secrets\.\w+", text))) == [
        "secrets.CLOUDFLARE_API_TOKEN",
        "secrets.SUPABASE_URL",
        "secrets.VAPID_PRIVATE_KEY",
    ]
    assert "sb_publishable_*" in text
    for name in ("CLOUDFLARE_API_TOKEN", "ENGINE_VAPID_PRIVATE_KEY"):
        assert f'echo "${name}' not in text
        assert f"{name}=" not in text  # GITHUB_ENV로 넘기지 않는다
    # 개인키는 공개키를 계산하는 데만 쓰고 웹앱 빌드 값으로 넘기지 않는다
    assert "VITE_VAPID_PUBLIC_KEY=$public" in text


def test_webapp_deploy_derives_matching_vapid_public_key():
    """워크플로의 node 계산이 엔진(trading.notify.vapid)과 같은 공개키를 내는지."""
    import os
    import shutil
    import subprocess

    from trading.notify.vapid import generate_private_key, public_key_from_private

    node = shutil.which("node")
    if node is None:
        pytest.skip("node 없음")
    script = re.search(r"node -e '(.+?)'\)", read("webapp-deploy.yml")).group(1)
    private = generate_private_key()
    env = {**os.environ, "ENGINE_VAPID_PRIVATE_KEY": private}
    out = subprocess.run([node, "-e", script], env=env, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == public_key_from_private(private)
