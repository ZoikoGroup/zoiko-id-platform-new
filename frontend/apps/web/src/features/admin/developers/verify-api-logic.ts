import type { VerifyApiCredential } from "@zoiko/api-client";

/** The plain-language side of the Verify API console (CR-149). The cell validates everything again; this only helps a person fill the form in, names the scopes in words, and shows the
 * code a developer copies. Nothing here handles a secret except to show the snippet with a placeholder. */

export type ScopeInfo = { id: string; label: string; help: string };

export const SCOPES: ScopeInfo[] = [
  { id: "verifications:create", label: "Start a verification", help: "Create a verification for one of your customers and get the link to send them." },
  { id: "verifications:read", label: "Read a verification", help: "See where one verification is: waiting, in progress, done." },
  { id: "verifications:list", label: "List verifications", help: "Search and page through your verifications." },
  { id: "decisions:read", label: "Read the result", help: "Get the outcome of a finished verification. Never includes the photographs." },
  { id: "verifications:resume", label: "Resume a verification", help: "Give a customer a fresh link to finish a verification they left." },
  { id: "verifications:recheck", label: "Ask for a recheck", help: "Ask for a new check of someone already verified." },
  { id: "products:read", label: "See available checks", help: "List the checks you can use in each country." },
  { id: "workflows:read", label: "See your workflows", help: "List the approved verification flows you can pick from." },
  { id: "webhooks:read", label: "Read webhook activity", help: "See delivery history for your webhook endpoints." },
  { id: "webhooks:write", label: "Manage webhook endpoints", help: "Register, test and rotate the secret of your webhook endpoints." },
  { id: "usage:read", label: "Read usage", help: "See how many checks you have used." },
  { id: "applications:submit", label: "Ask for production access", help: "Apply to go live. Not needed to build and test in the sandbox." },
];

/** What a new sandbox application gets when you do not choose: everything needed to run the whole sandbox journey, but not the call that asks for production. */
export const DEFAULT_SCOPES: string[] = SCOPES.filter((s) => s.id !== "applications:submit").map((s) => s.id);

export const scopeLabel = (id: string): string => SCOPES.find((s) => s.id === id)?.label ?? id;

const NAME = /^[A-Za-z0-9][A-Za-z0-9 ._-]{1,78}[A-Za-z0-9]$/;

/** Why a name cannot be used, or null. */
export function nameProblem(name: string): string | null {
  const clean = name.trim().replace(/\s+/g, " ");
  if (clean.length < 3) return "Give the application a name of at least three characters, for example “Checkout website”.";
  if (!NAME.test(clean)) return "Use letters, numbers, spaces, dots, dashes or underscores, starting and ending with a letter or number.";
  return null;
}

/** One origin per line (or separated by commas). Blank lines are dropped and repeats removed. */
export function parseOrigins(text: string): string[] {
  const seen = new Set<string>();
  for (const raw of text.split(/[\n,]/)) {
    const v = raw.trim();
    if (v) seen.add(v);
  }
  return [...seen];
}

/** Why an origin is refused, or null: an exact https origin such as https://app.example.com, with no path, wildcard or credentials. A local address may use http. */
export function originProblem(value: string): string | null {
  let u: URL;
  try {
    u = new URL(value);
  } catch {
    return `${value} is not a web address. Use something like https://app.example.com.`;
  }
  const local = ["localhost", "127.0.0.1", "[::1]"].includes(u.hostname);
  if (value.includes("*")) return `${value} has a wildcard. List each address exactly.`;
  if (u.username || u.password) return `${value} must not contain a user name or password.`;
  if (u.protocol !== "https:" && !(u.protocol === "http:" && local)) return `${value} must start with https://. Only a local address may use http://.`;
  if ((u.pathname !== "/" && u.pathname !== "") || u.search || u.hash) return `${value} must be only the address, with no path, query or fragment: ${u.origin}.`;
  return null;
}

export const firstOriginProblem = (origins: string[]): string | null => {
  for (const o of origins) {
    const p = originProblem(o);
    if (p) return p;
  }
  return origins.length > 20 ? "At most 20 addresses." : null;
};

export type CredentialState = { label: string; tone: "ok" | "warn" | "danger" | "neutral" };

/** A credential in words: ACTIVE is in use, ROTATING still works until its overlap ends, EXPIRED and REVOKED do not work. */
export function credentialState(c: Pick<VerifyApiCredential, "status">): CredentialState {
  switch (c.status) {
    case "ACTIVE":
      return { label: "Active", tone: "ok" };
    case "ROTATING":
      return { label: "Being replaced", tone: "warn" };
    case "EXPIRED":
      return { label: "Expired", tone: "neutral" };
    default:
      return { label: "Revoked", tone: "danger" };
  }
}

export const canIssue = (status: string): boolean => status === "ACTIVE";

/** The first call a developer makes. The base address is the console's own API address; the secret is a placeholder, never a real value. */
export function tokenSnippet(base: string, clientId: string): string {
  return [
    `curl -X POST ${base}/v1/verify-api/oauth2/token \\`,
    `  -H "Content-Type: application/json" \\`,
    `  -d '{"grant_type":"client_credentials","client_id":"${clientId}","client_secret":"<your secret>"}'`,
  ].join("\n");
}

export function whoamiSnippet(base: string): string {
  return [`curl ${base}/v1/verify-api/whoami \\`, `  -H "Authorization: Bearer <access_token from the call above>"`].join("\n");
}

/** What the person must do before the one-time secret can be closed: copy it. The dialog does not let them dismiss it by accident. */
export const SECRET_WARNING = "This is the only time this secret is shown. Copy it now and store it where your server can read it. If you lose it, create a new credential.";
