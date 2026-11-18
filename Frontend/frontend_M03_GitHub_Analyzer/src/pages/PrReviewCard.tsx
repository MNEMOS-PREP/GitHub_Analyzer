/**
 * M03 · review a branch before the student opens a pull request.
 *
 * The helper reads the change between two branches and explains it: what changed, what is worth checking, and a
 * suggested title and description. It suggests; the student decides and opens the pull request themselves. Every
 * checklist item cites the tool calls it rests on (E1, E2, ...), shown as the tool and its arguments only. Model text
 * is rendered as React text, never as HTML. The job id lives in this component's state: a reload loses the result.
 */

import { useEffect, useId, useState } from "react";

import { Button, Card, ErrorNote, Spinner } from "@ai/core";

import { M03Error, useAgentStatus, usePrReview, useStartPrReview } from "../api";
import { settled } from "../prPolling";
import type { PrEvidence, PrReport } from "../types";

const TITLE = "Review a branch before you open a pull request";
const PRIVACY = "This sends short, redacted excerpts of your changes to the language model this site is set up with.";

export function PrReviewCard({ id }: { id: string }) {
  const status = useAgentStatus();
  if (status.isLoading) return null;
  const pr = status.data?.pr;
  if (!pr?.available) {
    return (
      <Card title={TITLE}>
        <p className="text-sm text-ink-soft">{pr?.reason ?? "The pull request helper isn't available here right now."}</p>
      </Card>
    );
  }
  return <PrReview id={id} />;
}

function PrReview({ id }: { id: string }) {
  const f = useId();
  const start = useStartPrReview(id);
  const [head, setHead] = useState("");
  const [base, setBase] = useState("");
  const [verify, setVerify] = useState(true);
  const [jobId, setJobId] = useState<string | null>(null);
  const { job, gaveUp } = usePrReview(id, jobId);

  const data = job.data;
  const lost = job.error instanceof M03Error && job.error.status >= 400 && job.error.status < 500;
  const running = start.isPending || ((jobId !== null || data?.status === "running") && !settled(data?.status) && !gaveUp && !lost);
  const canStart = head.trim() !== "" && base.trim() !== "" && !running;

  const begin = () => {
    if (!canStart) return;
    setJobId(null);
    start.mutate({ base: base.trim(), head: head.trim(), verify }, { onSuccess: (r) => setJobId(r.job_id) });
  };

  const field = "mt-1 w-full rounded-lg border border-line bg-surface px-3 py-2 font-mono text-sm text-ink";
  return (
    <Card title={TITLE}>
      <p className="text-sm text-ink">
        Get a list of things worth checking in your branch before you open its pull request. It explains and suggests;
        you decide what to change, and you open the pull request yourself.
      </p>

      <form className="mt-4 space-y-3" onSubmit={(e) => { e.preventDefault(); begin(); }}>
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <label htmlFor={`${f}-head`} className="text-sm font-medium text-ink">Branch with your changes (head)</label>
            <input id={`${f}-head`} value={head} onChange={(e) => setHead(e.target.value)} placeholder="feature/login"
              autoComplete="off" spellCheck={false} maxLength={200} className={field} />
          </div>
          <div>
            <label htmlFor={`${f}-base`} className="text-sm font-medium text-ink">Branch it goes into (base)</label>
            <input id={`${f}-base`} value={base} onChange={(e) => setBase(e.target.value)} placeholder="main"
              autoComplete="off" spellCheck={false} maxLength={200} className={field} />
          </div>
        </div>
        <label className="flex cursor-pointer items-start gap-2 text-sm text-ink">
          <input type="checkbox" className="mt-0.5" checked={verify} onChange={(e) => setVerify(e.target.checked)} />
          <span>Double-check each item (uses more tokens)</span>
        </label>
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" size="sm" disabled={!canStart}>{running ? "Reviewing…" : "Start review"}</Button>
        </div>
        <p className="provenance">{PRIVACY}</p>
      </form>

      <div className="mt-4" aria-live="polite">
        {running && <Spinner label="Reading your changes. This can take a minute or two." />}
        {start.isError && (
          <ErrorNote message={start.error instanceof M03Error ? start.error.named.message : "Couldn't start the review. Try again in a moment."} />
        )}
        {!running && data?.status === "failed" && (
          <ErrorNote message={data.error?.message ?? "The review could not be finished. Try again later."} onRetry={begin} />
        )}
        {!running && lost && <ErrorNote message="This review is no longer available. Start it again." onRetry={begin} />}
        {gaveUp && (
          <ErrorNote message="This is taking longer than expected, so we stopped waiting. Try again in a few minutes." onRetry={begin} />
        )}
        {!running && data?.status === "done" && data.report && <Report report={data.report} onRetry={begin} />}
      </div>
    </Card>
  );
}

function Report({ report, onRetry }: { report: PrReport; onRetry: () => void }) {
  if (report.status === "no_changes") {
    return <p className="text-sm text-ink">No differences found between these branches.</p>;
  }
  if (report.status === "incomplete") {
    return (
      <div className="space-y-2">
        <p className="text-sm font-medium text-ink">The review couldn't be finished</p>
        {report.summary && <p className="text-sm text-ink-soft">{report.summary}</p>}
        <Button size="sm" variant="secondary" onClick={onRetry}>Try again</Button>
        <Provenance report={report} />
      </div>
    );
  }
  return <FullReport report={report} />;
}

function FullReport({ report }: { report: PrReport }) {
  const ev = useId().replace(/:/g, "");
  const [showEvidence, setShowEvidence] = useState(false);
  const [target, setTarget] = useState<string | null>(null);
  const [ticked, setTicked] = useState<number[]>([]);
  const evidence = Object.entries(report.evidence ?? {});

  useEffect(() => {
    if (!target) return;
    const el = document.getElementById(`${ev}-${target}`);
    el?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
    el?.focus();
  }, [target, ev]);

  const open = (e: string) => {
    setShowEvidence(true);
    setTarget(null);
    requestAnimationFrame(() => setTarget(e));
  };

  return (
    <div className="space-y-5">
      {report.summary && <p className="text-sm text-ink">{report.summary}</p>}

      {report.changes.length > 0 && (
        <section>
          <h3 className="text-sm font-semibold text-ink">What changed</h3>
          <ul className="mt-2 space-y-1.5 text-sm">
            {report.changes.map((c) => (
              <li key={c.path} className="break-words">
                <code className="break-all rounded bg-surface-sunken px-1 font-mono text-ink">{c.path}</code>
                {c.what && <span className="text-ink-soft"> {c.what}</span>}
              </li>
            ))}
          </ul>
        </section>
      )}

      {report.checklist.length > 0 && (
        <section>
          <h3 className="text-sm font-semibold text-ink">Things to check before you open the pull request</h3>
          <ul className="mt-2 space-y-3">
            {report.checklist.map((c, i) => {
              const box = `${ev}-item-${i}`;
              return (
                <li key={i} className="text-sm">
                  <div className="flex items-start gap-2">
                    <input id={box} type="checkbox" className="mt-0.5" checked={ticked.includes(i)}
                      onChange={() => setTicked(ticked.includes(i) ? ticked.filter((n) => n !== i) : [...ticked, i])} />
                    <label htmlFor={box} className="cursor-pointer text-ink">{c.item}</label>
                  </div>
                  <div className="ml-6 mt-1 space-y-1">
                    {c.why && <p className="text-ink-soft"><span className="font-medium">Why it matters: </span>{c.why}</p>}
                    {c.evidence.length > 0 && (
                      <p className="flex flex-wrap items-center gap-1.5 text-ink-soft">
                        <span>Based on</span>
                        {c.evidence.map((e) => (
                          <button key={e} type="button" onClick={() => open(e)} aria-label={`Show what ${e} read`}
                            className="rounded-full border border-line bg-surface-sunken px-2 py-0.5 font-mono text-xs text-brand hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-brand">
                            {e}
                          </button>
                        ))}
                      </p>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      {(report.suggested_title || report.suggested_description) && (
        <section className="space-y-3">
          <h3 className="text-sm font-semibold text-ink">A starting point for your pull request</h3>
          <p className="provenance">Edit these into your own words before you use them.</p>
          {report.suggested_title && (
            <Suggestion label="Suggested title" text={report.suggested_title} />
          )}
          {report.suggested_description && (
            <Suggestion label="Suggested description" text={report.suggested_description} />
          )}
        </section>
      )}

      {evidence.length > 0 && (
        <details open={showEvidence} onToggle={(e) => setShowEvidence(e.currentTarget.open)}>
          <summary className="cursor-pointer text-sm text-brand">Evidence ({evidence.length})</summary>
          <p className="provenance mt-1">What the helper looked at: each tool it used and what it asked for.</p>
          <ul className="mt-2 space-y-1 text-sm">
            {evidence.map(([eid, e]) => (
              <li key={eid} id={`${ev}-${eid}`} tabIndex={-1}
                className={`rounded px-1 outline-none ${target === eid ? "bg-surface-sunken ring-2 ring-brand" : ""}`}>
                <span className="font-mono text-ink">{eid}</span>
                <span className="text-ink-soft"> · </span>
                <span className="font-mono text-ink">{e.tool}</span>
                <span className="break-all text-ink-soft"> {describeArgs(e)}</span>
              </li>
            ))}
          </ul>
        </details>
      )}

      <Provenance report={report} />
    </div>
  );
}

function describeArgs(e: PrEvidence): string {
  const parts = Object.entries(e.args ?? {}).map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`);
  return parts.length ? parts.join(", ") : "(no arguments)";
}

function Provenance({ report }: { report: PrReport }) {
  const lines = [...(report.notes ?? [])];
  if (report.dropped > 0) {
    lines.push(`${report.dropped} item${report.dropped === 1 ? "" : "s"} left out because they couldn't be backed by what was read.`);
  }
  if (report.tokens) lines.push(`Tokens used: ${report.tokens.total.toLocaleString()}`);
  if (lines.length === 0) return null;
  return (
    <ul className="space-y-0.5">
      {lines.map((t) => <li key={t} className="provenance">{t}</li>)}
    </ul>
  );
}

function Suggestion({ label, text }: { label: string; text: string }) {
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
    <div>
      <div className="flex flex-wrap items-center gap-3">
        <p className="text-sm font-medium text-ink">{label}</p>
        <Button size="sm" variant="secondary" onClick={copy} aria-label={`Copy ${label.toLowerCase()}`}>Copy</Button>
        {state === "copied" && <span className="text-sm text-ink-soft" role="status">Copied</span>}
        {state === "failed" && (
          <span className="text-sm text-ink-soft" role="status">Couldn't reach the clipboard. Select the text below and copy it.</span>
        )}
      </div>
      <p className="mt-1 whitespace-pre-wrap break-words rounded-lg border border-line bg-surface-sunken px-3 py-2 text-sm text-ink">
        {text}
      </p>
    </div>
  );
}
