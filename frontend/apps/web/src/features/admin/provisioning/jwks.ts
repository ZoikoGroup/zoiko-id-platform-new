/** The connector's PUBLIC keys as the cell accepts them (JWK Set, 1 to 5 keys, each with a kid, no private members). */
export type Jwks = { keys: Record<string, unknown>[] };

const PRIVATE_MEMBERS = ["d", "p", "q", "dp", "dq", "qi", "k", "oth"];
const KEY_TYPES = ["RSA", "EC", "OKP"];

export function parseJwks(text: string): { jwks: Jwks; error?: undefined } | { jwks?: undefined; error: string } {
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    return { error: "This is not valid JSON." };
  }
  const keys = typeof data === "object" && data !== null ? (data as { keys?: unknown }).keys : undefined;
  if (!Array.isArray(keys) || keys.length === 0 || keys.length > 5) return { error: 'Paste a JWK Set: {"keys": [ ... ]} with 1 to 5 public keys.' };
  for (const k of keys) {
    if (typeof k !== "object" || k === null || Array.isArray(k) || !KEY_TYPES.includes((k as { kty?: string }).kty ?? "")) {
      return { error: "Each key needs kty RSA, EC or OKP." };
    }
    if (PRIVATE_MEMBERS.some((m) => m in k)) return { error: "This contains a private key. Paste the public keys only and keep the private key." };
    if (!(k as { kid?: unknown }).kid) return { error: "Each key needs a kid, so signatures can name which key they used." };
  }
  return { jwks: { keys: keys as Record<string, unknown>[] } };
}
