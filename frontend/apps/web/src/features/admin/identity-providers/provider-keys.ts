/** The provider's PUBLIC signing keys as the cell accepts them: a JWK Set. A single key, or a list of keys, is wrapped into one. */
export type ProviderKeys = { keys: Record<string, unknown>[] };

const PRIVATE_MEMBERS = ["d", "p", "q", "dp", "dq", "qi", "k", "oth"];

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);

export function parseProviderKeys(text: string): { keys: ProviderKeys; error?: undefined } | { keys?: undefined; error: string } {
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    return { error: "The keys are not valid JSON. Paste the provider's key list (its JWKS) as it is." };
  }
  const list = Array.isArray(data) ? data : isObject(data) && Array.isArray(data.keys) ? data.keys : isObject(data) && "kty" in data ? [data] : null;
  if (!list || list.length === 0) return { error: 'Paste the provider\'s key list: {"keys": [ … ]}. A single public key is fine too.' };
  for (const k of list) {
    if (!isObject(k) || typeof k.kty !== "string") return { error: "Each key needs a kty (RSA, EC or OKP)." };
    if (PRIVATE_MEMBERS.some((m) => m in k)) {
      return { error: "This contains a private key. Never paste a private key here: use the provider's public key list (its JWKS) instead." };
    }
  }
  return { keys: { keys: list as Record<string, unknown>[] } };
}
