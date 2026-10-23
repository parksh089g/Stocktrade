import base64
import json
import os

import http_ece
import pytest
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pywebpush import WebPushException

from trading.notify import vapid
from trading.notify.base import (
    ConsoleNotifier,
    MemoryNotifier,
    MultiNotifier,
    Notification,
    Notifier,
    NotifyError,
)
from trading.notify.webpush import MAX_BODY_BYTES, WebPushNotifier, truncate_utf8
from trading.storage.base import PushSubscription
from trading.storage.memory import MemoryStore

NOTE = Notification(title="[모의투자] 국내 · 완료", body="주문 1건", url="/runs/7")


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class Response:
    def __init__(self, status: int) -> None:
        self.status_code = status
        self.reason = "x"
        self.text = ""
        self.headers = {}


def store_with(*endpoints: str) -> MemoryStore:
    store = MemoryStore()
    for e in endpoints:
        store.add_push_subscription(PushSubscription(endpoint=e, p256dh="p", auth="a"))
    return store


class Recorder:
    """pywebpush.webpush 대신 호출 내용을 기록한다. statuses로 기기별 실패를 흉내 낸다."""

    def __init__(self, statuses: dict[str, int] | None = None) -> None:
        self.calls: list[dict] = []
        self.statuses = statuses or {}

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        status = self.statuses.get(kwargs["subscription_info"]["endpoint"], 201)
        if status > 202:
            raise WebPushException("fail", response=Response(status))
        return Response(status)


def test_sends_to_every_device():
    send = Recorder()
    WebPushNotifier(
        store_with("https://p/1", "https://p/2"), "KEY", "mailto:a@b.c", send=send
    ).send(NOTE)
    assert [c["subscription_info"]["endpoint"] for c in send.calls] == [
        "https://p/1",
        "https://p/2",
    ]
    payload = json.loads(send.calls[0]["data"])
    assert payload == {"title": NOTE.title, "body": "주문 1건", "url": "/runs/7", "urgent": False}
    assert send.calls[0]["headers"] == {"Urgency": "normal"}
    assert send.calls[0]["vapid_claims"] == {"sub": "mailto:a@b.c"}
    assert send.calls[0]["vapid_claims"] is not send.calls[1]["vapid_claims"]


def test_urgent_notification_wakes_phone():
    send = Recorder()
    note = NOTE.model_copy(update={"urgent": True})
    WebPushNotifier(store_with("https://p/1"), "KEY", "mailto:a@b.c", send=send).send(note)
    assert send.calls[0]["headers"] == {"Urgency": "high"}


def test_expired_subscription_is_deleted():
    store = store_with("https://p/old", "https://p/new")
    send = Recorder({"https://p/old": 410})
    WebPushNotifier(store, "KEY", "mailto:a@b.c", send=send).send(NOTE)
    assert [s.endpoint for s in store.get_push_subscriptions()] == ["https://p/new"]


def test_fails_when_no_device_received():
    store = store_with("https://p/1")
    send = Recorder({"https://p/1": 500})
    with pytest.raises(NotifyError, match="500"):
        WebPushNotifier(store, "KEY", "mailto:a@b.c", send=send).send(NOTE)
    assert store.get_push_subscriptions()  # 일시적 오류면 구독은 남긴다


def test_fails_without_subscriptions():
    with pytest.raises(NotifyError, match="웹앱에서 알림"):
        WebPushNotifier(MemoryStore(), "KEY", "mailto:a@b.c", send=Recorder()).send(NOTE)


def test_network_error_on_one_device_is_tolerated():
    calls = []

    def send(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise requests.ConnectionError("down")

    WebPushNotifier(store_with("https://p/1", "https://p/2"), "K", "mailto:a@b.c", send=send).send(
        NOTE
    )
    assert len(calls) == 2


def test_truncate_utf8():
    assert truncate_utf8("짧은 글", 100) == "짧은 글"
    long = "가" * 2000  # 6000바이트
    cut = truncate_utf8(long, MAX_BODY_BYTES)
    assert len(cut.encode()) <= MAX_BODY_BYTES
    assert cut.endswith("웹앱에서 전체 보기)")


def test_real_pywebpush_payload_can_be_decrypted_by_phone(monkeypatch):
    """실제 pywebpush로 암호화하고, 폰(브라우저) 쪽 키로 다시 풀어 본다. 전송만 가짜로 바꾼다."""
    phone_key = ec.generate_private_key(ec.SECP256R1())
    phone_public = phone_key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    auth_secret = os.urandom(16)
    store = MemoryStore()
    store.add_push_subscription(
        PushSubscription(
            endpoint="https://fcm.googleapis.com/fcm/send/abc",
            p256dh=b64url(phone_public),
            auth=b64url(auth_secret),
        )
    )
    posted = {}

    def fake_post(url, timeout=None, data=None, headers=None, **_):
        posted.update(url=url, data=data, headers=headers)
        return Response(201)

    monkeypatch.setattr(requests, "post", fake_post)
    private_key = vapid.generate_private_key()
    note = NOTE.model_copy(update={"urgent": True})
    WebPushNotifier(store, private_key, "mailto:a@b.c").send(note)

    assert posted["url"] == "https://fcm.googleapis.com/fcm/send/abc"
    headers = posted["headers"]
    assert headers["ttl"] == str(24 * 60 * 60)
    assert headers["Urgency"] == "high"
    assert headers["content-encoding"] == "aes128gcm"
    # VAPID 서명에 우리 공개키가 들어간다(웹앱에 넣을 공개키와 같아야 한다)
    assert f"k={vapid.public_key_from_private(private_key)}" in headers["authorization"]

    plain = http_ece.decrypt(
        posted["data"], private_key=phone_key, auth_secret=auth_secret, version="aes128gcm"
    )
    assert json.loads(plain) == {
        "title": note.title,
        "body": note.body,
        "url": "/runs/7",
        "urgent": True,
    }


def test_real_pywebpush_410_deletes_subscription(monkeypatch):
    phone_public = (
        ec.generate_private_key(ec.SECP256R1())
        .public_key()
        .public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    )
    store = MemoryStore()
    store.add_push_subscription(
        PushSubscription(
            endpoint="https://p/1", p256dh=b64url(phone_public), auth=b64url(b"x" * 16)
        )
    )
    monkeypatch.setattr(requests, "post", lambda *a, **k: Response(410))
    with pytest.raises(NotifyError):
        WebPushNotifier(store, vapid.generate_private_key(), "mailto:a@b.c").send(NOTE)
    assert store.get_push_subscriptions() == []


# ---- 여러 곳에 보내기 ----


class Broken(Notifier):
    def send(self, notification):
        raise RuntimeError("down")


def test_multi_notifier_sends_to_all_then_reports_failure():
    memory = MemoryNotifier()
    with pytest.raises(NotifyError, match="Broken"):
        MultiNotifier([Broken(), memory]).send(NOTE)
    assert memory.messages == [NOTE]


def test_console_notifier_prints(capsys):
    ConsoleNotifier().send(NOTE)
    assert capsys.readouterr().out == f"{NOTE.title}\n{NOTE.body}\n"


# ---- VAPID 키 ----


def test_vapid_key_shapes():
    private_key = vapid.generate_private_key()
    assert len(base64.urlsafe_b64decode(private_key + "==")) == 32
    public = base64.urlsafe_b64decode(vapid.public_key_from_private(private_key) + "=")
    assert len(public) == 65 and public[0] == 4


def test_vapid_cli_writes_env_without_printing_private_key(tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text("BROKER=fake")  # 끝에 줄바꿈 없음

    assert vapid.main(env) == 0
    lines = env.read_text().splitlines()
    assert lines[0] == "BROKER=fake"
    private_key = lines[1].removeprefix("VAPID_PRIVATE_KEY=")
    out = capsys.readouterr().out
    assert private_key not in out
    public_line = f"VAPID_PUBLIC_KEY={vapid.public_key_from_private(private_key)}"
    assert public_line in out

    # 다시 실행하면 새로 만들지 않고 같은 공개키를 보여 준다
    assert vapid.main(env) == 0
    assert env.read_text().count("VAPID_PRIVATE_KEY=") == 1
    assert public_line in capsys.readouterr().out
