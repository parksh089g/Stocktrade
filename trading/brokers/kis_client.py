"""KIS(한국투자증권) Open API 저수준 클라이언트.

토큰 발급과 캐시, 호출 간격, 재시도, 연속 조회를 맡는다. 비밀값(앱키/시크릿/토큰)은
로그와 오류 문장에 넣지 않는다. 참고: github.com/koreainvestment/open-trading-api

- 토큰은 1일 유효하고, 발급할 때마다 카카오 알림톡이 가며 1분에 1번만 발급된다.
  그래서 캐시해 다시 쓴다.
- 모의투자는 초당 호출 한도가 낮다. 해외 조회는 초당 1회로 맞춘다(CLAUDE.md).
- 초당 한도 초과(EGW00201)는 잠시 쉬고 다시 시도한다.
  토큰 만료(EGW00123)는 새로 발급받아 한 번 다시 시도한다.
- 주문(POST)은 연결 오류가 나도 다시 보내지 않는다.
  접수됐는지 알 수 없어 중복 주문이 될 수 있기 때문이다.
"""

import hashlib
import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import httpx

from trading.brokers.base import BrokerError
from trading.core.clock import KST, now_utc

logger = logging.getLogger(__name__)

LIVE_URL = "https://openapi.koreainvestment.com:9443"
PAPER_URL = "https://openapivts.koreainvestment.com:29443"

RATE_LIMIT_CODE = "EGW00201"  # 초당 거래건수 초과
TOKEN_ERROR_CODES = {"EGW00121", "EGW00123"}  # 유효하지 않은 토큰, 만료된 토큰
# 남은 시간이 이보다 짧으면 실행 도중 만료되지 않게 새로 발급받는다
TOKEN_MARGIN = timedelta(minutes=10)


class KisApiError(BrokerError):
    """KIS가 요청을 거절했다(rt_cd != 0). 주문이라면 '거부'로 다룬다."""

    def __init__(self, code: str, message: str, tr_id: str) -> None:
        super().__init__(f"KIS {tr_id} 거절: {message} ({code})")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class KisResponse:
    body: dict[str, Any]
    tr_cont: str  # "M"/"F"면 다음 페이지가 있다


@dataclass(frozen=True)
class CachedToken:
    token: str
    expires_at: datetime  # UTC


class TokenCache(Protocol):
    def load(self, key_id: str) -> CachedToken | None: ...

    def save(self, key_id: str, token: CachedToken) -> None: ...

    def clear(self, key_id: str) -> None: ...


class FileTokenCache:
    """로컬 파일 캐시(.cache/는 git에 올라가지 않는다). GitHub Actions용 캐시는 M6에서 붙인다."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self, key_id: str) -> CachedToken | None:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return None
        if data.get("key_id") != key_id:
            return None
        return CachedToken(data["token"], datetime.fromisoformat(data["expires_at"]))

    def save(self, key_id: str, token: CachedToken) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "key_id": key_id,
            "token": token.token,
            "expires_at": token.expires_at.isoformat(),
        }
        # 본인만 읽을 수 있게 만든다
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f)

    def clear(self, key_id: str) -> None:
        if self.load(key_id) is not None:
            self.path.unlink(missing_ok=True)


class KisClient:
    def __init__(
        self,
        *,
        app_key: str,
        app_secret: str,
        paper: bool,
        token_cache: TokenCache,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], datetime] = now_utc,
        sleep: Callable[[float], None] = time.sleep,
        timer: Callable[[], float] = time.monotonic,
        min_interval: float | None = None,
        overseas_interval: float = 1.0,
        max_rate_retries: int = 3,
    ) -> None:
        self.paper = paper
        self._app_key = app_key
        self._app_secret = app_secret
        # 캐시가 다른 앱키의 토큰을 돌려주지 않도록 앱키에서 만든 식별자로 구분한다
        self._key_id = hashlib.sha256(f"{paper}:{app_key}".encode()).hexdigest()[:16]
        self._cache = token_cache
        self._http = httpx.Client(
            base_url=PAPER_URL if paper else LIVE_URL, timeout=20, transport=transport
        )
        self._clock = clock
        self._sleep = sleep
        self._timer = timer
        # 모의투자는 초당 2건 정도, 실전은 초당 20건 미만으로 맞춘다
        self._min_interval = min_interval if min_interval is not None else (0.5 if paper else 0.06)
        self._overseas_interval = overseas_interval
        self._max_rate_retries = max_rate_retries
        self._last_call = float("-inf")
        self._last_overseas = float("-inf")
        self._token: CachedToken | None = None

    # ---- 호출 ----

    def get(
        self,
        path: str,
        tr_id: str,
        params: dict[str, str],
        *,
        tr_cont: str = "",
        overseas: bool = False,
    ) -> KisResponse:
        return self._call("GET", path, tr_id, params=params, tr_cont=tr_cont, overseas=overseas)

    def post(
        self, path: str, tr_id: str, body: dict[str, str], *, overseas: bool = False
    ) -> KisResponse:
        return self._call("POST", path, tr_id, body=body, overseas=overseas, resend_on_error=False)

    def get_pages(
        self,
        path: str,
        tr_id: str,
        params: dict[str, str],
        *,
        list_key: str,
        ctx_keys: tuple[str, str],
        overseas: bool = False,
        max_pages: int = 20,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """연속 조회로 모든 페이지의 list_key 목록을 모은다. (행 목록, 첫 페이지 응답)"""
        params = dict(params)
        rows: list[dict[str, Any]] = []
        first: dict[str, Any] | None = None
        tr_cont = ""
        for _ in range(max_pages):
            resp = self.get(path, tr_id, params, tr_cont=tr_cont, overseas=overseas)
            first = first if first is not None else resp.body
            rows += as_list(resp.body.get(list_key))
            if resp.tr_cont not in ("M", "F"):
                return rows, first
            for key in ctx_keys:
                params[key] = str(resp.body.get(key.lower(), "")).strip()
            tr_cont = "N"
        raise BrokerError(f"KIS {tr_id} 연속 조회가 {max_pages}페이지를 넘었습니다")

    def _call(
        self,
        method: str,
        path: str,
        tr_id: str,
        *,
        params: dict[str, str] | None = None,
        body: dict[str, str] | None = None,
        tr_cont: str = "",
        overseas: bool = False,
        resend_on_error: bool = True,
    ) -> KisResponse:
        rate_retries = 0
        error_retries = 0
        token_refreshed = False
        while True:
            headers = {
                "content-type": "application/json; charset=utf-8",
                "authorization": f"Bearer {self._access_token()}",
                "appkey": self._app_key,
                "appsecret": self._app_secret,
                "tr_id": tr_id,
                "custtype": "P",
                "tr_cont": tr_cont,
            }
            # 토큰 발급까지 마친 뒤, 보내기 바로 전에 간격을 맞춘다
            self._throttle(overseas)
            try:
                resp = self._http.request(method, path, params=params, json=body, headers=headers)
            except httpx.TransportError as e:
                if resend_on_error and error_retries < 2:
                    error_retries += 1
                    logger.warning("KIS %s 연결 오류, 다시 시도 (%s)", tr_id, type(e).__name__)
                    self._sleep(1.0 * error_retries)
                    continue
                raise BrokerError(f"KIS {tr_id} 연결 실패: {type(e).__name__}") from e

            data = _json(resp)
            code = str(data.get("msg_cd", ""))
            if code == RATE_LIMIT_CODE and rate_retries < self._max_rate_retries:
                # 한도 초과는 처리되지 않은 요청이라 주문도 다시 보내도 된다
                rate_retries += 1
                logger.warning("KIS 초당 한도 초과, %d초 쉬고 다시 시도", rate_retries)
                self._sleep(1.0 * rate_retries)
                continue
            if code in TOKEN_ERROR_CODES and not token_refreshed:
                logger.warning("KIS 토큰이 만료돼 새로 발급받습니다")
                self._drop_token()
                token_refreshed = True
                continue
            if resp.status_code >= 500 and resend_on_error and error_retries < 2:
                error_retries += 1
                logger.warning("KIS %s 서버 오류 %s, 다시 시도", tr_id, resp.status_code)
                self._sleep(1.0 * error_retries)
                continue
            if resp.status_code != 200 or "rt_cd" not in data:
                msg = str(data.get("msg1", ""))[:100]
                raise BrokerError(f"KIS {tr_id} 응답 오류: HTTP {resp.status_code} {code} {msg}")
            if str(data["rt_cd"]) != "0":
                raise KisApiError(code, str(data.get("msg1", "")).strip(), tr_id)
            return KisResponse(body=data, tr_cont=resp.headers.get("tr_cont", "").strip())

    def _throttle(self, overseas: bool) -> None:
        now = self._timer()
        wait = self._last_call + self._min_interval - now
        if overseas:
            wait = max(wait, self._last_overseas + self._overseas_interval - now)
        if wait > 0:
            self._sleep(wait)
        self._last_call = self._timer()
        if overseas:
            self._last_overseas = self._last_call

    # ---- 토큰 ----

    def _access_token(self) -> str:
        now = self._clock()
        if self._token and self._token.expires_at - now > TOKEN_MARGIN:
            return self._token.token
        cached = self._cache.load(self._key_id)
        if cached and cached.expires_at - now > TOKEN_MARGIN:
            self._token = cached
            return cached.token
        self._token = self._issue_token()
        self._cache.save(self._key_id, self._token)
        return self._token.token

    def _issue_token(self) -> CachedToken:
        logger.info("KIS 접근 토큰을 새로 발급받습니다")
        self._throttle(overseas=False)
        try:
            resp = self._http.post(
                "/oauth2/tokenP",
                json={
                    "grant_type": "client_credentials",
                    "appkey": self._app_key,
                    "appsecret": self._app_secret,
                },
            )
        except httpx.TransportError as e:
            raise BrokerError(f"KIS 토큰 발급 연결 실패: {type(e).__name__}") from e
        data = _json(resp)
        token = data.get("access_token")
        if resp.status_code != 200 or not token:
            detail = data.get("error_description") or data.get("msg1") or ""
            code = data.get("error_code") or data.get("msg_cd") or ""
            raise BrokerError(f"KIS 토큰 발급 실패: HTTP {resp.status_code} {code} {detail}")
        expired = data.get("access_token_token_expired")
        if expired:
            expires_at = datetime.strptime(expired, "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
        else:
            expires_at = self._clock() + timedelta(seconds=int(data.get("expires_in", 86400)))
        return CachedToken(token=token, expires_at=expires_at.astimezone(UTC))

    def _drop_token(self) -> None:
        self._token = None
        self._cache.clear(self._key_id)


def as_list(value: Any) -> list[dict[str, Any]]:
    """KIS 응답의 output은 목록일 때도, 하나짜리 객체일 때도 있다."""
    if value is None:
        return []
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    return [value] if isinstance(value, dict) else []


def _json(resp: httpx.Response) -> dict[str, Any]:
    try:
        data = resp.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}
