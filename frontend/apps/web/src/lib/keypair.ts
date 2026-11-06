/** A fresh signing key pair made in this browser. The private half is shown once and never sent to Zoiko iD; only the public half is
 * registered, so Zoiko iD can check what the application or connector signs. `prefix` names the key (its `kid`). */
export async function generateKeyPair(prefix = "key"): Promise<{ publicKeys: string; privateKey: string }> {
  const pair = await crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, true, ["sign", "verify"]);
  const kid = `${prefix}-${crypto.randomUUID().slice(0, 8)}`;
  const pub = { ...(await crypto.subtle.exportKey("jwk", pair.publicKey)), kid, alg: "ES256", use: "sig" };
  const priv = { ...(await crypto.subtle.exportKey("jwk", pair.privateKey)), kid, alg: "ES256" };
  return { publicKeys: JSON.stringify({ keys: [pub] }, null, 2), privateKey: JSON.stringify(priv, null, 2) };
}

/** Hands the browser a small text file to save (the private key, which Zoiko iD never keeps). */
export function downloadText(filename: string, text: string): void {
  const url = URL.createObjectURL(new Blob([text], { type: "application/json" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename.replace(/[^A-Za-z0-9._-]/g, "-");
  a.click();
  URL.revokeObjectURL(url);
}
