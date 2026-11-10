// 알림함: 실행 기록 목록(알림으로 보낸 내용과 같다). 누르면 실행 상세.
import { useEffect, useState } from "preact/hooks";
import { Card, Empty, LoadError, Loading, RunStatusChip } from "../components/ui";
import { useApp } from "../hooks";
import { formatKst, marketLabel } from "../lib/format";
import { runBody, runTitle } from "../lib/runs";
import type { RunRow } from "../lib/types";

const PAGE = 30;

function preview(run: RunRow): string {
  const body = runBody(run).split("\n").filter(Boolean);
  return body.slice(0, 2).join(" / ") || "—";
}

export function Runs() {
  const { source, mode, tick } = useApp();
  const [runs, setRuns] = useState<RunRow[] | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [more, setMore] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    source.listRuns(mode, { limit: PAGE }).then(
      (rows) => {
        if (!alive) return;
        setRuns(rows);
        setMore(rows.length === PAGE);
        setError(null);
      },
      (err: Error) => alive && setError(err),
    );
    return () => {
      alive = false;
    };
  }, [mode, tick]);

  const loadMore = async () => {
    const last = runs?.[runs.length - 1];
    if (!last) return;
    setBusy(true);
    try {
      const rows = await source.listRuns(mode, { limit: PAGE, before: last.started_at });
      setRuns([...(runs ?? []), ...rows]);
      setMore(rows.length === PAGE);
    } catch (err) {
      setError(err as Error);
    } finally {
      setBusy(false);
    }
  };

  if (error && !runs) return <LoadError error={error} />;
  if (!runs) return <Loading />;

  return (
    <div>
      {error && <LoadError error={error} />}
      <Card>
        {runs.length === 0 ? (
          <Empty>실행 기록이 없습니다.</Empty>
        ) : (
          <ul class="rows">
            {runs.map((run) => {
              const title = runTitle(run);
              const tags = [title.includes("손절 확인") && "손절 확인", title.includes("시험 실행") && "시험 실행"].filter(Boolean);
              return (
                <li key={run.id}>
                  <a class="row-link" href={`/runs/${run.id}`}>
                    <div class="row-top">
                      <span class="name">
                        {marketLabel(run.market)}
                        {tags.length ? ` · ${tags.join(" · ")}` : ""}
                      </span>
                      <span class="muted small num">{formatKst(run.started_at)}</span>
                      <span class="end">
                        <RunStatusChip status={run.status} />
                      </span>
                    </div>
                    <div class="small" style={{ color: "var(--ink-2)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {preview(run)}
                    </div>
                  </a>
                </li>
              );
            })}
          </ul>
        )}
      </Card>
      {more && (
        <button class="btn block" onClick={loadMore} disabled={busy}>
          {busy ? "불러오는 중…" : "더 보기"}
        </button>
      )}
    </div>
  );
}
