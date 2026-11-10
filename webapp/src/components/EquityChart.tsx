// 계좌 vs 비교 지수 수익 곡선. 둘 다 시작일=100으로 맞춰 축 하나에 그린다(이중 축 금지).
// 계좌는 강조색, 지수는 회색. 손가락으로 훑으면 그날 값을 보여 주고, 표로도 볼 수 있다.
import { useEffect, useRef, useState } from "preact/hooks";
import { formatDateKey } from "../lib/format";
import type { IndexedPoint } from "../lib/performance";

const HEIGHT = 180;
const AXIS = 22; // x축 글자 자리(높이에 포함)
const LEFT = 34;
const RIGHT = 10;
const TOP = 8;

function niceStep(span: number, count: number): number {
  const raw = span / Math.max(1, count);
  const mag = 10 ** Math.floor(Math.log10(raw));
  const unit = [1, 2, 5, 10].find((u) => u * mag >= raw) ?? 10;
  return unit * mag;
}

function useWidth(ref: { current: HTMLElement | null }): number {
  const [width, setWidth] = useState(320);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const update = () => setWidth(Math.max(200, Math.round(el.getBoundingClientRect().width)));
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return width;
}

const one = (v: number) => v.toFixed(1);

export function EquityChart({ points, benchName }: { points: IndexedPoint[]; benchName: string }) {
  const box = useRef<HTMLDivElement>(null);
  const width = useWidth(box);
  const [active, setActive] = useState<number | null>(null);
  const [table, setTable] = useState(false);

  const values = points.flatMap((p) => (p.bench === null ? [p.account] : [p.account, p.bench]));
  let lo = Math.min(100, ...values);
  let hi = Math.max(100, ...values);
  if (hi - lo < 2) {
    lo -= 1;
    hi += 1;
  }
  const step = niceStep(hi - lo, 4);
  lo = Math.floor(lo / step) * step;
  hi = Math.ceil(hi / step) * step;
  const ticks: number[] = [];
  for (let t = lo; t <= hi + step / 2; t += step) ticks.push(Number(t.toFixed(6)));

  const plotH = HEIGHT - AXIS - TOP;
  const plotW = width - LEFT - RIGHT;
  const x = (i: number) => LEFT + (points.length === 1 ? plotW / 2 : (i / (points.length - 1)) * plotW);
  const y = (v: number) => TOP + (1 - (v - lo) / (hi - lo)) * plotH;
  const path = (pick: (p: IndexedPoint) => number | null) => {
    let d = "";
    let pen = false;
    points.forEach((p, i) => {
      const v = pick(p);
      if (v === null) {
        pen = false;
        return;
      }
      d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
      pen = true;
    });
    return d;
  };

  const last = points[points.length - 1];
  const xTicks = points.length >= 5 ? [0, Math.floor((points.length - 1) / 2), points.length - 1] : [0, points.length - 1];

  const pick = (clientX: number) => {
    const el = box.current;
    if (!el || points.length === 0) return;
    const left = el.getBoundingClientRect().left;
    const i = points.length === 1 ? 0 : Math.round(((clientX - left - LEFT) / plotW) * (points.length - 1));
    setActive(Math.min(points.length - 1, Math.max(0, i)));
  };

  const cur = active === null ? null : points[active];
  const label = `계좌와 ${benchName}의 수익 곡선, 시작=100. 마지막 날 계좌 ${last ? one(last.account) : "—"}, ${benchName} ${last?.bench == null ? "자료 없음" : one(last.bench)}`;

  return (
    <div>
      <div
        class="chart"
        ref={box}
        onPointerDown={(e) => pick(e.clientX)}
        onPointerMove={(e) => (e.pointerType === "mouse" || e.buttons ? pick(e.clientX) : undefined)}
        onPointerLeave={(e) => (e.pointerType === "mouse" ? setActive(null) : undefined)}
      >
        <svg width={width} height={HEIGHT} role="img" aria-label={label}>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={LEFT} x2={width - RIGHT} y1={y(t)} y2={y(t)} stroke={t === 100 ? "var(--axis)" : "var(--line)"} stroke-width="1" />
              <text x={LEFT - 6} y={y(t) + 4} text-anchor="end" font-size="11" fill="var(--muted)" class="num">
                {t}
              </text>
            </g>
          ))}
          {xTicks.map((i, n) => (
            <text
              key={`x${i}`}
              x={x(i)}
              y={HEIGHT - 6}
              text-anchor={points.length === 1 ? "middle" : n === 0 ? "start" : n === xTicks.length - 1 ? "end" : "middle"}
              font-size="11"
              fill="var(--muted)"
            >
              {points[i] ? formatDateKey(points[i].date).replace(/\(.\)/, "") : ""}
            </text>
          ))}
          <path d={path((p) => p.bench)} fill="none" stroke="var(--bench)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" />
          <path d={path((p) => p.account)} fill="none" stroke="var(--accent)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" />
          {last && last.bench !== null && (
            <circle cx={x(points.length - 1)} cy={y(last.bench)} r="4" fill="var(--bench)" stroke="var(--surface)" stroke-width="2" />
          )}
          {last && <circle cx={x(points.length - 1)} cy={y(last.account)} r="4" fill="var(--accent)" stroke="var(--surface)" stroke-width="2" />}
          {cur && active !== null && (
            <g>
              <line x1={x(active)} x2={x(active)} y1={TOP} y2={TOP + plotH} stroke="var(--axis)" stroke-width="1" />
              {cur.bench !== null && <circle cx={x(active)} cy={y(cur.bench)} r="4" fill="var(--bench)" stroke="var(--surface)" stroke-width="2" />}
              <circle cx={x(active)} cy={y(cur.account)} r="4" fill="var(--accent)" stroke="var(--surface)" stroke-width="2" />
            </g>
          )}
        </svg>
        {cur && active !== null && (
          <div class="tip" style={{ left: `${Math.min(Math.max(0, x(active) - 60), width - 132)}px` }}>
            <div class="muted">{formatDateKey(cur.date)}</div>
            <div class="num">계좌 {one(cur.account)}</div>
            <div class="num">
              {benchName} {cur.bench === null ? "—" : one(cur.bench)}
            </div>
          </div>
        )}
      </div>
      <div class="legend">
        <span>
          <i style={{ background: "var(--accent)" }} />
          계좌
        </span>
        <span>
          <i style={{ background: "var(--bench)" }} />
          {benchName}
        </span>
        <button type="button" class="btn link small" style={{ marginLeft: "auto" }} onClick={() => setTable(!table)} aria-expanded={table}>
          {table ? "표 닫기" : "표로 보기"}
        </button>
      </div>
      {table && (
        <table class="data" style={{ marginTop: "8px" }}>
          <thead>
            <tr>
              <th>날짜</th>
              <th>계좌</th>
              <th>{benchName}</th>
            </tr>
          </thead>
          <tbody>
            {[...points].reverse().map((p) => (
              <tr key={p.date}>
                <td>{formatDateKey(p.date)}</td>
                <td>{one(p.account)}</td>
                <td>{p.bench === null ? "—" : one(p.bench)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
