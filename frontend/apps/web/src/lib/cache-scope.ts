/** Whose data is in the console's cache (ZID-CP-WF10, WF-10.02).
 *
 * Everything the console has fetched belongs to one signed-in person in one company. If that identity changes without a full page reload (signing in again in the same tab, an expired session
 * replaced by another person's), the old company's screens must not stay readable: usage, keys, applications and sessions are flushed the moment the identity differs. The session itself is kept
 * so the new identity can be read. Signing out is a full reload and already clears everything.
 */

export type IdentitySession = { authenticated: true; subject: { sub: string; tenantId?: string } } | { authenticated: false } | undefined;

/** A stable name for "this person in this company", or null when nobody is signed in (or the session has not loaded). */
export function identityOf(session: IdentitySession): string | null {
  if (!session || !session.authenticated) return null;
  return `${session.subject.tenantId ?? "-"}|${session.subject.sub}`;
}

/** Must the cache be flushed when the identity goes from `previous` to `next`? Only when a signed-in identity is replaced by a different one, or signed out; first load never flushes. */
export function mustFlush(previous: string | null, next: string | null): boolean {
  return previous !== null && previous !== next;
}

/** Query keys that survive a flush: the session itself. Everything else is the previous identity's data. */
export const KEEP_ON_FLUSH = ["session"];

export function isForeign(queryKey: readonly unknown[]): boolean {
  return !KEEP_ON_FLUSH.includes(String(queryKey[0]));
}
