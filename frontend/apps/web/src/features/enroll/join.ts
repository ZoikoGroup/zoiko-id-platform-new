/** The address an invitation email links to: `/join?t=<workspace>_<ticket>`. The email can carry only one token, so the workspace name rides in front of the ticket.
 * A workspace name has no underscore (lower-case letters, digits and inner hyphens), so the first underscore is the join. */

const WORKSPACE = /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/;
const TICKET = /^[A-Za-z0-9_-]{8,512}$/;

export function parseJoinToken(token: string | undefined): { workspace: string; ticket: string } | null {
  if (!token) return null;
  const cut = token.indexOf("_");
  if (cut < 1) return null;
  const workspace = token.slice(0, cut);
  const ticket = token.slice(cut + 1);
  return WORKSPACE.test(workspace) && TICKET.test(ticket) ? { workspace, ticket } : null;
}

/** Where the person goes: the passkey setup page, with the workspace named so the ticket is looked up in the right company. Anything unreadable goes to sign-in, never to an error page. */
export function joinDestination(token: string | undefined): string {
  const parsed = parseJoinToken(token);
  if (!parsed) return "/sign-in";
  return `/enroll?ticket=${encodeURIComponent(parsed.ticket)}&workspace=${encodeURIComponent(parsed.workspace)}`;
}
