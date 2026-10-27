// node --test scheduler/worker.test.js
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import worker, { SCHEDULE, dispatch, dueWorkflows } from "./worker.js";

const ENV = { GITHUB_TOKEN: "test-token", GITHUB_REPO: "owner/repo" };
const at = (iso) => new Date(iso);
const noSleep = async () => {};

function fakeFetch(...responses) {
  const calls = [];
  const fn = async (url, init) => {
    calls.push({ url, init });
    const next = responses.shift();
    if (next instanceof Error) throw next;
    return new Response(next.body ?? null, { status: next.status });
  };
  return { fn, calls };
}

test("정해진 시각에 맞는 워크플로만 부른다", () => {
  assert.deepEqual(dueWorkflows(at("2026-10-25T23:40:00Z")), ["trade-kr.yml"]); // 일(UTC) = 한국 월 08:40
  assert.deepEqual(dueWorkflows(at("2026-10-26T00:47:00Z")), ["trade-kr.yml"]);
  assert.deepEqual(dueWorkflows(at("2026-10-26T04:17:00Z")), ["stoploss-kr.yml"]);
  assert.deepEqual(dueWorkflows(at("2026-10-26T14:40:00Z")), ["trade-us.yml"]);
  assert.deepEqual(dueWorkflows(at("2026-10-26T16:17:00Z")), ["trade-us.yml"]);
  assert.deepEqual(dueWorkflows(at("2026-10-26T17:47:00Z")), ["stoploss-us.yml"]);
});

test("다른 시각과 주말에는 부르지 않는다", () => {
  assert.deepEqual(dueWorkflows(at("2026-10-26T14:17:00Z")), []); // 깨어나는 분이지만 표에 없음
  assert.deepEqual(dueWorkflows(at("2026-10-26T23:47:00Z")), []);
  assert.deepEqual(dueWorkflows(at("2026-10-30T23:40:00Z")), []); // 금(UTC) 23:40 = 한국 토요일
  assert.deepEqual(dueWorkflows(at("2026-10-31T00:47:00Z")), []); // 토요일
  assert.deepEqual(dueWorkflows(at("2026-10-31T14:40:00Z")), []);
});

test("하루에 각 실행이 한 번씩 돈다", () => {
  const count = {};
  const monday = Date.parse("2026-10-26T00:00:00Z");
  for (let minute = 0; minute < 24 * 60; minute++) {
    for (const w of dueWorkflows(new Date(monday + minute * 60_000))) count[w] = (count[w] ?? 0) + 1;
  }
  assert.deepEqual(count, {
    "trade-kr.yml": 2, // 월요일 00:47과 23:40(화요일 아침 몫)
    "stoploss-kr.yml": 1,
    "trade-us.yml": 2,
    "stoploss-us.yml": 1,
  });
  assert.equal(SCHEDULE.length, 6);
});

test("잘못된 cron은 조용히 넘어가지 않는다", () => {
  assert.throws(() => dueWorkflows(at("2026-10-26T14:40:00Z"), [["x.yml", "40 14 * *"]]));
  assert.throws(() => dueWorkflows(at("2026-10-26T14:40:00Z"), [["x.yml", "40 14 * * MON"]]));
});

test("예약 실행으로 main 브랜치의 워크플로를 부른다", async () => {
  const { fn, calls } = fakeFetch({ status: 204 });
  await dispatch("trade-us.yml", ENV, { fetchFn: fn, sleep: noSleep });
  assert.equal(calls.length, 1);
  const { url, init } = calls[0];
  assert.equal(url, "https://api.github.com/repos/owner/repo/actions/workflows/trade-us.yml/dispatches");
  assert.equal(init.method, "POST");
  assert.equal(init.headers.Authorization, "Bearer test-token");
  assert.ok(init.headers["User-Agent"]); // GitHub API는 User-Agent가 없으면 거부한다
  assert.deepEqual(JSON.parse(init.body), {
    ref: "main",
    inputs: { scheduled: "true", dry_run: "false" },
  });
});

test("서버 오류와 연결 실패는 다시 한다", async () => {
  const { fn, calls } = fakeFetch(new Error("timeout"), { status: 502 }, { status: 204 });
  const waits = [];
  await dispatch("trade-kr.yml", ENV, { fetchFn: fn, sleep: async (ms) => waits.push(ms) });
  assert.equal(calls.length, 3);
  assert.deepEqual(waits, [2_000, 10_000]);
});

test("토큰이나 권한 오류는 다시 하지 않고 실패로 알린다", async () => {
  const { fn, calls } = fakeFetch({ status: 401, body: '{"message":"Bad credentials"}' });
  await assert.rejects(dispatch("trade-kr.yml", ENV, { fetchFn: fn, sleep: noSleep }), (e) => {
    assert.match(e.message, /trade-kr\.yml 실행 요청 실패: HTTP 401 .*Bad credentials/);
    assert.doesNotMatch(e.message, /test-token/);
    return true;
  });
  assert.equal(calls.length, 1);
});

test("계속 실패하면 세 번까지만 하고 실패로 알린다", async () => {
  const { fn, calls } = fakeFetch({ status: 500 }, { status: 500 }, { status: 503 });
  await assert.rejects(dispatch("trade-kr.yml", ENV, { fetchFn: fn, sleep: noSleep }), /HTTP 503/);
  assert.equal(calls.length, 3);
});

test("토큰이 없으면 부르지 않는다", async () => {
  const { fn, calls } = fakeFetch();
  await assert.rejects(
    dispatch("trade-kr.yml", { GITHUB_REPO: "owner/repo" }, { fetchFn: fn, sleep: noSleep }),
    /GITHUB_TOKEN/,
  );
  assert.equal(calls.length, 0);
});

// ---- Cloudflare가 부르는 입구 ----

const realFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = realFetch;
});

test("예약 시각에 깨어나면 그 워크플로를 부른다", async () => {
  const { fn, calls } = fakeFetch({ status: 204 });
  globalThis.fetch = fn;
  await worker.scheduled({ scheduledTime: Date.parse("2026-10-26T14:40:00Z") }, ENV);
  assert.equal(calls.length, 1);
  assert.match(calls[0].url, /trade-us\.yml/);
});

test("할 일이 없는 시각에는 아무것도 부르지 않는다", async () => {
  const { fn, calls } = fakeFetch();
  globalThis.fetch = fn;
  await worker.scheduled({ scheduledTime: Date.parse("2026-10-26T15:17:00Z") }, ENV);
  assert.equal(calls.length, 0);
});

test("요청이 실패하면 Cloudflare 기록에 실패로 남긴다", async () => {
  globalThis.fetch = fakeFetch({ status: 403, body: "Resource not accessible" }).fn;
  await assert.rejects(
    worker.scheduled({ scheduledTime: Date.parse("2026-10-26T04:17:00Z") }, ENV),
    /stoploss-kr\.yml 실행 요청 실패: HTTP 403/,
  );
});
