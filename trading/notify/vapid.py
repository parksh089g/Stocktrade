"""웹 푸시용 VAPID 키 만들기.

  uv run python -m trading.notify.vapid

개인키는 화면에 찍지 않고 .env에 바로 넣는다(GitHub Secrets에는 .env에서 복사해 넣는다).
공개키는 화면에 보여 준다. 공개키는 웹앱(M7)에 넣는 값이라 알려져도 괜찮다.
이미 .env에 개인키가 있으면 새로 만들지 않고 그 개인키의 공개키만 보여 준다.
"""

import base64
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

ENV_NAME = "VAPID_PRIVATE_KEY"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def generate_private_key() -> str:
    """base64url로 쓴 32바이트 개인키(pywebpush가 그대로 받는 형식)."""
    key = ec.generate_private_key(ec.SECP256R1())
    return _b64url(key.private_numbers().private_value.to_bytes(32, "big"))


def public_key_from_private(private_key: str) -> str:
    """웹앱의 applicationServerKey로 쓰는 공개키(압축하지 않은 65바이트, base64url)."""
    value = int.from_bytes(_b64url_decode(private_key), "big")
    public = ec.derive_private_key(value, ec.SECP256R1()).public_key()
    return _b64url(
        public.public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
    )


def _read_env_value(env_path: Path, name: str) -> str | None:
    if not env_path.exists():
        return None
    for line in env_path.read_text().splitlines():
        key, _, value = line.partition("=")
        if key.strip() == name and value.strip():
            return value.strip()
    return None


def main(env_path: Path = Path(".env")) -> int:
    existing = _read_env_value(env_path, ENV_NAME)
    if existing:
        print(f"{env_path}에 이미 {ENV_NAME}가 있어 새로 만들지 않았습니다.")
        private_key = existing
    else:
        private_key = generate_private_key()
        prefix = "" if not env_path.exists() or env_path.read_text().endswith("\n") else "\n"
        with env_path.open("a") as f:
            f.write(f"{prefix}{ENV_NAME}={private_key}\n")
        print(f"{ENV_NAME}를 만들어 {env_path}에 넣었습니다. (화면에는 보여 주지 않습니다)")
    print(f"VAPID_PUBLIC_KEY={public_key_from_private(private_key)}")
    print("공개키는 M7 웹앱 설정에 씁니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
