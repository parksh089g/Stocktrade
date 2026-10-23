from pathlib import Path

import pytest

from trading.engine.__main__ import main

UNIVERSE = Path("config/universe.toml").resolve()


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    # 로컬 .env를 읽지 않도록 빈 폴더에서 실행한다
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("UNIVERSE_PATH", str(UNIVERSE))


@pytest.mark.parametrize("market", ["KR", "US"])
def test_cli_runs_with_fake_broker(market, capsys):
    assert main(["--market", market, "--ignore-hours", "--seed", "11"]) == 0
    out = capsys.readouterr().out
    assert "[모의투자]" in out
    assert "· 완료" in out
    assert "주문 1건" in out


def test_cli_needs_kis_settings(monkeypatch, capsys):
    monkeypatch.setenv("BROKER", "kis")
    assert main(["--market", "KR"]) == 2
    assert "KIS_APP_KEY" in capsys.readouterr().out


def test_ignore_hours_only_with_fake_or_dry_run(monkeypatch, capsys):
    monkeypatch.setenv("BROKER", "kis")
    assert main(["--market", "KR", "--ignore-hours"]) == 2
    assert "--dry-run" in capsys.readouterr().out
    # 시험 실행이면 장 시간 검사를 끌 수 있다(여기서는 KIS 설정이 없어 설정 오류로 끝난다)
    assert main(["--market", "KR", "--ignore-hours", "--dry-run"]) == 2
    assert "KIS_APP_KEY" in capsys.readouterr().out
