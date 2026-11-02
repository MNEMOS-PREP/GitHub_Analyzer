/**
 * M03 · API client. One hook per endpoint the screens use.
 *
 * Plain `fetch`, not `@ai/core`'s `api`: M03 answers errors as
 * `{"error": {code, message, offer}}` (a named error with what the student can
 * do next), and core's wrapper reads `detail` and would flatten that to a
 * status line. Identity comes from the same session, via `sessionHeaders`.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getSession, sessionHeaders } from "@ai/core";

import type { Handles, IdentityOptions, NamedError, ProfileInsight, ProfileQuestion, ProfilesView, RepoView, SecurityNotice } from "./types";

export class M03Error extends Error {
  constructor(
    readonly status: number,
    readonly named: NamedError,
  ) {
    super(named.message);
    this.name = "M03Error";
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...sessionHeaders(), ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    let named: NamedError = { code: "error", message: response.statusText || "Something went wrong.", offer: [] };
    try {
      const body = await response.json();
      if (body?.error) named = { offer: [], ...body.error };
      else if (typeof body?.detail === "string") named = { ...named, message: body.detail };
    } catch {
      /* non-JSON body: keep the status text */
    }
    throw new M03Error(response.status, named);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

const candidate = () => getSession()?.subject ?? "anonymous";

/* A student's analysed repositories. The service has no list route, so the ids
   this browser submitted are kept per student; the records themselves are still
   the service's (and gone once it deletes them). */
const KEY = (who: string) => `m03.repos.${who}`;

export interface KnownRepo {
  id: string;
  label: string;
  at: string;
}

export function knownRepos(): KnownRepo[] {
  try {
    return JSON.parse(localStorage.getItem(KEY(candidate())) ?? "[]");
  } catch {
    return [];
  }
}

function remember(repo: KnownRepo): void {
  try {
    const rest = knownRepos().filter((r) => r.id !== repo.id);
    localStorage.setItem(KEY(candidate()), JSON.stringify([repo, ...rest].slice(0, 20)));
  } catch {
    /* storage can be blocked; the analysis itself is unaffected */
  }
}

function forget(id: string): void {
  try {
    localStorage.setItem(KEY(candidate()), JSON.stringify(knownRepos().filter((r) => r.id !== id)));
  } catch {
    /* see remember() */
  }
}

export function useSubmitRepo() {
  return useMutation({
    mutationFn: async (target: string) => {
      const text = target.trim();
      const body = /^(https?:\/\/|git@)|github\.com/.test(text) ? { url: text } : { repo_id: text };
      const res = await call<{ id: string; status: string; cached: boolean }>("/v1/repos", {
        method: "POST",
        body: JSON.stringify(body),
      });
      remember({
        id: res.id,
        label: text.replace(/^https?:\/\/github\.com\//, "").replace(/\.git$/, ""),
        at: new Date().toISOString(),
      });
      return res;
    },
  });
}

/** Polls while the analysis is still running, then stops. */
export function useRepo(id: string | undefined) {
  return useQuery({
    queryKey: ["m03", "repo", id],
    queryFn: () => call<RepoView>(`/v1/repos/${id}`),
    enabled: !!id,
    retry: false,
    refetchInterval: (q) => (q.state.data && ["queued", "running"].includes(q.state.data.status) ? 1000 : false),
  });
}

export function useIdentities(id: string, enabled: boolean) {
  return useQuery({
    queryKey: ["m03", "identities", id],
    queryFn: () => call<IdentityOptions>(`/v1/repos/${id}/identities`),
    enabled,
    retry: false,
  });
}

export function useConfirmIdentities(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (emails: string[]) =>
      call<RepoView>(`/v1/repos/${id}/identities/confirm`, { method: "POST", body: JSON.stringify({ emails }) }),
    onSuccess: (view) => {
      qc.setQueryData(["m03", "repo", id], view);
      qc.invalidateQueries({ queryKey: ["m03", "identities", id] });
    },
  });
}

export function useConfirmUnderstanding(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (corrected_summary: string | null) =>
      call(`/v1/repos/${id}/understanding/confirm`, { method: "POST", body: JSON.stringify({ corrected_summary }) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["m03", "repo", id] }),
  });
}

export function useSecurityNotices(id: string, enabled: boolean) {
  return useQuery({
    queryKey: ["m03", "notices", id],
    queryFn: () => call<{ notices: SecurityNotice[] }>(`/v1/repos/${id}/security-notices`),
    enabled,
    retry: false,
  });
}

export function useDeleteRepo(id: string) {
  return useMutation({
    mutationFn: () => call<void>(`/v1/repos/${id}`, { method: "DELETE" }),
    onSuccess: () => forget(id),
  });
}

/* ---- coding profiles: Codeforces, LeetCode, CodeChef ---- */

const PROFILES = ["m03", "profiles"] as const;

export function useProfiles() {
  return useQuery({ queryKey: PROFILES, queryFn: () => call<ProfilesView>("/v1/coding-profiles"), retry: false });
}

/** Save the handles, then read them. Reading takes several seconds (Codeforces allows one call per two). */
export function useLinkProfiles() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (handles: Handles) => {
      await call<ProfilesView>("/v1/coding-profiles", { method: "PUT", body: JSON.stringify(handles) });
      return call<ProfilesView>("/v1/coding-profiles/refresh", { method: "POST" });
    },
    onSuccess: (view) => {
      qc.setQueryData(PROFILES, view);
      qc.invalidateQueries({ queryKey: [...PROFILES, "questions"] });
    },
  });
}

export function useVerifyProfiles() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => call<ProfilesView>("/v1/coding-profiles/verify", { method: "POST" }),
    onSuccess: (view) => {
      qc.setQueryData(PROFILES, view);
      qc.invalidateQueries({ queryKey: [...PROFILES, "questions"] });
    },
  });
}

export function useProfileInsights(enabled: boolean) {
  return useQuery({
    queryKey: [...PROFILES, "insights"],
    queryFn: () => call<{ insights: ProfileInsight[] }>("/v1/coding-profiles/insights"),
    enabled,
    retry: false,
  });
}

export function useProfileQuestions(enabled: boolean) {
  return useQuery({
    queryKey: [...PROFILES, "questions"],
    queryFn: () => call<{ questions: ProfileQuestion[] }>("/v1/coding-profiles/questions"),
    enabled,
    retry: false,
  });
}

export function useDeleteProfiles() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => call<void>("/v1/coding-profiles", { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: PROFILES }),
  });
}
