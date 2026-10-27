/**
 * 예약 실행을 정시에 부르는 Cloudflare Worker. 설정 방법은 docs/SETUP.md 5장.
 *
 * GitHub 예약(cron)은 몇 시간씩 늦게 시작할 때가 있어서(2026-10-26에 최대 6시간), Cloudflare의
 * Cron Trigger가 정해진 시각에 GitHub Actions 워크플로를 workflow_dispatch로 부른다. 손으로 실행
 * 버튼을 누른 것과 같아서 바로 시작한다. GitHub 예약도 대비용으로 그대로 둔다. 엔진이 하루 한 번만
 * 돌게 막으므로(--once-per-day) 늦게 온 GitHub 예약은 기록과 알림 없이 끝난다.
 *
 * 무료 플랜은 계정당 Cron Trigger가 5개까지라서, 실행 시각들의 '분'마다 깨어나(wrangler.toml)
 * 아래 표에서 지금 시각에 맞는 워크플로만 부른다.
 *
 * 필요한 값
 * - GITHUB_TOKEN: 비밀값. 이 저장소 하나에 Actions 읽기/쓰기 권한만 준 GitHub 토큰. 로그에 찍지 않는다.
 * - GITHUB_REPO: wrangler.toml의 [vars]. 예: parksh089g/Stocktrade
 */

// [워크플로 파일, 실행 시각(UTC cron)]. GitHub 예약의 cron과 같아야 한다(tests/test_workflows.py가 확인).
export const SCHEDULE = [
  ["trade-kr.yml", "40 23 * * 0-4"], // 한국 월~금 08:40
  ["trade-kr.yml", "47 0 * * 1-5"], // 한국 월~금 09:47 (백업: 앞 실행이 오류로 끝났으면 다시 돈다)
  ["stoploss-kr.yml", "17 4 * * 1-5"], // 한국 월~금 13:17
  ["trade-us.yml", "40 14 * * 1-5"], // 뉴욕 09:40(겨울) / 10:40(서머타임)
  ["trade-us.yml", "17 16 * * 1-5"], // 뉴욕 11:17 / 12:17 (백업)
  ["stoploss-us.yml", "47 17 * * 1-5"], // 뉴욕 12:47 / 13:47
];

// 예약 실행으로 부른다. 시험 실행이 아니고, 저장소 변수 SCHEDULE_ENABLED가 true일 때만 돈다.
const INPUTS = { scheduled: "true", dry_run: "false" };
const RETRY_DELAYS_MS = [2_000, 10_000];

/** 이 시각(분 단위)에 불러야 할 워크플로들. */
export function dueWorkflows(time, schedule = SCHEDULE) {
  const now = [
    time.getUTCMinutes(),
    time.getUTCHours(),
    time.getUTCDate(),
    time.getUTCMonth() + 1,
    time.getUTCDay(), // 0=일요일
  ];
  return schedule.filter(([, cron]) => cronMatches(cron, now)).map(([workflow]) => workflow);
}

function cronMatches(cron, now) {
  const fields = cron.trim().split(/\s+/);
  if (fields.length !== 5) throw new Error(`cron 형식 오류: ${cron}`);
  return fields.every((field, i) => fieldMatches(field, now[i]));
}

function fieldMatches(field, value) {
  // '*', 숫자, 범위(a-b), 쉼표 목록만 쓴다
  return field.split(",").some((part) => {
    if (part === "*") return true;
    const [low, high = low] = part.split("-").map(Number);
    if (!Number.isInteger(low) || !Number.isInteger(high)) {
      throw new Error(`cron 형식 오류: ${field}`);
    }
    return low <= value && value <= high;
  });
}

/** GitHub에 워크플로 실행을 요청한다. 서버 오류와 네트워크 오류는 조금 기다렸다가 다시 한다. */
export async function dispatch(workflow, env, { fetchFn = (...a) => fetch(...a), sleep } = {}) {
  sleep ??= (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  if (!env.GITHUB_TOKEN) throw new Error("GITHUB_TOKEN 비밀값이 없습니다");
  if (!env.GITHUB_REPO) throw new Error("GITHUB_REPO 값이 없습니다");
  const url = `https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/${workflow}/dispatches`;
  const init = {
    method: "POST",
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      "Content-Type": "application/json",
      "User-Agent": "stocktrade-scheduler",
      "X-GitHub-Api-Version": "2022-11-28",
    },
    body: JSON.stringify({ ref: "main", inputs: INPUTS }),
  };
  let problem = "";
  for (let attempt = 0; attempt <= RETRY_DELAYS_MS.length; attempt++) {
    if (attempt > 0) await sleep(RETRY_DELAYS_MS[attempt - 1]);
    let resp;
    try {
      resp = await fetchFn(url, init);
    } catch (e) {
      problem = `연결 실패: ${e.message}`;
      continue;
    }
    if (resp.ok) return;
    problem = `HTTP ${resp.status} ${(await resp.text()).slice(0, 200)}`;
    // 토큰 만료, 권한 부족, 잘못된 입력값은 다시 해도 같다
    if (resp.status < 500 && resp.status !== 429) break;
  }
  throw new Error(`${workflow} 실행 요청 실패: ${problem}`);
}

export default {
  async scheduled(controller, env) {
    const workflows = dueWorkflows(new Date(controller.scheduledTime));
    // 하나가 실패해도 나머지는 부른다
    const results = await Promise.allSettled(workflows.map((w) => dispatch(w, env)));
    const failed = [];
    results.forEach((result, i) => {
      if (result.status === "fulfilled") console.log(`${workflows[i]} 실행 요청`);
      else failed.push(result.reason.message);
    });
    // Cloudflare 기록에 실패로 남긴다. GitHub 예약이 늦게라도 대신 돈다.
    if (failed.length) throw new Error(failed.join("; "));
  },
};
