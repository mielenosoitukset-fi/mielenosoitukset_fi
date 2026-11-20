// Where keyboard and screen reader focus goes after a confirm button is used. A
// failure (network, rate limit, stale session) leaves the button usable, so focus
// returns to it once it is enabled again; a final answer replaces the page content,
// so focus moves to the new heading, which announces it.
const retry = new Set(['error', 'rate-limited', 'session']);
const final = new Set([
  'success', 'invalid', 'expired', 'confirmed', 'user-not-found', 'joined',
  'not-invited', 'organization-not-found', 'protected', 'signed-out',
]);

export type FocusTarget = 'button' | 'heading' | null;

/** `outcome` is the answer the page just received, or null before any answer. */
export function focusTargetFor(outcome: string | null): FocusTarget {
  if (outcome === null) return null;
  if (retry.has(outcome)) return 'button';
  return final.has(outcome) ? 'heading' : null;
}
