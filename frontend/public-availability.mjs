// The status is deliberately independent of event/config caches. An explicit
// lockdown answer applies at once and is sticky: a failed read never reopens a
// site that was locked. A failed or timed-out read (Go API restarting, database
// outage) keeps the last explicit "available" answer for 15 minutes
// (PUBLIC_GATE_STALE_SECONDS), then closes. A process that never had an answer
// stays open. The local emergency override closes the gate on every failed read
// (PUBLIC_LOCKDOWN_OVERRIDE=closed, or the file PUBLIC_LOCKDOWN_OVERRIDE_FILE,
// default /etc/mielenosoitukset/lockdown-override, exists). See docs/decisions.md.
import { existsSync } from 'node:fs';

const staleSeconds = Number.parseInt(process.env.PUBLIC_GATE_STALE_SECONDS ?? '', 10);
export const AVAILABILITY_GRACE_MS = (staleSeconds > 0 ? staleSeconds : 900) * 1000;

export function lockdownOverride(env = process.env, exists = existsSync) {
  if (env.PUBLIC_LOCKDOWN_OVERRIDE === 'closed') return true;
  return exists(env.PUBLIC_LOCKDOWN_OVERRIDE_FILE || '/etc/mielenosoitukset/lockdown-override');
}

export function availabilityReader(apiReadBase, fetcher = fetch, now = Date.now, override = lockdownOverride, log = console.error) {
  let expires = 0;
  let available = false;
  let lastOpenAt = 0;
  let answered = false;
  let everLocked = false;
  let failures = 0;
  let lastLogAt = 0;
  let pending;
  return async function read() {
    if (now() < expires) return available;
    if (!pending) pending = (async () => {
      let answer;
      try {
        const response = await fetcher(`${apiReadBase}/public-status`, {
          signal: AbortSignal.timeout(1200), headers: { Accept: 'application/json' },
        });
        const data = response.ok ? await response.json() : null;
        if (typeof data?.available === 'boolean') answer = data.available;
      } catch { /* No answer; decided below. */ }
      if (answer === undefined) {
        failures += 1;
        if (now() - lastLogAt >= 30_000) {
          lastLogAt = now();
          log(`public gate: status read failed (${failures} since start, answered=${answered}, locked=${everLocked})`);
        }
        if (override() || everLocked) available = false;
        else if (!answered) available = true;
        else available = now() - lastOpenAt <= AVAILABILITY_GRACE_MS;
      } else {
        answered = true;
        everLocked = answer === false;
        lastOpenAt = answer ? now() : 0;
        available = answer;
      }
      expires = now() + 1000;
      return available;
    })().finally(() => { pending = null; });
    return pending;
  };
}
