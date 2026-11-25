"""테스트용 가짜 Supabase REST(PostgREST). SupabaseStore가 쓰는 만큼만 흉내 낸다.

- 필터: col=eq.값, col=in.(a,b), limit
- missing_columns에 넣은 칸은 표에 없는 것처럼 400으로 답한다(schema.sql을 다시 실행하기 전)
- POST: on_conflict + Prefer resolution=merge-duplicates면 upsert, 아니면 insert(runs는 id 부여)
- Prefer return=representation이면 넣은 행을 돌려준다
- rpc/get_engine_secrets는 vault의 값을 돌려준다
- numeric 컬럼은 진짜처럼 JSON 숫자로 돌려준다
"""

import json
import re
from typing import Any

import httpx

NUMERIC = {"limit_price", "avg_price", "avg_fill_price"}
_NUM_MARK = re.compile(r'"__NUM__(.*?)__"')


class FakePostgrest:
    def __init__(self, key: str = "sb_secret_test") -> None:
        self.key = key
        self.tables: dict[str, list[dict[str, Any]]] = {
            "trading_switch": [
                {"id": 1, "paused": False, "reason": "", "updated_at": "2026-10-26T00:00:00+00:00"}
            ],
            "positions": [],
            "orders": [],
            "runs": [],
            "push_subscriptions": [],
            "engine_cache": [],
            "engine_settings": [],
        }
        # Vault 비밀값(rpc/get_engine_secrets가 돌려준다): 이름 → 값
        self.vault: dict[str, str] = {}
        self.requests: list[httpx.Request] = []
        # 앞에서부터 하나씩 꺼내 쓴다: 숫자면 그 상태 코드로 응답, 예외면 던진다
        self.fail_next: list[int | Exception] = []
        # 표 이름 → 아직 없는 칸 (예: {"orders": {"reason"}})
        self.missing_columns: dict[str, set[str]] = {}
        self._next_id = 1

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail_next:
            failure = self.fail_next.pop(0)
            if isinstance(failure, Exception):
                raise failure
            return httpx.Response(failure, text='{"message":"boom"}')
        if request.headers.get("apikey") != self.key:
            return httpx.Response(401, text='{"message":"bad key"}')

        table = request.url.path.removeprefix("/rest/v1/")
        if table == "rpc/get_engine_secrets":
            rows = [{"name": k, "value": v} for k, v in self.vault.items()]
            return httpx.Response(200, text=json.dumps(rows))
        rows = self.tables[table]
        params = dict(request.url.params.multi_items())
        select = params.pop("select", None)
        limit = params.pop("limit", None)
        on_conflict = params.pop("on_conflict", None)
        prefer = request.headers.get("prefer", "")
        missing = self.missing_columns.get(table, set())

        def matches(row: dict[str, Any]) -> bool:
            return all(_match(row.get(col), expr) for col, expr in params.items())

        if request.method == "GET":
            if gone := missing & set((select or "").split(",")):
                col = sorted(gone)[0]
                return self._error(400, "42703", f"column {table}.{col} does not exist")
            found = [_project(r, select) for r in rows if matches(r)]
            return self._json(found[: int(limit)] if limit else found)

        if request.method == "POST":
            body = json.loads(request.content)
            items = body if isinstance(body, list) else [body]
            if gone := missing & {k for item in items for k in item}:
                col = sorted(gone)[0]
                message = f"Could not find the '{col}' column of '{table}' in the schema cache"
                return self._error(400, "PGRST204", message)
            written = []
            for item in items:
                if on_conflict and "merge-duplicates" in prefer:
                    keys = on_conflict.split(",")
                    found = next((r for r in rows if all(r[k] == item[k] for k in keys)), None)
                    if found:
                        found.update(item)
                        written.append(found)
                        continue
                if table == "runs":
                    item = {"id": self._next_id, **item}
                    self._next_id += 1
                rows.append(item)
                written.append(item)
            if "return=representation" in prefer:
                return self._json([_project(r, select) for r in written], status=201)
            return httpx.Response(201)

        if request.method == "PATCH":
            for r in rows:
                if matches(r):
                    r.update(json.loads(request.content))
            return httpx.Response(204)

        if request.method == "DELETE":
            self.tables[table] = [r for r in rows if not matches(r)]
            return httpx.Response(204)

        return httpx.Response(405)

    def _error(self, status: int, code: str, message: str) -> httpx.Response:
        return httpx.Response(status, json={"code": code, "message": message})

    def _json(self, data: Any, status: int = 200) -> httpx.Response:
        def mark(value: Any) -> Any:
            if isinstance(value, dict):
                return {
                    k: f"__NUM__{v}__" if k in NUMERIC and v is not None else mark(v)
                    for k, v in value.items()
                }
            if isinstance(value, list):
                return [mark(v) for v in value]
            return value

        text = _NUM_MARK.sub(r"\1", json.dumps(mark(data), ensure_ascii=False))
        return httpx.Response(status, text=text, headers={"content-type": "application/json"})


def _match(value: Any, expr: str) -> bool:
    op, _, arg = expr.partition(".")
    if op == "eq":
        return str(value) == arg
    if op == "in":
        return str(value) in arg.strip("()").split(",")
    raise ValueError(f"지원하지 않는 필터: {expr}")


def _project(row: dict[str, Any], select: str | None) -> dict[str, Any]:
    if not select or select == "*":
        return dict(row)
    return {col: row.get(col) for col in select.split(",")}
