import pytest
from pydantic import ValidationError

from trading.config import Settings


def make(**kwargs) -> Settings:
    return Settings(_env_file=None, **kwargs)


def test_default_is_paper_and_fake():
    s = make()
    assert s.trading_mode == "paper"
    assert s.broker == "fake"
    assert not s.is_live


def test_env_overrides(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BROKER", "kis")
    monkeypatch.setenv("KIS_APP_KEY", "paper-key-123")
    s = make()
    assert s.broker == "kis"
    assert s.kis_app_key is not None
    assert s.kis_app_key.get_secret_value() == "paper-key-123"


def test_live_refused_outside_github_actions():
    with pytest.raises(ValidationError, match="GitHub Actions"):
        make(trading_mode="live", broker="kis")


def test_live_refused_with_fake_broker():
    with pytest.raises(ValidationError, match="fake"):
        make(trading_mode="live", broker="fake", github_actions=True)


def test_live_allowed_in_github_actions(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    s = make(trading_mode="live", broker="kis")
    assert s.is_live


def test_unknown_mode_rejected():
    with pytest.raises(ValidationError):
        make(trading_mode="real")


def test_secrets_hidden_in_repr_and_listed_for_redaction():
    s = make(kis_app_key="abcd-secret", vapid_private_key="vapid-key-999")
    assert "abcd-secret" not in repr(s)
    assert "vapid-key-999" not in str(s)
    assert set(s.secret_values()) == {"abcd-secret", "vapid-key-999"}


def test_empty_env_values_are_ignored(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KIS_APP_KEY", "")
    monkeypatch.setenv("MAX_DAILY_BUY_KRW", "")
    s = make()
    assert s.kis_app_key is None
    assert s.max_daily_buy_krw is None


def test_daily_buy_caps(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MAX_DAILY_BUY_KRW", "1000000")
    assert make().max_daily_buy_krw == 1_000_000
    monkeypatch.setenv("MAX_DAILY_BUY_KRW", "-5")
    with pytest.raises(ValidationError):
        make()
