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


def test_cli_refuses_kis_until_m5(monkeypatch, capsys):
    monkeypatch.setenv("BROKER", "kis")
    assert main(["--market", "KR"]) == 2
    assert "M5" in capsys.readouterr().out


def test_ignore_hours_only_with_fake(monkeypatch, capsys):
    monkeypatch.setenv("BROKER", "kis")
    assert main(["--market", "KR", "--ignore-hours"]) == 2
    assert "fake" in capsys.readouterr().out
