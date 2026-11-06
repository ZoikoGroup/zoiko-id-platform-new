/** Where a step may send the person on when it finishes (`?next=`). Only a path inside this console is followed: anything else (another site, a
 * protocol-relative address, a backslash trick) is dropped, so the parameter can never be an open redirect. */
export function safeNext(raw: string | null | undefined): string | null {
  if (!raw || !raw.startsWith("/") || raw.startsWith("//") || raw.includes("\\") || /[\u0000-\u001f]/.test(raw)) return null;
  return raw;
}

/** The identity-verification page address that returns to `to` when the check completes. */
export function verifyThenGo(to: string): string {
  return `/account/verification?next=${encodeURIComponent(to)}`;
}
