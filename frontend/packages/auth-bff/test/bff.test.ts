import { describe, expect, it } from "vitest";

import { CELL_SESSION_COOKIE, createBff, MemorySessionStore, readRuntimeEnv, safeReturnTo, type BffConfig } from "../src";

const base: Omit<BffConfig, "store"> = {
  app: "web",
  cellApiUrl: "https://cell.test",
  issuer: "https://cell.test",
  clientId: "zoiko-web",
  publicOrigin: "https://id.test",
  callbackPath: "/auth/callback",
  scopes: ["openid"],
  secureCookies: true,
  sessionTtlSeconds: 3600,
  apiPathPrefixes: ["/v1/", "/sessions"],
  passthroughPrefixes: ["/authn/", "/oauth2/consent/"],
};

async function withSession(store: MemorySessionStore) {
  await store.set(
    { id: "sid", csrf: "csrf-token", accessToken: "at", accessTokenExpiresAt: 9999999999, createdAt: 0, subject: { sub: "u1" } },
    3600,
  );
  return { cookie: `__Host-zid_web=sid; ${CELL_SESSION_COOKIE}=cell` };
}

function b64url(o: unknown) {
  return Buffer.from(JSON.stringify(o)).toString("base64url");
}

describe("safeReturnTo", () => {
  it("rejects open redirects", () => {
    expect(safeReturnTo("//evil.example")).toBe("/");
    expect(safeReturnTo("/\\evil.example")).toBe("/");
    expect(safeReturnTo("/ok\tpath")).toBe("/");
    expect(safeReturnTo("https://evil.example")).toBe("/");
    expect(safeReturnTo("/account?x=1")).toBe("/account?x=1");
  });
});

describe("session", () => {
  it("says who is signed in and the public API address, and never a token", async () => {
    const store = new MemorySessionStore();
    const h = await withSession(store);
    const body = await (await createBff({ ...base, store }).session(new Request("https://id.test/api/auth/session", { headers: h }))).json();
    expect(body).toMatchObject({ authenticated: true, subject: { sub: "u1" }, csrf: "csrf-token", apiBase: "https://cell.test" });
    expect(JSON.stringify(body)).not.toContain("accessToken");
    expect(JSON.stringify(body)).not.toContain('"at"');
  });
});

describe("proxy", () => {
  it("requires a session", async () => {
    const bff = createBff({ ...base, store: new MemorySessionStore() });
    const res = await bff.proxy(new Request("https://id.test/api/bff/v1/x"), ["v1", "x"]);
    expect(res.status).toBe(401);
    expect((await res.json()).error.code).toBe("LOGIN_REQUIRED");
  });

  it("refuses paths outside the allowlist and traversal", async () => {
    const store = new MemorySessionStore();
    const h = await withSession(store);
    const bff = createBff({ ...base, store });
    expect((await bff.proxy(new Request("https://id.test/api/bff/admin", { headers: h }), ["admin"])).status).toBe(404);
    expect((await bff.proxy(new Request("https://id.test/api/bff/v1/..", { headers: h }), ["v1", ".."])).status).toBe(404);
  });

  it("rejects mutations without the CSRF header or from another origin", async () => {
    const store = new MemorySessionStore();
    const h = await withSession(store);
    const bff = createBff({ ...base, store, fetchImpl: async () => new Response("{}") });
    const noCsrf = await bff.proxy(new Request("https://id.test/api/bff/v1/x", { method: "POST", headers: h, body: "{}" }), ["v1", "x"]);
    expect(noCsrf.status).toBe(403);
    const crossOrigin = await bff.proxy(
      new Request("https://id.test/api/bff/v1/x", {
        method: "POST",
        headers: { ...h, "x-zid-csrf": "csrf-token", origin: "https://evil.test" },
        body: "{}",
      }),
      ["v1", "x"],
    );
    expect(crossOrigin.status).toBe(403);
  });

  it("forwards with the bearer token, keeps ':' actions, and never leaks cookies", async () => {
    const store = new MemorySessionStore();
    const h = await withSession(store);
    let seenUrl = "";
    let seen: Headers | undefined;
    const bff = createBff({
      ...base,
      store,
      fetchImpl: async (u, init) => {
        seenUrl = String(u);
        seen = new Headers(init?.headers);
        return new Response('{"ok":true}', { headers: { "content-type": "application/json", "set-cookie": "x=y" } });
      },
    });
    const res = await bff.proxy(
      new Request("https://id.test/api/bff/sessions/abc:revoke", {
        method: "POST",
        headers: { ...h, "x-zid-csrf": "csrf-token", "idempotency-key": "k1", "content-type": "application/json" },
        body: "{}",
      }),
      ["sessions", "abc:revoke"],
    );
    expect(res.status).toBe(200);
    expect(seenUrl).toBe("https://cell.test/sessions/abc:revoke");
    expect(seen?.get("authorization")).toBe("Bearer at");
    expect(seen?.get("cookie")).toBeNull();
    expect(seen?.get("idempotency-key")).toBe("k1");
    expect(res.headers.get("set-cookie") ?? "").not.toContain("x=y");
    expect(res.headers.get("cache-control")).toContain("no-store");
  });
});

describe("passthrough", () => {
  it("relays only the cell session cookie, both ways", async () => {
    let seen: Headers | undefined;
    const bff = createBff({
      ...base,
      store: new MemorySessionStore(),
      fetchImpl: async (_u, init) => {
        seen = new Headers(init?.headers);
        const headers = new Headers({ "content-type": "application/json" });
        headers.append("set-cookie", `${CELL_SESSION_COOKIE}=new; Path=/; Secure; HttpOnly; SameSite=Lax`);
        headers.append("set-cookie", "other=1; Path=/");
        return new Response("{}", { headers });
      },
    });
    const res = await bff.passthrough(
      new Request("https://id.test/authn/webauthn/authentication/verify", {
        method: "POST",
        headers: { cookie: `__Host-zid_web=sid; ${CELL_SESSION_COOKIE}=old`, origin: "https://id.test" },
        body: "{}",
      }),
      "/authn/webauthn/authentication/verify",
    );
    expect(seen?.get("cookie")).toBe(`${CELL_SESSION_COOKIE}=old`);
    expect(seen?.get("origin")).toBe("https://id.test");
    expect(res.headers.get("set-cookie")).toContain(`${CELL_SESSION_COOKIE}=new`);
    expect(res.headers.get("set-cookie")).not.toContain("other=1");
  });

  it("refuses paths that are not browser flows", async () => {
    const bff = createBff({ ...base, store: new MemorySessionStore() });
    expect((await bff.passthrough(new Request("https://id.test/v1/x"), "/v1/x")).status).toBe(404);
  });
});

describe("reauthorize", () => {
  it("turns the cell cookie into an app session without a browser redirect", async () => {
    const store = new MemorySessionStore();
    let nonce = "";
    const bff = createBff({
      ...base,
      store,
      fetchImpl: async (u, init) => {
        const url = new URL(String(u));
        if (url.pathname === "/oauth2/authorize") {
          expect(new Headers(init?.headers).get("cookie")).toBe(`${CELL_SESSION_COOKIE}=cell`);
          nonce = url.searchParams.get("nonce")!;
          return new Response(null, {
            status: 303,
            headers: { location: `https://id.test/auth/callback?code=c1&state=${url.searchParams.get("state")}` },
          });
        }
        const idToken = `${b64url({ alg: "none" })}.${b64url({ iss: "https://cell.test", aud: "zoiko-web", nonce, sub: "p1", tenant_id: "t1" })}.`;
        return Response.json({ access_token: "at", id_token: idToken, expires_in: 300 });
      },
    });
    const res = await bff.reauthorize(
      new Request("https://id.test/api/auth/session", { method: "POST", headers: { cookie: `${CELL_SESSION_COOKIE}=cell`, origin: "https://id.test" } }),
    );
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.subject).toMatchObject({ sub: "p1", tenantId: "t1" });
    expect(res.headers.get("set-cookie")).toContain("__Host-zid_web=");
  });

  it("maps login_required to 401", async () => {
    const bff = createBff({
      ...base,
      store: new MemorySessionStore(),
      fetchImpl: async () => new Response(null, { status: 303, headers: { location: "https://id.test/auth/callback?error=login_required" } }),
    });
    const res = await bff.reauthorize(new Request("https://id.test/api/auth/session", { method: "POST", headers: { cookie: `${CELL_SESSION_COOKIE}=x` } }));
    expect(res.status).toBe(401);
  });
});

describe("runtime env", () => {
  it("requires the core settings and parses federation options", () => {
    expect(() => readRuntimeEnv({})).toThrow();
    const env = readRuntimeEnv({
      ZID_CELL_API_URL: "http://api:8000/",
      ZID_OAUTH_CLIENT_ID: "zoiko-web",
      ZID_PUBLIC_ORIGIN: "http://localhost:3000",
      ZID_FEDERATION_CONNECTIONS: "c1=Acme Okta, c2=Contoso Entra",
    });
    expect(env.cellApiUrl).toBe("http://api:8000");
    expect(env.issuer).toBe("http://api:8000");
    expect(env.federationConnections).toEqual([
      { id: "c1", label: "Acme Okta" },
      { id: "c2", label: "Contoso Entra" },
    ]);
  });
});
