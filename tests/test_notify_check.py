"""웹 푸시 알림 확인 도구(trading.notify.check). 가짜 Supabase와 가짜 발송으로 시험한다."""

import json

import pytest

from tests.fake_postgrest import FakePostgrest
from trading.notify import check, vapid


class Sent:
    """pywebpush.webpush 대신 보낸 내용을 모은다."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)


@pytest.fixture
def fake(monkeypatch, tmp_path) -> FakePostgrest:
    monkeypatch.chdir(tmp_path)  # 이 PC의 .env를 읽지 않게
    fake = FakePostgrest()
    real = check.SupabaseStore
    monkeypatch.setattr(
        check,
        "SupabaseStore",
        lambda url, key, mode: real(url, key, mode, transport=fake.transport),
    )
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", fake.key)
    monkeypatch.setenv("VAPID_PRIVATE_KEY", vapid.generate_private_key())
    monkeypatch.setenv("VAPID_SUBJECT", "mailto:me@example.com")
    return fake


def add_device(fake: FakePostgrest, endpoint: str = "https://push.example/1") -> None:
    fake.tables["push_subscriptions"].append({"endpoint": endpoint, "p256dh": "p", "auth": "a"})


def test_sends_test_notification_to_devices(fake, capsys):
    add_device(fake)
    sent = Sent()
    assert check.main(send=sent) == 0
    [call] = sent.calls
    assert call["subscription_info"]["endpoint"] == "https://push.example/1"
    payload = json.loads(call["data"])
    assert payload["title"] == "[시험] 푸시 알림 확인"
    assert payload["url"] == "/"
    out = capsys.readouterr().out
    assert "알림을 켠 기기: 1대" in out
    assert "보냈습니다" in out
    assert fake.tables["runs"] == []  # 실행 기록은 남기지 않는다


def test_no_device_is_a_failure(fake, capsys):
    assert check.main(send=Sent()) == 1
    assert "웹앱에서 알림을 켜 주세요" in capsys.readouterr().out


def test_needs_settings(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    assert check.main(send=Sent()) == 2
    out = capsys.readouterr().out
    assert "SUPABASE_URL" in out
    assert "VAPID_PRIVATE_KEY" in out


def test_default_subject_gets_a_hint(fake, monkeypatch, capsys):
    monkeypatch.delenv("VAPID_SUBJECT")
    add_device(fake)
    assert check.main(send=Sent()) == 0
    assert "VAPID_SUBJECT가 기본값" in capsys.readouterr().out


def test_private_key_is_never_printed(fake, monkeypatch, capsys):
    key = vapid.generate_private_key()
    monkeypatch.setenv("VAPID_PRIVATE_KEY", key)
    add_device(fake)
    check.main(send=Sent())
    assert key not in capsys.readouterr().out
