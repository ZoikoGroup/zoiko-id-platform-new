/** The plain-language side of the stronger credentials (CR-158): an IP allowlist and a private-key credential. The cell checks everything again; this only helps a person fill the form in. */

const V4 = /^(\d{1,3})(\.\d{1,3}){3}(\/\d{1,2})?$/;
const V6 = /^[0-9a-fA-F:]+(\/\d{1,3})?$/;

/** One entry per line (or separated by commas). Blank lines are dropped and repeats removed. */
export function parseAllowlist(text: string): string[] {
  const seen = new Set<string>();
  for (const raw of text.split(/[\n,]/)) {
    const v = raw.trim();
    if (v) seen.add(v);
  }
  return [...seen];
}

/** Why one entry is refused, or null. Looks only at the shape: the cell decides what is a real address. */
export function addressProblem(entry: string): string | null {
  if (entry === "0.0.0.0/0" || entry === "::/0" || /\/0$/.test(entry)) return "An entry that allows every address is not an allowlist. Leave the list empty for no restriction.";
  if (V4.test(entry)) {
    const [addr, prefix] = entry.split("/");
    if (addr!.split(".").some((p) => Number(p) > 255)) return `${entry} is not an IP address.`;
    if (prefix !== undefined && Number(prefix) > 32) return `${entry} has a network size over 32.`;
    return null;
  }
  if (entry.includes(":") && V6.test(entry)) {
    const prefix = entry.split("/")[1];
    return prefix !== undefined && Number(prefix) > 128 ? `${entry} has a network size over 128.` : null;
  }
  return `${entry} is not an IP address or network. Use something like 203.0.113.7 or 203.0.113.0/24.`;
}

export const MAX_ALLOWLIST = 20;

/** Why the list cannot be saved, or null. */
export function allowlistProblem(entries: string[]): string | null {
  if (entries.length > MAX_ALLOWLIST) return `At most ${MAX_ALLOWLIST} entries.`;
  for (const e of entries) {
    const p = addressProblem(e);
    if (p) return p;
  }
  return null;
}

const PRIVATE_MEMBERS = ["d", "p", "q", "dp", "dq", "qi", "k", "oth"];

export type PublicKeyCheck = { jwk: Record<string, string> | null; problem: string | null };

/** Reads the public key a person pasted. A key that carries private material is refused outright, before it is ever sent, with the advice to treat that private key as exposed. */
export function parsePublicKey(text: string): PublicKeyCheck {
  const raw = text.trim();
  if (!raw) return { jwk: null, problem: null };
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return { jwk: null, problem: "That is not valid JSON. Paste the public key as a JSON Web Key, starting with {." };
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return { jwk: null, problem: "The public key is one JSON object." };
  const key = parsed as Record<string, unknown>;
  if (PRIVATE_MEMBERS.some((m) => m in key)) {
    return { jwk: null, problem: "That key contains private key material. Send only the public key, and treat that private key as exposed." };
  }
  if (!Object.values(key).every((v) => typeof v === "string")) return { jwk: null, problem: "Every part of the key is text." };
  const k = key as Record<string, string>;
  const ok = (k.kty === "EC" && k.crv === "P-256" && k.x && k.y) || (k.kty === "OKP" && k.crv === "Ed25519" && k.x);
  if (!ok) return { jwk: null, problem: "The key must be an EC P-256 or an Ed25519 public key." };
  return { jwk: k, problem: null };
}

/** The signed-assertion call a developer copies. Placeholders only: the private key never goes in a page or a message. */
export function assertionSnippet(base: string, clientId: string): string {
  return [
    "# 1. Sign a short assertion with YOUR PRIVATE KEY (ES256 or EdDSA). Claims:",
    `#    iss = sub = ${clientId}`,
    `#    aud = ${base}/v1/verify-api/oauth2/token`,
    "#    exp = now + 2 minutes (at most 5), jti = a new random id each time",
    "# 2. Exchange it for a token:",
    `curl -X POST ${base}/v1/verify-api/oauth2/token \\`,
    "  -d grant_type=client_credentials \\",
    "  -d client_assertion_type=urn:ietf:params:oauth:client-assertion-type:jwt-bearer \\",
    "  -d client_assertion=<THE_SIGNED_ASSERTION>",
  ].join("\n");
}

export const KEY_CREDENTIAL_NOTE =
  "A key credential has no secret. Your server signs a short assertion with its own private key, so we hold nothing that could leak. Each assertion works once.";
