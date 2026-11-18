/**
 * M03 · how the files connect: which file imports which, drawn from the import lines only.
 *
 * Every arrow is an import the service read (gh_analyzer/visualise.py); nothing is guessed, so an arrow can be missing
 * but never invented, and the card says when imports could not be followed. Plain SVG, no dependency; colours are the
 * data-viz slots ProfileCharts uses, with the shell's tokens for chrome.
 */

import { useId, useMemo, useState } from "react";

import { Button, Card } from "@ai/core";

import { useDiagrams } from "../api";
import type { DiagramNode, Diagrams, GraphQuality } from "../types";
import { basename, layout, NODE_H, NODE_W } from "./diagramLayout";
import { CSS } from "./ProfileCharts";

const SLOTS = ["var(--viz-1)", "var(--viz-2)", "var(--viz-3)"];
const OTHER = "var(--color-ink-faint)";

const QUALITY: Record<GraphQuality, string> = {
  full: "Arrows come only from import lines we could read in your code. Nothing is guessed.",
  partial: "Imports could not be fully followed for some files, so a few arrows may be missing.",
  tree_only: "Imports could not be followed for the languages in this project, so the files are shown without arrows.",
};

/* visualise.py's own wording for the same facts; shown once, as the sentence above, not twice */
const SAID_BY_QUALITY = [
  "Some languages here were read with less depth, so a few connections may be missing.",
  "I could not read the imports in this repository's languages, so this diagram shows the folder layout only.",
];

export function DiagramsCard({ id, ready }: { id: string; ready: boolean }) {
  const q = useDiagrams(id, ready);
  if (!ready || q.isLoading) return null;
  const d = q.data?.diagrams;
  const nodes = d?.graph?.nodes ?? [];
  if (q.isError || !d || nodes.length === 0) {
    return (
      <Card title="Diagrams">
        <p className="text-sm text-ink-soft">{q.isError ? "Couldn't load the diagram for this analysis." : "No diagram for this analysis."}</p>
      </Card>
    );
  }
  return <Diagram d={d as Diagrams} />;
}

function Diagram({ d }: { d: Diagrams }) {
  const { nodes, edges, quality, truncated } = d.graph;
  const placed = useMemo(() => layout(nodes, edges), [nodes, edges]);
  const colour = useMemo(() => {
    const count = new Map<string, number>();
    for (const n of nodes) count.set(n.group, (count.get(n.group) ?? 0) + 1);
    const ranked = [...count.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(([g]) => g);
    return { of: (g: string) => SLOTS[ranked.indexOf(g)] ?? OTHER, ranked };
  }, [nodes]);
  const [focus, setFocus] = useState<string | null>(null);
  const marker = useId().replace(/:/g, "");
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const notes = (d.notes ?? []).filter((t) => !SAID_BY_QUALITY.includes(t));
  const saysCount = notes.some((t) => t.startsWith("Showing the"));
  const hasBack = placed.edges.some((e) => e.back);
  const hasEntry = nodes.some((n) => n.entry);
  const near = (n: DiagramNode) => edges.filter((e) => e.from === n.id || e.to === n.id).length;

  return (
    <Card title="Diagrams" meta="which file imports which">
      <style>{CSS}</style>
      <div className="viz-root">
        <p className="text-sm text-ink">{QUALITY[quality] ?? QUALITY.partial}</p>
        {truncated && !saysCount && <p className="provenance mt-1">Showing the {nodes.length} most connected files.</p>}
        {notes.length > 0 && (
          <ul className="mt-1 space-y-0.5">
            {notes.map((t) => <li key={t} className="provenance">{t}</li>)}
          </ul>
        )}

        <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-sm text-ink-soft" aria-label="Legend">
          {colour.ranked.slice(0, SLOTS.length).map((g) => (
            <li key={g} className="flex items-center gap-1.5">
              <span aria-hidden className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: colour.of(g) }} />
              <span className="font-mono">{g}</span>
            </li>
          ))}
          {colour.ranked.length > SLOTS.length && (
            <li className="flex items-center gap-1.5">
              <span aria-hidden className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: OTHER }} />
              Other folders
            </li>
          )}
          {hasEntry && (
            <li className="flex items-center gap-1.5">
              <span aria-hidden className="inline-block h-2.5 w-3.5 rounded-sm border-2 border-ink" />
              Where the program starts
            </li>
          )}
          {hasBack && (
            <li className="flex items-center gap-1.5">
              <svg aria-hidden width="18" height="6"><line x1="0" x2="18" y1="3" y2="3" stroke="var(--color-ink-faint)" strokeDasharray="3 3" /></svg>
              Import that loops back
            </li>
          )}
        </ul>

        <div className="mt-3 overflow-x-auto rounded-lg bg-surface-sunken" onPointerLeave={() => setFocus(null)}>
          <svg width={placed.width} height={placed.height} viewBox={`0 0 ${placed.width} ${placed.height}`}
            className="block max-w-none" role="group"
            aria-label={`Import diagram of ${nodes.length} files and ${edges.length} imports. Arrows point from a file to the file it imports.`}>
            <defs>
              <marker id={`${marker}-a`} viewBox="0 0 8 8" refX="7.5" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                <path d="M0,0 L8,4 L0,8 Z" fill="var(--color-ink-faint)" />
              </marker>
              <marker id={`${marker}-h`} viewBox="0 0 8 8" refX="7.5" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                <path d="M0,0 L8,4 L0,8 Z" fill="var(--color-ink)" />
              </marker>
            </defs>
            {placed.looseX !== null && placed.looseX > 16 && (
              <line x1={placed.looseX - 32} x2={placed.looseX - 32} y1={8} y2={placed.height - 8}
                stroke="var(--color-line-strong, var(--color-line))" strokeDasharray="4 4" />
            )}
            {placed.edges.map((e) => {
              const lit = focus !== null && (e.from === focus || e.to === focus);
              return (
                <path key={`${e.from}-${e.to}`} d={e.d} fill="none"
                  stroke={lit ? "var(--color-ink)" : "var(--color-ink-faint)"}
                  strokeWidth={lit ? 1.75 : 1} opacity={focus === null || lit ? 0.9 : 0.25}
                  strokeDasharray={e.back ? "4 3" : undefined}
                  markerEnd={`url(#${marker}-${lit ? "h" : "a"})`} />
              );
            })}
            {nodes.map((n) => {
              const p = placed.nodes.get(n.id);
              if (!p) return null;
              const fill = colour.of(n.group);
              const on = focus === n.id;
              const k = near(n);
              return (
                <g key={n.id} transform={`translate(${p.x},${p.y})`} tabIndex={0} role="img" className="cursor-default outline-none"
                  aria-label={`${n.path}${n.entry ? ", where the program starts" : ""}. Imported by ${n.in_degree} file${n.in_degree === 1 ? "" : "s"}; ${k} arrow${k === 1 ? "" : "s"} shown.`}
                  onFocus={() => setFocus(n.id)} onBlur={() => setFocus(null)} onPointerEnter={() => setFocus(n.id)}>
                  <title>{n.path}</title>
                  <rect width={NODE_W} height={NODE_H} rx={6} fill="var(--color-surface)"
                    stroke={n.entry || on ? "var(--color-ink)" : "var(--color-line-strong, var(--color-line))"}
                    strokeWidth={n.entry ? 2 : on ? 1.5 : 1} />
                  <rect x={0} y={0} width={NODE_W} height={NODE_H} rx={6} fill={fill} opacity={0.14} />
                  <rect x={1} y={4} width={4} height={NODE_H - 8} rx={2} fill={fill} />
                  <text x={12} y={NODE_H / 2 + 4} fontSize={12} fill="var(--color-ink)" fontWeight={n.entry ? 600 : 400}>
                    {basename(n.path)}
                  </text>
                  {on && <rect x={-3} y={-3} width={NODE_W + 6} height={NODE_H + 6} rx={8} fill="none" stroke="var(--color-brand)" strokeWidth={2} />}
                </g>
              );
            })}
          </svg>
        </div>
        {focus !== null && byId.get(focus) && (
          <p className="provenance mt-1 font-mono" role="status">{byId.get(focus)!.path}</p>
        )}

        <div className="mt-3 flex flex-wrap items-start gap-3">
          {d.mermaid?.imports && <CopyMermaid text={d.mermaid.imports} />}
        </div>

        <details className="mt-2">
          <summary className="cursor-pointer text-sm text-brand">View as list</summary>
          <ul className="mt-2 max-h-64 space-y-1 overflow-auto text-sm">
            {nodes.map((n) => {
              const to = edges.filter((e) => e.from === n.id).map((e) => byId.get(e.to)?.path).filter(Boolean);
              return (
                <li key={n.id}>
                  <span className="font-mono text-ink">{n.path}</span>
                  {n.entry && <span className="text-ink-soft"> · starts here</span>}
                  {to.length > 0 && <span className="text-ink-soft"> imports {to.join(", ")}</span>}
                </li>
              );
            })}
          </ul>
        </details>
      </div>
    </Card>
  );
}

function CopyMermaid({ text }: { text: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  const copy = async () => {
    try {
      if (!navigator.clipboard?.writeText) throw new Error("no clipboard");
      await navigator.clipboard.writeText(text);
      setState("copied");
    } catch {
      setState("failed");
    }
  };
  return (
    <div className="w-full">
      <div className="flex items-center gap-3">
        <Button size="sm" variant="secondary" onClick={copy}>Copy as Mermaid</Button>
        {state === "copied" && <span className="text-sm text-ink-soft" role="status">Copied. Paste it into any Mermaid editor.</span>}
        {state === "failed" && <span className="text-sm text-ink-soft" role="status">Couldn't reach the clipboard. Select the text below and copy it.</span>}
      </div>
      {state === "failed" && (
        <textarea readOnly value={text} rows={6} onFocus={(e) => e.currentTarget.select()}
          className="mt-2 w-full rounded-lg border border-line bg-surface px-3 py-2 font-mono text-xs text-ink" />
      )}
    </div>
  );
}
