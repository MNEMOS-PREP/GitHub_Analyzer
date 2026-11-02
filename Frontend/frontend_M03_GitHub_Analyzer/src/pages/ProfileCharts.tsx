/**
 * M03 · coding-profile charts: when you practised, how your rating moved, and where your solves sit.
 *
 * What the sites can tell us is WHEN a submission happened and what it was, never how long it took, so "time spent"
 * is shown as submissions and active days, and the page says so. Nothing here ranks or scores anyone.
 *
 * Colour: series identity uses the data-viz categorical order (Codeforces = slot 1 blue, LeetCode = slot 2 orange),
 * validated for colour-blind separation on the shell's own light and dark panels. The platform's green and amber are
 * NOT used: on this platform they mean "confirmed" and "reported". Chrome (grid, axis, ink) uses the shell's tokens.
 * Plain SVG and HTML, so the module gains no dependency.
 */

import { useMemo, useRef, useState } from "react";

import { Button, Card } from "@ai/core";

import type { DayCount, Platform, ProfileRow, RatingPoint } from "../types";

const NAME: Record<Platform, string> = { codeforces: "Codeforces", leetcode: "LeetCode", codechef: "CodeChef" };
const COLOUR: Partial<Record<Platform, string>> = { codeforces: "var(--viz-1)", leetcode: "var(--viz-2)" };
const ORDER: Platform[] = ["codeforces", "leetcode"];                      // stack order, fixed: the entity keeps its colour

/* Both palettes pass the validator on the shell's surfaces (light #ffffff, dark #142d27); see the data-viz notes. */
const CSS = `
.viz-root{--viz-1:#2a78d6;--viz-2:#eb6834;--viz-o1:#86b6ef;--viz-o2:#3987e5;--viz-o3:#1c5cab}
@media (prefers-color-scheme: dark){:root:where(:not([data-theme="light"])) .viz-root{--viz-1:#3987e5;--viz-2:#d95926;--viz-o1:#1c5cab;--viz-o2:#3987e5;--viz-o3:#86b6ef}}
:root[data-theme="dark"] .viz-root{--viz-1:#3987e5;--viz-2:#d95926;--viz-o1:#1c5cab;--viz-o2:#3987e5;--viz-o3:#86b6ef}
.viz-root svg text{font-family:inherit}
`;

const n = (v: number) => v.toLocaleString();

/* ---- dates: every day here is a UTC calendar day, the same one the service uses ---------------------------------- */

const DAY = 86_400_000;
const iso = (d: Date) => d.toISOString().slice(0, 10);
const addDays = (d: Date, k: number) => new Date(d.getTime() + k * DAY);
const utcToday = () => {
  const t = new Date();
  return new Date(Date.UTC(t.getUTCFullYear(), t.getUTCMonth(), t.getUTCDate()));
};
const short = (d: Date) => d.toLocaleDateString("en", { day: "numeric", month: "short", timeZone: "UTC" });
const monthName = (d: Date) => d.toLocaleDateString("en", { month: "long", year: "numeric", timeZone: "UTC" });

type Mode = "month" | "30d" | "12m";

interface Col {
  id: string;
  label: string;
  tick: string;
  segs: { platform: Platform; value: number }[];
}

type Series = { platform: Platform; days: Map<string, DayCount> };

function seriesOf(rows: ProfileRow[]): Series[] {
  return ORDER.flatMap((platform) => {
    const row = rows.find((r) => r.platform === platform);
    const daily = row?.stats?.daily;
    return daily ? [{ platform, days: new Map(daily.map((d) => [d.date, d])) }] : [];
  });
}

/** One column per day (a month or the last 30 days) or per month (the last 12). */
function columns(series: Series[], mode: Mode, back: number): { cols: Col[]; title: string; from: string; to: string } {
  const today = utcToday();
  const value = (p: Series, from: Date, days: number) => {
    let total = 0;
    for (let i = 0; i < days; i++) total += p.days.get(iso(addDays(from, i)))?.submissions ?? 0;
    return total;
  };
  if (mode === "12m") {
    const cols: Col[] = [];
    for (let k = 11; k >= 0; k--) {
      const first = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth() - k, 1));
      const len = new Date(Date.UTC(first.getUTCFullYear(), first.getUTCMonth() + 1, 0)).getUTCDate();
      cols.push({
        id: iso(first).slice(0, 7),
        label: monthName(first),
        tick: first.toLocaleDateString("en", { month: "short", timeZone: "UTC" }),
        segs: series.map((p) => ({ platform: p.platform, value: value(p, first, len) })),
      });
    }
    const start = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth() - 11, 1));
    return { cols, title: "Last 12 months", from: iso(start), to: iso(today) };
  }
  let start: Date;
  let days: number;
  let title: string;
  if (mode === "30d") {
    start = addDays(today, -29);
    days = 30;
    title = "Last 30 days";
  } else {
    start = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth() - back, 1));
    days = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth() + 1, 0)).getUTCDate();
    title = back === 0 ? "This month" : monthName(start);
  }
  const cols = Array.from({ length: days }, (_, i) => {
    const d = addDays(start, i);
    return {
      id: iso(d),
      label: short(d),
      tick: String(d.getUTCDate()),
      segs: series.map((p) => ({ platform: p.platform, value: p.days.get(iso(d))?.submissions ?? 0 })),
    };
  });
  return { cols, title, from: iso(start), to: iso(addDays(start, days - 1)) };
}

function activeDays(series: Series[], from: string, to: string): number {
  const days = new Set<string>();
  for (const s of series) for (const [d, c] of s.days) if (d >= from && d <= to && c.submissions > 0) days.add(d);
  return days.size;
}

/* ---- the card -------------------------------------------------------------------------------------------------- */

export function ProfileCharts({ rows }: { rows: ProfileRow[] }) {
  const series = useMemo(() => seriesOf(rows), [rows]);
  const lines = rows.filter((r) => (r.stats?.rating_history?.length ?? 0) > 1 && COLOUR[r.platform]);
  const mix = rows.filter((r) => r.stats && (r.platform === "leetcode" || r.stats.solved_by_rating));
  if (series.length === 0 && lines.length === 0 && mix.length === 0) return null;
  return (
    <>
      <style>{CSS}</style>
      {series.length > 0 && <ActivityCard series={series} />}
      {lines.length > 0 && (
        <Card title="Rating over time" meta="each site on its own scale">
          <div className="viz-root space-y-8">
            {lines.map((r) => (
              <LineChart key={r.platform} platform={r.platform} points={r.stats!.rating_history!} />
            ))}
          </div>
        </Card>
      )}
      {mix.length > 0 && <MixCard rows={mix} />}
    </>
  );
}

function ActivityCard({ series }: { series: Series[] }) {
  const [mode, setMode] = useState<Mode>("month");
  const [back, setBack] = useState(0);
  const { cols, title, from, to } = useMemo(() => columns(series, mode, back), [series, mode, back]);
  const total = cols.reduce((a, c) => a + c.segs.reduce((b, s) => b + s.value, 0), 0);
  const active = activeDays(series, from, to);
  const prev = useMemo(() => (mode === "month" ? columns(series, "month", back + 1) : null), [series, mode, back]);
  const prevTotal = prev?.cols.reduce((a, c) => a + c.segs.reduce((b, s) => b + s.value, 0), 0) ?? 0;
  const prevActive = prev ? activeDays(series, prev.from, prev.to) : 0;
  const earliest = series.flatMap((s) => [...s.days.keys()]).sort()[0];

  const choose = (m: Mode) => {
    setMode(m);
    setBack(0);
  };
  return (
    <Card title="Activity" meta="submissions per day">
      <div className="viz-root">
        <div className="flex flex-wrap items-center gap-2">
          {([["month", "Month"], ["30d", "Last 30 days"], ["12m", "Last 12 months"]] as [Mode, string][]).map(([m, label]) => (
            <Button key={m} size="sm" variant={mode === m ? "secondary" : "ghost"} aria-pressed={mode === m} onClick={() => choose(m)}>
              {label}
            </Button>
          ))}
          {mode === "month" && (
            <span className="ml-auto flex items-center gap-1">
              <Button size="sm" variant="ghost" aria-label="Previous month" disabled={!earliest || prev!.to < earliest} onClick={() => setBack(back + 1)}>‹</Button>
              <Button size="sm" variant="ghost" aria-label="Next month" disabled={back === 0} onClick={() => setBack(back - 1)}>›</Button>
            </span>
          )}
        </div>

        <p className="mt-4 text-ink">
          <span className="font-semibold">{title}:</span> {n(total)} submission{total === 1 ? "" : "s"} on {active} active day{active === 1 ? "" : "s"}
        </p>
        {prev && (
          <p className="provenance mt-0.5">
            {monthName(new Date(prev.from))}: {n(prevTotal)} on {prevActive} day{prevActive === 1 ? "" : "s"}
          </p>
        )}

        <Legend platforms={series.map((s) => s.platform)} />
        {total === 0 ? (
          <p className="mt-4 rounded-lg bg-surface-sunken px-3 py-6 text-center text-sm text-ink-soft">
            No submissions in this period.{mode === "month" && back === 0 ? " Use ‹ to see an earlier month." : ""}
          </p>
        ) : (
          <Columns cols={cols} platforms={series.map((s) => s.platform)} summary={`${title}: ${total} submissions on ${active} active days`} />
        )}
        <p className="provenance mt-3">
          These sites report when you submitted, not how long you worked, so time spent isn't available. Submissions and
          active days are the closest honest measure.
        </p>
      </div>
    </Card>
  );
}

function Legend({ platforms }: { platforms: Platform[] }) {
  if (platforms.length < 2) return null;
  return (
    <ul className="mt-3 flex flex-wrap gap-4 text-sm text-ink-soft" aria-label="Legend">
      {platforms.map((p) => (
        <li key={p} className="flex items-center gap-1.5">
          <span aria-hidden className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: COLOUR[p] }} />
          {NAME[p]}
        </li>
      ))}
    </ul>
  );
}

/* ---- stacked columns ------------------------------------------------------------------------------------------- */

function niceMax(v: number): number {
  if (v <= 2) return 2;
  if (v <= 10) return Math.ceil(v / 2) * 2;
  const pow = 10 ** Math.floor(Math.log10(v));
  // every step is an even number, so the middle tick is a clean half
  return [1, 1.2, 1.6, 2, 2.4, 3, 4, 5, 6, 8, 10].map((f) => f * pow).find((m) => m >= v)!;
}

/** A column with a rounded top and a square foot; `r` is capped so a thin or short column never inverts. */
function colPath(x: number, y: number, w: number, h: number, r: number): string {
  const k = Math.min(r, w / 2, h);
  return `M${x},${y + h} V${y + k} Q${x},${y} ${x + k},${y} H${x + w - k} Q${x + w},${y} ${x + w},${y + k} V${y + h} Z`;
}

function Columns({ cols, platforms, summary }: { cols: Col[]; platforms: Platform[]; summary: string }) {
  const W = 640, H = 210, L = 34, R = 6, T = 8, B = 24;
  const iw = W - L - R, ih = H - T - B, base = H - B;
  const totals = cols.map((c) => c.segs.reduce((a, s) => a + s.value, 0));
  const max = niceMax(Math.max(1, ...totals));
  const band = iw / cols.length;
  const bw = Math.min(24, Math.max(2, band - 2));
  const every = cols.length > 16 ? 5 : 1;
  const [hover, setHover] = useState<number | null>(null);
  const svg = useRef<SVGSVGElement>(null);
  const wrap = useRef<HTMLDivElement>(null);
  const [tipX, setTipX] = useState(0);

  const show = (i: number) => {
    setHover(i);
    const w = svg.current?.getBoundingClientRect().width ?? W;
    setTipX(Math.min(Math.max(((L + band * (i + 0.5)) / W) * w, 70), w - 70));
  };
  const ticks = [0, Math.round(max / 2), max];

  return (
    <div ref={wrap} className="relative mt-3" onPointerLeave={() => setHover(null)}>
      <svg ref={svg} viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label={summary}>
        {ticks.map((t) => {
          const y = base - (t / max) * ih;
          return (
            <g key={t}>
              <line x1={L} x2={W - R} y1={y} y2={y} stroke="var(--color-line)" strokeWidth={1} />
              <text x={L - 6} y={y + 4} textAnchor="end" fontSize={11} fill="var(--color-ink-faint)">{n(t)}</text>
            </g>
          );
        })}
        {cols.map((c, i) => {
          const x = L + band * i + (band - bw) / 2;
          const live = c.segs.filter((s) => s.value > 0);
          let cum = 0;
          return (
            <g key={c.id}>
              {hover === i && <rect x={L + band * i} y={T} width={band} height={ih} fill="var(--color-ink)" opacity={0.06} rx={4} />}
              {live.map((s, k) => {
                const h = Math.max(2, (s.value / max) * ih);
                const y = base - cum - h;
                const top = k === live.length - 1;
                const gap = k > 0 ? 2 : 0;                          // the surface gap between stacked segments
                cum += h;
                return <path key={s.platform} d={colPath(x, y, bw, Math.max(1, h - gap), top ? 4 : 0)} fill={COLOUR[s.platform]} />;
              })}
              {i % every === 0 && (
                <text x={L + band * (i + 0.5)} y={H - 7} textAnchor="middle" fontSize={11} fill="var(--color-ink-faint)">{c.tick}</text>
              )}
              <rect x={L + band * i} y={T} width={band} height={ih + B} fill="transparent" tabIndex={0}
                aria-label={`${c.label}: ${totals[i]} submission${totals[i] === 1 ? "" : "s"}`}
                onPointerMove={() => show(i)} onFocus={() => show(i)} onBlur={() => setHover(null)} />
            </g>
          );
        })}
        <line x1={L} x2={W - R} y1={base} y2={base} stroke="var(--color-line-strong, var(--color-line))" strokeWidth={1} />
      </svg>
      {hover !== null && (
        <div className="pointer-events-none absolute top-0 z-10 w-max -translate-x-1/2 rounded-lg border border-line bg-surface px-3 py-2 text-xs shadow-lg"
          style={{ left: tipX }} role="status">
          <p className="text-ink-soft">{cols[hover].label}</p>
          {cols[hover].segs.map((s) => (
            <p key={s.platform} className="mt-0.5 flex items-center gap-2">
              <span aria-hidden className="inline-block h-0.5 w-3" style={{ background: COLOUR[s.platform] }} />
              <span className="font-semibold text-ink">{n(s.value)}</span>
              <span className="text-ink-soft">{NAME[s.platform]}</span>
            </p>
          ))}
          {platforms.length > 1 && <p className="mt-1 border-t border-line pt-1 font-semibold text-ink">{n(totals[hover])} total</p>}
        </div>
      )}
      <details className="mt-2">
        <summary className="cursor-pointer text-sm text-brand">View as table</summary>
        <div className="mt-2 max-h-64 overflow-auto">
          <table className="w-full text-left text-sm">
            <thead className="text-ink-soft">
              <tr>
                <th className="py-1 pr-4 font-medium">Period</th>
                {platforms.map((p) => <th key={p} className="py-1 pr-4 font-medium">{NAME[p]}</th>)}
                <th className="py-1 font-medium">Total</th>
              </tr>
            </thead>
            <tbody>
              {cols.map((c, i) => (
                <tr key={c.id} className="border-t border-line">
                  <td className="py-1 pr-4 text-ink-soft">{c.label}</td>
                  {c.segs.map((s) => <td key={s.platform} className="py-1 pr-4 tabular-nums text-ink">{n(s.value)}</td>)}
                  <td className="py-1 tabular-nums text-ink">{n(totals[i])}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}

/* ---- rating line ----------------------------------------------------------------------------------------------- */

function LineChart({ platform, points }: { platform: Platform; points: RatingPoint[] }) {
  const W = 640, H = 200, L = 44, R = 60, T = 14, B = 26;
  const iw = W - L - R, ih = H - T - B, base = H - B;
  const t = points.map((p) => Date.parse(p.date));
  const t0 = t[0], span = Math.max(1, t[t.length - 1] - t0);
  const lo = Math.min(...points.map((p) => p.rating)), hi = Math.max(...points.map((p) => p.rating));
  const min = Math.floor((lo - 40) / 100) * 100, top = Math.ceil((hi + 40) / 100) * 100;
  const X = (i: number) => L + ((t[i] - t0) / span) * iw;
  const Y = (v: number) => base - ((v - min) / (top - min)) * ih;
  const path = points.map((p, i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(p.rating).toFixed(1)}`).join(" ");
  const peak = points.reduce((b, p, i) => (p.rating > points[b].rating ? i : b), 0);
  const last = points.length - 1;
  const [hover, setHover] = useState<number | null>(null);
  const svg = useRef<SVGSVGElement>(null);
  const ticks = [min, Math.round((min + top) / 200) * 100, top];
  const xTicks = [0, Math.floor(last / 2), last];
  const colour = COLOUR[platform];

  const nearest = (clientX: number) => {
    const r = svg.current!.getBoundingClientRect();
    const x = ((clientX - r.left) / r.width) * W;
    let best = 0;
    for (let i = 1; i < points.length; i++) if (Math.abs(X(i) - x) < Math.abs(X(best) - x)) best = i;
    setHover(best);
  };
  const w = svg.current?.getBoundingClientRect().width ?? W;
  const delta = hover !== null && hover > 0 ? points[hover].rating - points[hover - 1].rating : null;

  return (
    <div className="relative" onPointerLeave={() => setHover(null)}>
      <p className="mb-1 flex items-center gap-2 text-sm font-medium text-ink">
        <span aria-hidden className="inline-block h-0.5 w-4" style={{ background: colour }} />
        {NAME[platform]} <span className="provenance">{points.length} contests</span>
      </p>
      <svg ref={svg} viewBox={`0 0 ${W} ${H}`} className="w-full" role="img"
        aria-label={`${NAME[platform]} rating over ${points.length} contests: from ${points[0].rating} to ${points[last].rating}, peak ${points[peak].rating}`}>
        {ticks.map((v) => (
          <g key={v}>
            <line x1={L} x2={W - R} y1={Y(v)} y2={Y(v)} stroke="var(--color-line)" strokeWidth={1} />
            <text x={L - 6} y={Y(v) + 4} textAnchor="end" fontSize={11} fill="var(--color-ink-faint)">{n(v)}</text>
          </g>
        ))}
        {xTicks.map((i) => (
          <text key={i} x={X(i)} y={H - 7} fontSize={11} fill="var(--color-ink-faint)"
            textAnchor={i === 0 ? "start" : i === last ? "end" : "middle"}>
            {new Date(points[i].date).toLocaleDateString("en", { month: "short", year: "numeric", timeZone: "UTC" })}
          </text>
        ))}
        <path d={`${path} L${X(last)},${base} L${X(0)},${base} Z`} fill={colour} opacity={0.1} />
        <path d={path} fill="none" stroke={colour} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        {peak !== last && (
          <g>
            <circle cx={X(peak)} cy={Y(points[peak].rating)} r={4} fill={colour} stroke="var(--color-surface)" strokeWidth={2} />
            <text x={Math.min(Math.max(X(peak), L + 30), W - R - 30)} y={Y(points[peak].rating) - 9} textAnchor="middle" fontSize={11} fill="var(--color-ink-soft)">
              peak {n(points[peak].rating)}
            </text>
          </g>
        )}
        <circle cx={X(last)} cy={Y(points[last].rating)} r={4.5} fill={colour} stroke="var(--color-surface)" strokeWidth={2} />
        <text x={X(last) + 9} y={Y(points[last].rating) + 4} fontSize={12} fontWeight={600} fill="var(--color-ink)">{n(points[last].rating)}</text>
        {hover !== null && (
          <g pointerEvents="none">
            <line x1={X(hover)} x2={X(hover)} y1={T} y2={base} stroke="var(--color-ink-faint)" strokeWidth={1} />
            <circle cx={X(hover)} cy={Y(points[hover].rating)} r={5} fill={colour} stroke="var(--color-surface)" strokeWidth={2} />
          </g>
        )}
        <rect x={L} y={T} width={iw} height={ih + B} fill="transparent" tabIndex={0}
          onPointerMove={(e) => nearest(e.clientX)} onFocus={() => setHover(last)} onBlur={() => setHover(null)}
          onKeyDown={(e) => {
            if (e.key === "ArrowLeft") setHover((h) => Math.max(0, (h ?? last) - 1));
            if (e.key === "ArrowRight") setHover((h) => Math.min(last, (h ?? last) + 1));
          }} />
      </svg>
      {hover !== null && (
        <div className="pointer-events-none absolute top-6 z-10 w-max -translate-x-1/2 rounded-lg border border-line bg-surface px-3 py-2 text-xs shadow-lg"
          style={{ left: Math.min(Math.max((X(hover) / W) * w, 80), w - 80) }} role="status">
          <p className="text-ink-soft">{points[hover].contest || "Contest"}</p>
          <p className="mt-0.5 flex items-center gap-2">
            <span aria-hidden className="inline-block h-0.5 w-3" style={{ background: colour }} />
            <span className="font-semibold text-ink">{n(points[hover].rating)}</span>
            {delta !== null && <span className="text-ink-soft">{delta > 0 ? "+" : ""}{delta}</span>}
          </p>
          <p className="provenance mt-0.5">{new Date(points[hover].date).toLocaleDateString("en", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" })}</p>
        </div>
      )}
      <details className="mt-1">
        <summary className="cursor-pointer text-sm text-brand">View as table</summary>
        <div className="mt-2 max-h-56 overflow-auto">
          <table className="w-full text-left text-sm">
            <thead className="text-ink-soft"><tr><th className="py-1 pr-4 font-medium">Date</th><th className="py-1 pr-4 font-medium">Contest</th><th className="py-1 font-medium">Rating</th></tr></thead>
            <tbody>
              {[...points].reverse().map((p, i) => (
                <tr key={i} className="border-t border-line">
                  <td className="py-1 pr-4 text-ink-soft">{p.date}</td>
                  <td className="py-1 pr-4 text-ink">{p.contest}</td>
                  <td className="py-1 tabular-nums text-ink">{n(p.rating)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}

/* ---- level mix ------------------------------------------------------------------------------------------------- */

const BAND_ORDER = ["under 1200", "1200-1599", "1600-1999", "2000 and above"];

function MixCard({ rows }: { rows: ProfileRow[] }) {
  return (
    <Card title="Where your solves sit" meta="what you've solved, by level">
      <div className="viz-root space-y-6">
        {rows.map((r) => {
          const s = r.stats!;
          if (r.platform === "leetcode") {
            const parts = [["Easy", s.solved_easy ?? 0, "var(--viz-o1)"], ["Medium", s.solved_medium ?? 0, "var(--viz-o2)"], ["Hard", s.solved_hard ?? 0, "var(--viz-o3)"]] as const;
            const total = parts.reduce((a, p) => a + p[1], 0);
            if (!total) return null;
            return (
              <div key={r.platform}>
                <p className="text-sm font-medium text-ink">LeetCode <span className="provenance">{n(total)} solved</span></p>
                <div className="mt-2 flex h-3 gap-0.5" role="img" aria-label={parts.map((p) => `${p[0]} ${p[1]}`).join(", ")}>
                  {parts.filter((p) => p[1] > 0).map(([label, v, c], i, a) => (
                    <div key={label} title={`${label}: ${v}`} style={{ flex: v, background: c }}
                      className={`${i === 0 ? "rounded-l" : ""} ${i === a.length - 1 ? "rounded-r" : ""}`} />
                  ))}
                </div>
                <ul className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-sm text-ink-soft">
                  {parts.map(([label, v, c]) => (
                    <li key={label} className="flex items-center gap-1.5">
                      <span aria-hidden className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: c }} />
                      {label} <span className="font-medium text-ink">{n(v)}</span> <span className="provenance">{Math.round((100 * v) / total)}%</span>
                    </li>
                  ))}
                </ul>
              </div>
            );
          }
          const bands = BAND_ORDER.filter((b) => s.solved_by_rating?.[b]);
          const max = Math.max(1, ...bands.map((b) => s.solved_by_rating![b]));
          if (!bands.length) return null;
          return (
            <div key={r.platform}>
              <p className="text-sm font-medium text-ink">{NAME[r.platform]} <span className="provenance">by problem rating</span></p>
              <ul className="mt-2 space-y-1.5">
                {bands.map((b) => (
                  <li key={b} className="flex items-center gap-3 text-sm">
                    <span className="w-32 shrink-0 text-ink-soft">{b}</span>
                    <span className="h-3 flex-1 rounded bg-surface-sunken">
                      <span className="block h-3 rounded" style={{ width: `${(100 * s.solved_by_rating![b]) / max}%`, background: "var(--viz-1)" }} />
                    </span>
                    <span className="w-14 text-right font-medium tabular-nums text-ink">{n(s.solved_by_rating![b])}</span>
                  </li>
                ))}
              </ul>
            </div>
          );
        })}
      </div>
    </Card>
  );
}
