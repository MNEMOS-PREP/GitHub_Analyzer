/**
 * M03 · coding profiles: Codeforces, LeetCode, CodeChef.
 *
 * The student links usernames; we read the public profile and turn the numbers
 * into questions about the problems behind them. Same rules as the repository
 * screens: no score, no ranking against anyone, and a site we cannot read is
 * "unavailable", which says something about the site and nothing about them.
 */

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { Button, Card, EmptyState, ErrorNote, Spinner } from "@ai/core";

import {
  M03Error,
  useDeleteProfiles,
  useLinkProfiles,
  useProfileInsights,
  useProfileQuestions,
  useProfiles,
  useVerifyProfiles,
} from "../api";
import { ProfileCharts } from "./ProfileCharts";
import type { Handles, InsightKind, Platform, ProfileInsight, ProfileQuestion, ProfileRow, ProfileStats } from "../types";

const PLATFORMS: { id: Platform; name: string; placeholder: string }[] = [
  { id: "codeforces", name: "Codeforces", placeholder: "username or codeforces.com/profile/…" },
  { id: "leetcode", name: "LeetCode", placeholder: "username or leetcode.com/u/…" },
  { id: "codechef", name: "CodeChef", placeholder: "username or codechef.com/users/…" },
];

/** Statistics first. The questions are built and served (/v1/coding-profiles/questions); flip this to show them. */
const SHOW_QUESTIONS = false;

const NAME: Record<Platform, string> = { codeforces: "Codeforces", leetcode: "LeetCode", codechef: "CodeChef" };

/** Only the numbers the platform actually gave; an absent one is left out, not shown as zero. */
function statRows(platform: Platform, s: ProfileStats): [string, string][] {
  const rows: [string, string | null | undefined][] = [];
  const n = (v: number | null | undefined) => (v == null ? null : v.toLocaleString());
  if (platform === "codeforces") {
    rows.push(["Rating", s.rating != null ? `${s.rating}${s.rank ? ` · ${s.rank}` : ""}` : null]);
    rows.push(["Peak rating", n(s.max_rating)]);
    rows.push(["Problems solved", s.solved_total != null ? `${s.solved_total.toLocaleString()}${s.truncated ? "+" : ""}` : null]);
    rows.push(["Hardest solved", s.hardest_solved_rating ? `rated ${s.hardest_solved_rating}` : null]);
    rows.push(["Contests", n(s.contests)]);
  } else if (platform === "leetcode") {
    rows.push(["Problems solved", n(s.solved_total)]);
    rows.push(["Easy / Medium / Hard", s.solved_total != null ? `${s.solved_easy ?? 0} / ${s.solved_medium ?? 0} / ${s.solved_hard ?? 0}` : null]);
    rows.push(["Contest rating", n(s.contest_rating)]);
    rows.push(["Contests", s.contest_rating != null ? n(s.contests) : null]);
    rows.push(["Current streak", s.streak != null ? `${s.streak} days` : null]);
  } else {
    rows.push(["Rating", s.rating != null ? `${s.rating}${s.stars ? ` · ${s.stars}★` : ""}` : null]);
    rows.push(["Peak rating", n(s.max_rating)]);
    rows.push(["Problems solved", n(s.solved_total)]);
    rows.push(["Contests", n(s.contests)]);
  }
  rows.push(["Active days, last year", n(s.active_days_last_year)]);
  rows.push(["Last active", s.last_active]);
  return rows.filter((r): r is [string, string] => !!r[1]);
}

export function ProfilesPage() {
  const profiles = useProfiles();
  const link = useLinkProfiles();
  const verify = useVerifyProfiles();
  const remove = useDeleteProfiles();
  const rows = profiles.data?.profiles ?? [];
  const insights = useProfileInsights(rows.some((r) => r.stats));
  const questions = useProfileQuestions(SHOW_QUESTIONS && rows.some((r) => r.stats));

  const [handles, setHandles] = useState<Record<Platform, string>>({ codeforces: "", leetcode: "", codechef: "" });
  const [filled, setFilled] = useState(false);
  useEffect(() => {
    // Fill the form from what is already linked, once; after that the student's typing is theirs.
    if (profiles.data && !filled) {
      setHandles((h) => ({ ...h, ...Object.fromEntries(profiles.data.profiles.map((p) => [p.platform, p.handle])) }));
      setFilled(true);
    }
  }, [profiles.data, filled]);

  const save = (event: React.FormEvent) => {
    event.preventDefault();
    const body: Handles = {};
    for (const p of PLATFORMS) {
      const typed = handles[p.id].trim();
      if (typed || rows.some((r) => r.platform === p.id)) body[p.id] = typed;     // "" unlinks a platform
    }
    verify.reset();
    link.mutate(body);
  };

  const busy = link.isPending || verify.isPending;
  const named = (e: unknown) => (e instanceof M03Error ? e.named : null);
  const failure = named(link.error) ?? named(verify.error);
  const unverified = rows.filter((r) => !r.verified && r.verification);

  return (
    <div className="mx-auto max-w-3xl">
      <Link to="/github" className="text-sm text-ink-soft hover:underline">← Repositories</Link>
      <p className="rule-label mt-2">Coding profiles</p>
      <h1 className="mt-1 text-title font-semibold text-ink">{SHOW_QUESTIONS ? "Questions about the problems you've solved" : "Your coding profiles"}</h1>
      <p className="mt-2 max-w-2xl text-ink-soft">
        Add the usernames you use on any of these sites. We read the public profile and show the numbers the site
        itself shows{SHOW_QUESTIONS && ", then turn them into questions an interviewer could ask about your thinking"}.
        This never scores you, never compares you with anyone, and a site you leave out changes nothing.
      </p>

      <form onSubmit={save} className="mt-6 space-y-3">
        {PLATFORMS.map((p) => (
          <label key={p.id} className="flex flex-col gap-1 sm:flex-row sm:items-center sm:gap-3">
            <span className="w-28 shrink-0 text-sm font-medium text-ink">{p.name}</span>
            <input
              value={handles[p.id]}
              onChange={(e) => setHandles({ ...handles, [p.id]: e.target.value })}
              placeholder={p.placeholder}
              aria-label={`${p.name} username`}
              className="min-w-0 flex-1 rounded-lg border border-line bg-surface px-3 py-2 text-ink"
            />
          </label>
        ))}
        <div className="flex items-center gap-3 pt-1">
          <Button type="submit" disabled={busy || PLATFORMS.every((p) => !handles[p.id].trim() && !rows.length)}>
            {link.isPending ? "Reading…" : "Save and read"}
          </Button>
          {link.isPending && <span className="provenance">This can take about ten seconds.</span>}
        </div>
      </form>

      {failure && (
        <div className="mt-4">
          <ErrorNote message={failure.message} />
        </div>
      )}
      {(link.isError || verify.isError) && !failure && (
        <div className="mt-4"><ErrorNote message="That didn't go through. Try again." /></div>
      )}

      <div className="mt-8 space-y-6">
        {profiles.isLoading && <Spinner label="Opening your profiles…" />}
        {profiles.isError && <ErrorNote message="Couldn't load your profiles. Try again in a moment." />}
        {profiles.data && rows.length === 0 && !link.isPending && (
          <EmptyState title="No profiles linked yet" icon="search">
            Add a username above. You can use one site, or all three.
          </EmptyState>
        )}

        {rows.map((row) => <ProfileCard key={row.platform} row={row} />)}

        <ProfileCharts rows={rows} />

        {(insights.data?.insights ?? []).length > 0 && (
          <Card title="What the numbers show">
            <InsightList insights={insights.data!.insights} />
          </Card>
        )}

        {unverified.length > 0 && (
          <Card title="Show these are yours">
            <p className="text-sm text-ink-soft">
              Until we find a short code on your profile, the numbers are shown as self-reported. Copy the code into
              the place named, save it on the site, then check. You can remove it afterwards.
            </p>
            <ul className="mt-3 space-y-3">
              {unverified.map((r) => r.verification && (
                <li key={r.platform} className="text-sm">
                  <span className="font-medium text-ink">{NAME[r.platform]}: </span>
                  <code className="rounded bg-surface-sunken px-1.5 py-0.5 font-mono text-ink">{r.verification.code}</code>
                  <p className="provenance mt-1">{r.verification.where}</p>
                </li>
              ))}
            </ul>
            <div className="mt-4">
              <Button size="sm" variant="secondary" disabled={busy} onClick={() => verify.mutate()}>
                {verify.isPending ? "Checking…" : "Check now"}
              </Button>
              {verify.isSuccess && verify.data.profiles.some((r) => !r.verified) && (
                <span className="ml-3 text-sm text-ink-soft">Not found yet. A profile change can take a minute to show.</span>
              )}
            </div>
          </Card>
        )}

        {SHOW_QUESTIONS && (questions.data?.questions ?? []).length > 0 && (
          <Card title="Questions from your profiles" meta={`${questions.data!.questions.length} questions`}>
            <QuestionList questions={questions.data!.questions} />
          </Card>
        )}

        {rows.length > 0 && profiles.data && (
          <p className="provenance">{profiles.data.summary.note}</p>
        )}

        {rows.length > 0 && (
          <div className="border-t border-line pt-4">
            <Button size="sm" variant="ghost" disabled={remove.isPending}
              onClick={() => { remove.mutate(); setHandles({ codeforces: "", leetcode: "", codechef: "" }); }}>
              Remove all my coding profiles
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * Ownership state as the platform draws verification state everywhere else: confirmed is green, reported
 * (not independently confirmed) is amber, and the words carry the meaning, never the colour alone.
 */
const PILL = "inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium";
function Pill({ tone, children }: { tone: "confirmed" | "reported" | "neutral"; children: React.ReactNode }) {
  const tones = {
    confirmed: "border-confirmed/30 bg-confirmed-soft text-confirmed",
    reported: "border-reported/30 bg-reported-soft text-reported",
    neutral: "border-line bg-surface text-ink-soft",
  };
  return <span className={`${PILL} ${tones[tone]}`}>{children}</span>;
}

function ProfileCard({ row }: { row: ProfileRow }) {
  const stats = row.stats ? statRows(row.platform, row.stats) : [];
  const tags = row.stats?.top_tags ?? [];
  const meta = (
    <span className="flex items-center gap-2">
      {row.stale && row.fetched_at && <Pill tone="neutral">from {new Date(row.fetched_at).toLocaleDateString()}, may have changed</Pill>}
      {row.status === "not_found" ? (
        <Pill tone="neutral">Not found</Pill>
      ) : row.verified ? (
        <Pill tone="confirmed">Confirmed yours</Pill>
      ) : (
        <Pill tone="reported">Self-reported</Pill>
      )}
    </span>
  );
  return (
    <Card title={`${NAME[row.platform]} · ${row.handle}`} meta={meta}>
      {stats.length > 0 && (
        <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-3">
          {stats.map(([label, value]) => (
            <div key={label}>
              <dt className="text-xs text-ink-soft">{label}</dt>
              <dd className="font-medium text-ink">{value}</dd>
            </div>
          ))}
        </dl>
      )}
      {tags.length > 0 && (
        <p className="mt-3 text-sm text-ink-soft">
          Most-solved topics: {tags.map((t) => `${t.tag} (${t.solved})`).join(" · ")}
        </p>
      )}
      {row.status === "pending" && <p className="text-sm text-ink-soft">Saved. Press “Save and read” to load the numbers.</p>}
      {row.status === "partial" && (
        <p className="mt-3 text-sm text-ink-soft">Only some of this profile could be read. What's shown is accurate; the rest is missing, not zero.</p>
      )}
      {row.error && (
        <p className="mt-3 text-sm text-reported">
          {row.error.message}
          {row.stale && " The numbers above are from the last time it worked."}
          {row.error.code === "not_found"
            ? " Check the spelling, or paste your profile link."
            : " Nothing about this changes anything else."}
        </p>
      )}
      <p className="provenance mt-3">
        {row.fetched_at ? `Read ${new Date(row.fetched_at).toLocaleString()}` : "Not read yet"}
        {row.profile_url && <> · <a href={row.profile_url} target="_blank" rel="noreferrer noopener" className="text-brand hover:underline">Open profile</a></>}
      </p>
    </Card>
  );
}

/** One question per card line; the gentler version is shown only when it is asked for. */
function QuestionList({ questions }: { questions: ProfileQuestion[] }) {
  return (
    <ol className="space-y-5">
      {questions.map((q) => (
        <li key={q.id}>
          <p className="provenance">{NAME[q.platform]} · {q.handle}{q.verified ? "" : " · self-reported"}</p>
          <p className="mt-1 text-ink">{q.question}</p>
          <details className="mt-2">
            <summary className="cursor-pointer text-sm text-brand">A gentler version</summary>
            <p className="mt-2 border-l-2 border-line pl-3 text-sm text-ink-soft">If you don't remember: {q.fallback_if_unanswered}</p>
          </details>
        </li>
      ))}
    </ol>
  );
}

const KIND: Record<InsightKind, string> = { strength: "Stands out", pattern: "Pattern", worth_a_look: "Worth a look" };

/**
 * Grouped by site, the across-sites reading first. The kind is a plain word, not a colour: green and amber on this
 * platform mean "confirmed" and "reported", and an insight is neither.
 */
function InsightList({ insights }: { insights: ProfileInsight[] }) {
  const groups = [...new Set(insights.map((i) => i.platform))];
  return (
    <div className="space-y-6">
      {groups.map((g) => (
        <section key={g}>
          <p className="rule-label">{g === "all" ? "Across your sites" : `${NAME[g]} · ${insights.find((i) => i.platform === g)?.handle ?? ""}`}</p>
          <ul className="mt-2 divide-y divide-line">
            {insights.filter((i) => i.platform === g).map((i) => (
              <li key={i.id} className="py-3 first:pt-1">
                <p className="provenance">{KIND[i.kind]}</p>
                <p className="mt-0.5 font-medium text-ink">{i.headline}</p>
                <p className="mt-0.5 text-sm text-ink-soft">{i.body}</p>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
