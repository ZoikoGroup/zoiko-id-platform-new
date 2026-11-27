/**
 * Getting an API client's PUBLIC key into the registration form (CR-141).
 *
 * A customer makes their own key pair, keeps the private half and gives us the public half. They may hold it as a key set (JWKS), a single JWK, or the PEM file
 * `openssl ... -pubout` writes. This turns any of those into the one shape the server takes, adds the `kid` when it is missing (the RFC 7638 thumbprint, so it is stable
 * and the same key always gets the same name), and refuses anything that carries a secret or a remote reference. The server checks all of it again and never trusts this.
 *
 * Nothing here sends anything. A private key is refused before it leaves the page and is never repeated back.
 */

export type Jwk = Record<string, string>;
export type Jwks = { keys: Jwk[] };
export type KeyResult = { jwks?: Jwks; error?: string; note?: string };

const PRIVATE_MEMBERS = ["d", "p", "q", "dp", "dq", "qi", "oth", "k"];
const REMOTE_MEMBERS = ["x5u", "jku", "x5c"];
const KEPT_MEMBERS = ["kty", "crv", "x", "y", "n", "e", "kid", "alg", "use"];
const MIN_RSA_BITS = 2048;

export const PRIVATE_MESSAGE = "That contains private material. Paste the PUBLIC key only, and do not share the private half with anyone.";

function b64url(bytes: ArrayBuffer | Uint8Array): string {
  const u = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  let s = "";
  for (const b of u) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function fromB64url(v: string): Uint8Array {
  const s = v.replace(/-/g, "+").replace(/_/g, "/");
  const bin = atob(s + "=".repeat((4 - (s.length % 4)) % 4));
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
}

function subtle(): SubtleCrypto {
  const s = globalThis.crypto?.subtle;
  if (!s) throw new Error("This browser cannot handle keys here. Use a current browser over https, or paste a key set that already has a kid.");
  return s;
}

/** RFC 7638: SHA-256 over the required members in lexicographic order, no whitespace, base64url without padding. Matches the server's. */
export async function thumbprint(jwk: Jwk): Promise<string> {
  const members: Record<string, string[]> = { EC: ["crv", "kty", "x", "y"], OKP: ["crv", "kty", "x"], RSA: ["e", "kty", "n"] };
  const names = members[jwk.kty ?? ""];
  if (!names || names.some((m) => typeof jwk[m] !== "string")) throw new Error("incomplete key");
  const canonical = JSON.stringify(Object.fromEntries(names.map((m) => [m, jwk[m]])));
  return b64url(await subtle().digest("SHA-256", new TextEncoder().encode(canonical)));
}

/** Why one key may not be registered, in words a person can act on. Mirrors the server's rules; the server decides. */
function keyProblem(k: Jwk): string | null {
  if (PRIVATE_MEMBERS.some((m) => m in k)) return PRIVATE_MESSAGE;
  if (REMOTE_MEMBERS.some((m) => m in k)) return "A key must be written out in full here, not fetched from a web address or taken from a certificate. Paste the public key itself.";
  if (k.use === "enc" || ["none", "HS256", "HS384", "HS512"].includes(k.alg ?? "")) return "That is not a signing key. An agent signs with a private key and we check with the public half.";
  if (k.kty === "EC") return ["P-256", "P-384"].includes(k.crv ?? "") && typeof k.x === "string" && typeof k.y === "string" ? null : "Elliptic-curve keys must be P-256 or P-384.";
  if (k.kty === "OKP") return k.crv === "Ed25519" && typeof k.x === "string" ? null : "Ed25519 is the only key of that kind we accept.";
  if (k.kty === "RSA") {
    try {
      let bytes = fromB64url(String(k.n));
      while (bytes.length && bytes[0] === 0) bytes = bytes.slice(1);
      return bytes.length * 8 >= MIN_RSA_BITS && typeof k.e === "string" ? null : "RSA keys must be at least 2048 bits.";
    } catch {
      return "That RSA key is not readable.";
    }
  }
  return "Use an elliptic-curve key (P-256 or P-384), Ed25519, or RSA of 2048 bits or more.";
}

/** Refuse a private key in the browser too, so a secret is never even sent. Synchronous and shape-only; `normalizePublicKey` is the one the form uses. */
export function jwksProblem(raw: string): { jwks?: { keys: Record<string, string>[] }; error?: string } {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return { error: "That is not valid JSON. Paste the public key set, for example {\"keys\": [ ... ]}." };
  }
  const keys = (parsed as { keys?: unknown }).keys;
  if (!Array.isArray(keys) || keys.length < 1 || keys.length > 5) return { error: "A key set has 1 to 5 keys under \"keys\"." };
  for (const k of keys as Record<string, unknown>[]) {
    if (PRIVATE_MEMBERS.some((m) => m in k)) return { error: "That key contains private material. Paste the PUBLIC key only. Do not share the private half with anyone." };
    if (typeof k.kid !== "string" || !k.kid) return { error: "Every key needs a \"kid\" (a name for it), so a rotation can be traced." };
  }
  return { jwks: parsed as { keys: Record<string, string>[] } };
}

function pemBody(pem: string, label: string): Uint8Array | null {
  const m = pem.match(new RegExp(`-----BEGIN ${label}-----([A-Za-z0-9+/=\\s]+)-----END ${label}-----`));
  if (!m?.[1]) return null;
  try {
    const bin = atob(m[1].replace(/\s+/g, ""));
    return Uint8Array.from(bin, (c) => c.charCodeAt(0));
  } catch {
    return null;
  }
}

/** A PEM public key (what `openssl ... -pubout` writes) as a JWK. Tries each kind we accept; the browser does the parsing. */
async function pemToJwk(der: Uint8Array): Promise<Jwk | null> {
  const tries: (AlgorithmIdentifier | EcKeyImportParams | RsaHashedImportParams)[] = [
    { name: "ECDSA", namedCurve: "P-256" },
    { name: "ECDSA", namedCurve: "P-384" },
    { name: "Ed25519" },
    { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" },
  ];
  for (const algorithm of tries) {
    try {
      const key = await subtle().importKey("spki", der as BufferSource, algorithm, true, ["verify"]);
      return (await subtle().exportKey("jwk", key)) as Jwk;
    } catch {
      /* not this kind: try the next */
    }
  }
  return null;
}

async function tidy(k: Jwk): Promise<Jwk> {
  const out: Jwk = {};
  for (const m of KEPT_MEMBERS) if (typeof k[m] === "string") out[m] = k[m];
  if (!out.kid) out.kid = await thumbprint(out);
  return out;
}

/**
 * Whatever the customer pasted, as a checked public key set. Accepts a key set, a single key, or a PEM public key.
 * Adds a kid where one is missing and reports that it did. Returns an error, never a partial result.
 */
export async function normalizePublicKey(raw: string): Promise<KeyResult> {
  const text = raw.trim();
  if (!text) return { error: "Paste the public key. A key set, a single key, or the PEM public key file all work." };
  if (/-----BEGIN [A-Z ]*PRIVATE KEY-----/.test(text) || /-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----/.test(text)) return { error: PRIVATE_MESSAGE };
  if (/-----BEGIN CERTIFICATE-----/.test(text)) return { error: "That is a certificate. Paste the public key itself (the file made with -pubout), not a certificate." };

  let keys: Jwk[];
  let note: string | undefined;
  if (text.startsWith("-----BEGIN")) {
    const der = pemBody(text, "PUBLIC KEY");
    if (!der) return { error: "That PEM is not a public key. It should begin with -----BEGIN PUBLIC KEY-----." };
    const jwk = await pemToJwk(der).catch(() => null);
    if (!jwk) return { error: "We could not read that public key. Use an elliptic-curve key (P-256 or P-384), Ed25519, or RSA of 2048 bits or more." };
    keys = [jwk];
    note = "Converted from a PEM public key.";
  } else {
    let parsed: unknown;
    try {
      parsed = JSON.parse(text);
    } catch {
      return { error: "That is not a key we can read. Paste a key set ({\"keys\": [ ... ]}), a single public key, or the PEM public key." };
    }
    const obj = parsed as { keys?: unknown; kty?: unknown };
    if (Array.isArray(obj.keys)) {
      if (obj.keys.length < 1 || obj.keys.length > 5) return { error: "A key set has 1 to 5 keys under \"keys\"." };
      if (Object.keys(obj).some((m) => m !== "keys")) return { error: "A key set has only \"keys\" in it." };
      keys = obj.keys as Jwk[];
    } else if (typeof obj.kty === "string") {
      keys = [obj as Jwk];
      note = "Wrapped a single key into a key set.";
    } else {
      return { error: "That is not a key we can read. Paste a key set ({\"keys\": [ ... ]}), a single public key, or the PEM public key." };
    }
  }

  const out: Jwk[] = [];
  for (const k of keys) {
    if (!k || typeof k !== "object") return { error: "A key in the set is not readable." };
    const problem = keyProblem(k);
    if (problem) return { error: problem };
    let tidy1: Jwk;
    try {
      tidy1 = await tidy(k);
    } catch {
      return { error: "A key is missing part of itself (for an elliptic-curve key, its x and y). Paste the whole public key." };
    }
    if (!k.kid) note = [note, "Named each key from its fingerprint."].filter(Boolean).join(" ");
    out.push(tidy1);
  }
  const seen = new Set<string>();
  for (const k of out) {
    if (seen.has(k.kid ?? "")) return { error: "Two keys have the same kid. Each key needs its own name." };
    seen.add(k.kid ?? "");
  }
  return { jwks: { keys: out }, note };
}

/** PEM-wrap a PKCS#8 private key for the file the person keeps. */
function pkcs8Pem(der: ArrayBuffer): string {
  const body = btoa(String.fromCharCode(...new Uint8Array(der))).match(/.{1,64}/g)?.join("\n") ?? "";
  return `-----BEGIN PRIVATE KEY-----\n${body}\n-----END PRIVATE KEY-----\n`;  // zid-secret-scan: allow: PEM framing of a key generated in the browser, not a stored secret
}

/**
 * Make a P-256 key pair IN THE BROWSER, for a test or a small team without a key service. The private key is returned once, to be saved as a file by the person, and is
 * never stored by the page or sent anywhere. A company with a key service should make the key there (it never leaves) and paste only the public half.
 */
export async function generateClientKeyPair(): Promise<{ jwks: Jwks; privatePem: string; kid: string }> {
  const pair = await subtle().generateKey({ name: "ECDSA", namedCurve: "P-256" }, true, ["sign", "verify"]);
  const pub = (await subtle().exportKey("jwk", pair.publicKey)) as Jwk;
  const privatePem = pkcs8Pem(await subtle().exportKey("pkcs8", pair.privateKey));
  const key = await tidy(pub);
  return { jwks: { keys: [key] }, privatePem, kid: key.kid ?? "" };
}
