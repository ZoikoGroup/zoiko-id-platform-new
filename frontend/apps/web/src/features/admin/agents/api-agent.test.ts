/** The browser refuses a private key before anything is sent (CR-093). The server refuses it again; this is the first wall, not the only one. */
import { describe, expect, it } from "vitest";

import { jwksProblem } from "./api-agent";

const pub = { kty: "EC", crv: "P-256", x: "f83OJ3D2xF1Bg8vub9tLe1gHMzV76e8Tus9uPHvRVEU", y: "x_FEzRu9m36HLN_tue659LNpXW6pCyStikYjKIWI5a0", kid: "k1" };

describe("jwksProblem", () => {
  it("accepts a public key set", () => {
    const r = jwksProblem(JSON.stringify({ keys: [pub] }));
    expect(r.error).toBeUndefined();
    expect(r.jwks?.keys).toHaveLength(1);
  });
  it.each(["d", "p", "q", "dp", "dq", "qi", "k"])("refuses a key that has the private member %s and does not repeat it", (member) => {
    const r = jwksProblem(JSON.stringify({ keys: [{ ...pub, [member]: "SECRET-VALUE" }] }));
    expect(r.jwks).toBeUndefined();
    expect(r.error).toMatch(/private material/);
    expect(r.error).not.toMatch(/SECRET-VALUE/);
  });
  it("explains bad JSON, a wrong shape, too many keys and a missing kid", () => {
    expect(jwksProblem("{nope").error).toMatch(/not valid JSON/);
    expect(jwksProblem(JSON.stringify({ keys: [] })).error).toMatch(/1 to 5 keys/);
    expect(jwksProblem(JSON.stringify({ keys: Array(6).fill(pub) })).error).toMatch(/1 to 5 keys/);
    expect(jwksProblem(JSON.stringify({ keys: [{ kty: "EC" }] })).error).toMatch(/kid/);
  });
});
