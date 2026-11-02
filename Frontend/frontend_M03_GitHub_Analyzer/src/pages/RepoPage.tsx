/**
 * M03 · the front door: paste a repository link.
 *
 * The same page is the no-repo path (FR-3.18): a student with nothing to
 * paste is told that is fine, and nothing about the rest of the platform
 * waits on it. A missing repo never changes a score.
 */

import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { Button, Card, ErrorNote } from "@ai/core";

import { M03Error, knownRepos, useSubmitRepo } from "../api";

export function RepoPage() {
  const [target, setTarget] = useState("");
  const submit = useSubmitRepo();
  const navigate = useNavigate();
  const earlier = knownRepos();

  const go = (event: React.FormEvent) => {
    event.preventDefault();
    if (!target.trim()) return;
    submit.mutate(target, { onSuccess: (res) => navigate(`/github/${res.id}`) });
  };

  const named = submit.error instanceof M03Error ? submit.error.named : null;

  return (
    <div className="mx-auto max-w-3xl">
      <p className="rule-label">GitHub</p>
      <h1 className="mt-1 text-title font-semibold text-ink">Questions about your own code</h1>
      <p className="mt-2 max-w-2xl text-ink-soft">
        Paste a link to a repository you built. We read it without running it, find the decisions you
        made in it, and turn them into questions an interviewer could ask. This never scores you, and a
        repository you leave out changes nothing.
      </p>

      <form onSubmit={go} className="mt-6 flex flex-col gap-3 sm:flex-row">
        <input
          value={target}
          onChange={(e) => setTarget(e.target.value)}
          placeholder="https://github.com/you/your-project"
          aria-label="Repository link"
          className="min-w-0 flex-1 rounded-lg border border-line bg-surface px-3 py-2 text-ink"
        />
        <Button type="submit" disabled={!target.trim() || submit.isPending}>
          {submit.isPending ? "Reading…" : "Analyse"}
        </Button>
      </form>

      {named && (
        <div className="mt-4">
          <ErrorNote
            message={named.message}
            detail={named.offer.includes("continue_without_repo") ? "You can carry on without a repository." : undefined}
          />
        </div>
      )}
      {submit.isError && !named && <div className="mt-4"><ErrorNote message="That didn't go through. Try again." /></div>}

      <p className="mt-4 text-sm text-ink-soft">
        Also solve problems on Codeforces, LeetCode or CodeChef?{" "}
        <Link to="/github/profiles" className="text-brand hover:underline">Add those profiles</Link>
      </p>

      <div className="mt-10">
        <Card title="Earlier analyses">
          {earlier.length === 0 ? (
            <p className="text-sm text-ink-soft">Nothing yet. Paste a link above to start.</p>
          ) : (
            <ul className="divide-y divide-line">
              {earlier.map((r) => (
                <li key={r.id} className="flex items-baseline justify-between gap-4 py-2">
                  <Link to={`/github/${r.id}`} className="font-medium text-brand hover:underline">
                    {r.label}
                  </Link>
                  <span className="provenance">{new Date(r.at).toLocaleDateString()}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}
