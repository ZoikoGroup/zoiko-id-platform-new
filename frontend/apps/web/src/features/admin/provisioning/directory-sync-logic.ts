import type { DirectorySync } from "@zoiko/api-client";

/** What the cell records when a run fails is a code, never the provider's text. Say what each one means and what to do about it. */
const ERRORS: Record<string, string> = {
  CREDENTIAL_REJECTED: "Auth0 refused the client ID or secret. Replace the secret, and check the client ID.",
  CREDENTIAL_NOT_ALLOWED: "The Auth0 application is not allowed to read users. In Auth0, grant it the read:users permission on the Management API.",
  CREDENTIAL_TOO_BROAD: "The Auth0 application can do more than read users. In Auth0, remove every permission except read:users, so a leaked secret can change nothing.",
  CREDENTIAL_MISSING: "No secret is stored for this sync. Replace the secret.",
  DIRECTORY_TOO_LARGE: "Auth0 lists more than 1,000 users, which this sync cannot read completely. Nothing was changed, because a partial list would look like people who left.",
  DIRECTORY_INCOMPLETE: "Auth0 returned fewer people than it reported. Nothing was changed.",
  EMPTY_DIRECTORY: "Auth0 listed nobody, so nothing was changed. Check the Auth0 application and the tenant, then run it again.",
  MASS_DEACTIVATION: "Auth0 no longer lists many of your people, so nothing was changed. A person should check the directory before this runs again.",
  PROVIDER_UNREACHABLE: "Auth0 could not be reached. It will try again later.",
  PROVIDER_RATE_LIMITED: "Auth0 asked us to slow down. It will try again later.",
  PROVIDER_ERROR: "Auth0 answered with an error. It will try again later.",
  PROVIDER_ADDRESS_REFUSED: "That Auth0 address is not allowed.",
  SOURCE_NOT_ACTIVE: "The provisioning source this sync feeds is not active. Resume it.",
  SOME_PEOPLE_REFUSED: "The sync ran, but some people could not be applied. The rest were.",
  SYNC_ERROR: "The sync failed unexpectedly. It will try again later.",
};

export function syncErrorMessage(code: string | null): string | null {
  return code ? (ERRORS[code] ?? "The last run did not complete.") : null;
}

/** The counts of the last run in words, e.g. "3 created, 1 updated, 12 unchanged, 1 deactivated". */
export function countsSummary(counts: DirectorySync["last_counts"]): string {
  const order: [string, string][] = [
    ["created", "created"],
    ["updated", "updated"],
    ["unchanged", "unchanged"],
    ["deactivated", "deactivated"],
    ["failed", "refused"],
  ];
  const parts = order.filter(([k]) => (counts[k] ?? 0) > 0).map(([k, label]) => `${counts[k]} ${label}`);
  return parts.length ? parts.join(", ") : "no changes";
}

export const INTERVALS: { id: string; label: string; seconds: number }[] = [
  { id: "900", label: "Every 15 minutes", seconds: 900 },
  { id: "3600", label: "Every hour", seconds: 3600 },
  { id: "21600", label: "Every 6 hours", seconds: 21600 },
  { id: "86400", label: "Once a day", seconds: 86400 },
];

/** Auth0 tenant addresses only: `your-tenant.us.auth0.com`. The cell checks it again; this just says so before sending. */
export function isAuth0Domain(v: string): boolean {
  return /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*\.auth0\.com$/i.test(v.trim());
}
