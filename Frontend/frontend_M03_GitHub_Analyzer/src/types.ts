/** M03 · shapes of /v1 responses (gh_analyzer/service.py `student_view`). */

export type RepoStatus = "queued" | "running" | "ready" | "empty" | "failed";

export interface IdentityCandidate {
  email: string;
  names: string[];
  commits: number;
}

export interface Decision {
  id: string;
  path: string;
  line_start: number;
  line_end: number;
  snippet: string;
  kind: string;
  summary: string;
  detector: string;
  interest_score: number;
}

export interface Question {
  decision_id: string;
  /** 0 = the opening question, 3 = the deepest follow-up. */
  depth: number;
  question: string;
  expects: string[];
  citation: string;
  fallback_if_unanswered: string;
}

export interface Contribution {
  statement: string;
  history_usable: boolean;
  /** A range, never a point; null when it cannot be told. */
  range: [number, number] | null;
  cadence: string | null;
  active_days: number;
  span_days: number;
  primary_areas: string[];
  other_contributors: number;
  identity_candidates: IdentityCandidate[];
}

export interface Inventory {
  total_files: number;
  analysed_files: number;
  excluded_files: number;
  loc_total: number;
  languages: Record<string, number>;
  frameworks: string[];
  has_tests: boolean;
  test_loc: number;
  has_ci: boolean;
  has_dockerfile: boolean;
  has_readme: boolean;
  licence: string | null;
}

export interface Understanding {
  languages: Record<string, number>;
  frameworks: string[];
  entry_points: string[];
  top_level: Record<string, number>;
  needs_confirmation: boolean;
}

export interface RepoView {
  id: string;
  status: RepoStatus;
  repo: string | null;
  pinned_sha: string | null;
  cached: boolean;
  progress: string[];
  offer?: string[];
  message?: string;
  understanding?: Understanding;
  understanding_state?: { confirmed: boolean; accepted_without_correction: boolean; correction: string | null } | null;
  inventory?: Inventory;
  contribution?: Contribution;
  decisions?: Decision[];
  questions?: Question[];
  supplementary_questions?: Question[];
  notes?: string[];
  identities_confirmed?: string[];
}

export interface IdentityOptions {
  candidates: IdentityCandidate[];
  suggested: string[];
  confirmed: string[];
}

export type SecurityNotice = Record<string, unknown>;

/* ---- diagrams (gh_analyzer/visualise.py `diagrams`; `{}` for older analyses) ---- */

export interface DiagramNode {
  id: string;
  path: string;
  /** The top-level folder, or "(root)". */
  group: string;
  in_degree: number;
  entry: boolean;
}

/** `from` imports `to`. Only edges read from import lines; never inferred. */
export interface DiagramEdge {
  from: string;
  to: string;
}

/** full: every language's imports were read · partial: some were not · tree_only: none were, so no arrows. */
export type GraphQuality = "full" | "partial" | "tree_only";

export interface Diagrams {
  graph: { nodes: DiagramNode[]; edges: DiagramEdge[]; quality: GraphQuality; truncated: boolean };
  /** Mermaid `flowchart LR` text, labels already escaped. */
  mermaid: Partial<Record<"modules" | "imports" | "entry_flow", string>>;
  notes: string[];
}

/* ---- pull request review (gh_analyzer/agents_app.py; the report is gh_analyzer/agents/pr_agent.py `_result`) ---- */

/** `GET /v1/github/pr-helper/status`. `reason` is plain words for the student when the helper is off. */
export interface AgentStatus {
  pr: { available: boolean; reason: string | null };
}

export interface PrReviewRequest {
  base: string;
  head: string;
  verify: boolean;
}

export interface PrReviewStart {
  job_id: string;
  status: "running";
}

export interface PrChange {
  path: string;
  what: string;
}

/** Every item cites at least one evidence id from `PrReport.evidence`. */
export interface PrCheckItem {
  item: string;
  why: string;
  evidence: string[];
}

/** A tool call the reviewer made: the tool's name and its arguments only, never what it returned. */
export interface PrEvidence {
  tool: string;
  args: Record<string, unknown>;
}

/** ok: a review · no_changes: the branches do not differ · incomplete: the review could not be finished. */
export type PrReportStatus = "ok" | "no_changes" | "incomplete";

export interface PrReport {
  status: PrReportStatus;
  summary: string;
  changes: PrChange[];
  checklist: PrCheckItem[];
  suggested_title: string;
  suggested_description: string;
  findings: { claim: string; severity: string; evidence: string[] }[];
  /** Items left out because nothing that was read backed them. */
  dropped: number;
  verified: boolean;
  evidence: Record<string, PrEvidence>;
  tokens: { prompt: number; completion: number; total: number; tier1?: number };
  notes: string[];
}

export type PrJobStatus = "running" | "done" | "failed";

/** `GET /v1/repos/{id}/agents/pr/{job_id}`. */
export interface PrReviewJob {
  status: PrJobStatus;
  report: PrReport | null;
  error: NamedError | null;
}

/** The named error the service sends: what happened, and what the student can do next. */
export interface NamedError {
  code: string;
  message: string;
  offer: string[];
}

/* ---- coding profiles (gh_analyzer/coding_profiles.py `CodingProfiles.view`) ---- */

export type Platform = "codeforces" | "leetcode" | "codechef" | "hackerrank";

/** Every stat is optional: a platform gives what it gives, and a missing number is never filled in. */
export type ProfileStats = Partial<{
  rating: number | null;
  max_rating: number | null;
  rank: string | null;
  stars: number | null;
  contests: number | null;
  solved_total: number | null;
  solved_easy: number;
  solved_medium: number;
  solved_hard: number;
  hardest_solved_rating: number | null;
  contest_rating: number | null;
  top_percent: number | null;
  streak: number | null;
  top_tags: { tag: string; solved: number }[];
  active_days_last_year: number | null;
  last_active: string | null;
  truncated: boolean;
  /** Per day, UTC, the last year. LeetCode counts submissions; Codeforces also says how many problems were first solved. */
  daily: DayCount[];
  /** One point per rated contest, oldest first. */
  rating_history: RatingPoint[];
  solved_by_rating: Record<string, number>;
  /** HackerRank: one entry per badge track with something in it. */
  badges: { badge: string; stars: number; solved: number }[];
  challenges_available: number | null;
  track_rank: number | null;
  longest_break_days: number;
  active_days_last_30: number;
  active_days_last_90: number;
}>;

export interface DayCount {
  date: string;
  submissions: number;
  solved?: number;
}

export interface RatingPoint {
  date: string;
  rating: number;
  contest: string;
}

export interface ProfileRow {
  platform: Platform;
  handle: string;
  verified: boolean;
  /** ok | partial | pending (linked, not read yet) | not_found | unavailable */
  status: string;
  stats: ProfileStats | null;
  profile_url: string | null;
  fetched_at: string | null;
  /** The numbers shown are the last good read, and the latest attempt failed. */
  stale: boolean;
  error: NamedError | null;
  verification?: { code: string; where: string; note: string };
}

export interface ProfilesView {
  profiles: ProfileRow[];
  summary: { linked: number; readable: number; problems_solved_total: number; note: string };
}

export interface ProfileQuestion {
  id: string;
  platform: Platform;
  handle: string;
  verified: boolean;
  depth: number;
  question: string;
  fallback_if_unanswered: string;
  evidence: Record<string, unknown>;
}

export type Handles = Partial<Record<Platform, string>>;

export type InsightKind = "strength" | "pattern" | "worth_a_look";

/** A plain-sentence reading of the numbers, with the figures behind it. Descriptive; never a mark. */
export interface ProfileInsight {
  id: string;
  platform: Platform | "all";
  handle?: string;
  kind: InsightKind;
  headline: string;
  body: string;
  evidence: Record<string, unknown>;
}
