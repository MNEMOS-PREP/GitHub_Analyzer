/**
 * M03 · when to ask again about a pull request review, and when to stop waiting. Pure, so it can be checked alone.
 */

export const POLL_MS = 2000;
export const GIVE_UP_MS = 3 * 60 * 1000;

/** The job has an answer (a report or a named error); asking again would not change it. */
export function settled(status: string | undefined): boolean {
  return status === "done" || status === "failed";
}

/**
 * react-query `refetchInterval`: every POLL_MS while the job runs. Stops once it is settled, once the wait has run
 * past GIVE_UP_MS, or after a 4xx (the job or the repository is gone, so asking again cannot help). A network blip
 * or a 5xx keeps polling until the give-up time.
 */
export function nextPoll(status: string | undefined, waitedMs: number, errorStatus?: number): number | false {
  if (settled(status)) return false;
  if (waitedMs >= GIVE_UP_MS) return false;
  if (errorStatus !== undefined && errorStatus >= 400 && errorStatus < 500) return false;
  return POLL_MS;
}

/** True once the wait has run out without an answer. */
export function gaveUp(status: string | undefined, waitedMs: number): boolean {
  return !settled(status) && waitedMs >= GIVE_UP_MS;
}
