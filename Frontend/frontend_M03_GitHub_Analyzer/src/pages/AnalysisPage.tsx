/**
 * M03 · one repository: what was found, who wrote it, and the questions it makes.
 *
 * Order is the order a student should trust it: the read-back first (so a
 * wrong reading is caught before anything is built on it), then whose commits
 * are theirs, then the questions. Nothing here is a verdict.
 */

import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { Button, Card, EmptyState, ErrorNote, Spinner } from "@ai/core";

import {
  M03Error,
  useConfirmIdentities,
  useConfirmUnderstanding,
  useDeleteRepo,
  useIdentities,
  useRepo,
  useSecurityNotices,
} from "../api";
import type { Question, RepoView } from "../types";

const DEPTH_LABEL = ["Opening", "Why", "What if", "Under pressure"];

function percent(n: number) {
  return `${Math.round(n * 100)}%`;
}

export function AnalysisPage() {
  const { repoId = "" } = useParams();
  const repo = useRepo(repoId);

  if (repo.isLoading) return <Spinner label="Opening this analysis…" />;
  if (repo.isError || !repo.data) {
    const gone = repo.error instanceof M03Error && repo.error.status === 404;
    return (
      <div className="mx-auto max-w-2xl">
        <EmptyState title={gone ? "This analysis is no longer here" : "Couldn't open this analysis"} icon="alert"
          action={<Link to="/github" className="text-brand hover:underline">Analyse a repository</Link>}>
          {gone ? "It may have been deleted, or it belongs to someone else." : "Try again in a moment."}
        </EmptyState>
      </div>
    );
  }

  const view = repo.data;
  return (
    <div className="mx-auto max-w-3xl">
      <Link to="/github" className="text-sm text-ink-soft hover:underline">← All repositories</Link>
      <h1 className="mt-2 text-title font-semibold text-ink">{view.repo ?? "Repository"}</h1>
      {view.pinned_sha && <p className="provenance mt-1">Read at commit {view.pinned_sha}</p>}

      {(view.status === "queued" || view.status === "running") && (
        <div className="mt-6">
          <Spinner label={view.progress.length ? `Reading… (${view.progress[view.progress.length - 1]})` : "Queued…"} />
        </div>
      )}
      {view.status === "failed" && <Failed view={view} />}
      {view.status === "empty" && (
        <div className="mt-6">
          <EmptyState title="Nothing to ask about yet" icon="search">
            {view.message ?? "There wasn't enough code here to find decisions in."}
          </EmptyState>
        </div>
      )}
      {view.status === "ready" && <Ready view={view} />}
    </div>
  );
}

function Failed({ view }: { view: RepoView }) {
  return (
    <div className="mt-6">
      <ErrorNote message={view.message ?? "We couldn't read this repository."} />
      {view.offer?.includes("continue_without_repo") && (
        <p className="mt-3 text-sm text-ink-soft">You can carry on without it. Nothing else changes.</p>
      )}
    </div>
  );
}

function Ready({ view }: { view: RepoView }) {
  const inv = view.inventory;
  const u = view.understanding;
  const c = view.contribution;
  const questions = [...(view.questions ?? []), ...(view.supplementary_questions ?? [])];

  return (
    <div className="mt-6 space-y-6">
      {u && <ReadBack view={view} />}

      {inv && (
        <Card title="What's in it">
          <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-4">
            <Stat label="Lines of code" value={inv.loc_total.toLocaleString()} />
            <Stat label="Files read" value={`${inv.analysed_files} of ${inv.total_files}`} />
            <Stat label="Tests" value={inv.has_tests ? `${inv.test_loc.toLocaleString()} lines` : "None found"} />
            <Stat label="CI / Docker" value={`${inv.has_ci ? "CI" : "no CI"} · ${inv.has_dockerfile ? "Docker" : "no Docker"}`} />
          </dl>
        </Card>
      )}

      {c && <Contribution view={view} />}

      <Card title="Questions from your own code" meta={`${new Set(questions.map((q) => q.decision_id)).size} decisions`}>
        {questions.length === 0 ? (
          <p className="text-sm text-ink-soft">No decisions stood out enough to ask about.</p>
        ) : (
          <QuestionLadders questions={questions} />
        )}
      </Card>

      {(view.notes ?? []).length > 0 && (
        <ul className="list-disc space-y-1 pl-5 text-sm text-ink-soft">
          {view.notes!.map((n) => <li key={n}>{n}</li>)}
        </ul>
      )}

      <Notices id={view.id} />
      <Remove id={view.id} />
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-ink-soft">{label}</dt>
      <dd className="font-medium text-ink">{value}</dd>
    </div>
  );
}

/** AC-3.7: the read-back is confirmed or corrected by the student, in their own words. */
function ReadBack({ view }: { view: RepoView }) {
  const u = view.understanding!;
  const confirm = useConfirmUnderstanding(view.id);
  const [fixing, setFixing] = useState(false);
  const [text, setText] = useState("");
  const done = view.understanding_state?.confirmed;
  const langs = Object.keys(u.languages).join(", ") || "unknown languages";

  return (
    <Card title="Did we read this right?">
      <p className="text-sm text-ink">
        This looks like a <strong>{langs}</strong> project
        {u.frameworks.length > 0 && <> using <strong>{u.frameworks.join(", ")}</strong></>}
        {Object.keys(u.top_level).length > 0 && <>, mostly in <span className="font-mono">{Object.keys(u.top_level).slice(0, 3).join(", ")}</span></>}.
      </p>
      {done ? (
        <p className="mt-3 text-sm text-ink-soft">
          {view.understanding_state?.correction ? `Your correction: ${view.understanding_state.correction}` : "You confirmed this."}
        </p>
      ) : fixing ? (
        <div className="mt-3 space-y-2">
          <textarea value={text} onChange={(e) => setText(e.target.value)} rows={3} maxLength={1000}
            placeholder="What it actually is, in your words"
            className="w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink" />
          <div className="flex gap-2">
            <Button size="sm" disabled={!text.trim() || confirm.isPending} onClick={() => confirm.mutate(text)}>Save correction</Button>
            <Button size="sm" variant="ghost" onClick={() => setFixing(false)}>Cancel</Button>
          </div>
        </div>
      ) : (
        <div className="mt-3 flex gap-2">
          <Button size="sm" disabled={confirm.isPending} onClick={() => confirm.mutate(null)}>Yes, that's right</Button>
          <Button size="sm" variant="secondary" onClick={() => setFixing(true)}>Not quite</Button>
        </div>
      )}
    </Card>
  );
}

/** FR-3.6: a range, with the method stated; never a point, and never invented from squashed history. */
function Contribution({ view }: { view: RepoView }) {
  const c = view.contribution!;
  const options = useIdentities(view.id, c.identity_candidates.length > 0);
  const confirm = useConfirmIdentities(view.id);
  const [picked, setPicked] = useState<string[] | null>(null);
  const suggested = options.data?.suggested ?? [];
  const chosen = picked ?? (view.identities_confirmed?.length ? view.identities_confirmed : suggested);

  const toggle = (email: string) =>
    setPicked(chosen.includes(email) ? chosen.filter((e) => e !== email) : [...chosen, email]);

  return (
    <Card title="How much of it is yours">
      <p className="text-sm text-ink">
        {c.range
          ? `Roughly ${percent(c.range[0])} to ${percent(c.range[1])} of the code history is yours.`
          : c.statement}
      </p>
      {c.cadence && (
        <p className="provenance mt-1">
          {c.active_days} active days over {c.span_days} · {c.cadence} · {c.other_contributors} other contributors
        </p>
      )}
      {options.data && options.data.candidates.length > 0 && (
        <div className="mt-4">
          <p className="text-sm font-medium text-ink">Which of these are you?</p>
          <ul className="mt-2 space-y-1">
            {options.data.candidates.map((id) => (
              <li key={id.email}>
                <label className="flex cursor-pointer items-baseline gap-2 text-sm">
                  <input type="checkbox" checked={chosen.includes(id.email)} onChange={() => toggle(id.email)} />
                  <span className="text-ink">{id.names.join(", ")}</span>
                  <span className="provenance">{id.email} · {id.commits} commits</span>
                </label>
              </li>
            ))}
          </ul>
          <div className="mt-3">
            <Button size="sm" disabled={chosen.length === 0 || confirm.isPending} onClick={() => confirm.mutate(chosen)}>
              {confirm.isPending ? "Working it out…" : "These are mine"}
            </Button>
            {confirm.error instanceof M03Error && (
              <p className="mt-2 text-sm text-contested">{confirm.error.named.message}</p>
            )}
          </div>
        </div>
      )}
    </Card>
  );
}

/** Each decision is a four-step ladder; the fallback is shown only when it is asked for. */
function QuestionLadders({ questions }: { questions: Question[] }) {
  const byDecision = new Map<string, Question[]>();
  for (const q of questions) byDecision.set(q.decision_id, [...(byDecision.get(q.decision_id) ?? []), q]);

  return (
    <ol className="space-y-5">
      {[...byDecision.entries()].map(([id, steps]) => {
        const ordered = [...steps].sort((a, b) => a.depth - b.depth);
        return (
          <li key={id}>
            <p className="provenance font-mono">{ordered[0].citation}</p>
            <p className="mt-1 text-ink">{ordered[0].question}</p>
            <details className="mt-2">
              <summary className="cursor-pointer text-sm text-brand">Follow-ups and a gentler version</summary>
              <ul className="mt-2 space-y-2 border-l-2 border-line pl-3 text-sm">
                {ordered.slice(1).map((q) => (
                  <li key={q.depth}>
                    <span className="text-ink-soft">{DEPTH_LABEL[q.depth] ?? `Step ${q.depth + 1}`}: </span>
                    <span className="text-ink">{q.question}</span>
                  </li>
                ))}
                <li className="text-ink-soft">If you don't remember: {ordered[0].fallback_if_unanswered}</li>
              </ul>
            </details>
          </li>
        );
      })}
    </ol>
  );
}

/** Private to the student: the only place a committed secret is ever mentioned. */
function Notices({ id }: { id: string }) {
  const notices = useSecurityNotices(id, true);
  const list = notices.data?.notices ?? [];
  if (list.length === 0) return null;
  return (
    <Card title="Only you can see this">
      <p className="text-sm text-ink-soft">These look like secrets committed to the repository. Rotate them and keep them out of git.</p>
      <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-ink">
        {list.map((n, i) => (
          <li key={i}>{String(n.message ?? n.notice ?? JSON.stringify(n))}</li>
        ))}
      </ul>
    </Card>
  );
}

function Remove({ id }: { id: string }) {
  const remove = useDeleteRepo(id);
  const navigate = useNavigate();
  const [sure, setSure] = useState(false);
  return (
    <div className="border-t border-line pt-4">
      {sure ? (
        <div className="flex items-center gap-3 text-sm">
          <span className="text-ink-soft">Delete this analysis and the copy we kept?</span>
          <Button size="sm" variant="secondary" disabled={remove.isPending}
            onClick={() => remove.mutate(undefined, { onSuccess: () => navigate("/github") })}>Delete</Button>
          <Button size="sm" variant="ghost" onClick={() => setSure(false)}>Keep</Button>
        </div>
      ) : (
        <Button size="sm" variant="ghost" onClick={() => setSure(true)}>Delete this analysis</Button>
      )}
    </div>
  );
}
