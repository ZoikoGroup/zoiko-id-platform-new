import { describe, expect, it } from "vitest";

import { applyDevice, createBff, DEVICE_HEADER, deviceCookieName, MemorySessionStore, resolveDevice, type BffConfig } from "../src";

const HEX32 = /^[0-9a-f]{32}$/;
const GOOD = "0123456789abcdef0123456789abcdef";

describe("resolveDevice", () => {
  it("mints 32 lowercase hex characters when the cookie is absent, and says to set it", () => {
    const d = resolveDevice(null, true);
    expect(d.id).toMatch(HEX32);
    expect(d.setCookie).toContain(`__Host-zid_device=${d.id}`);
    expect(resolveDevice(null, true).id).not.toBe(d.id);
  });

  it("reuses a well-formed id without setting it again", () => {
    expect(resolveDevice(`a=b; __Host-zid_device=${GOOD}`, true)).toEqual({ id: GOOD, setCookie: null });
  });

  it("replaces a malformed value", () => {
    for (const bad of ["short", GOOD.toUpperCase(), `${GOOD}0`, "zz".repeat(16)]) {
      const d = resolveDevice(`__Host-zid_device=${bad}`, true);
      expect(d.id).toMatch(HEX32);
      expect(d.id).not.toBe(bad);
      expect(d.setCookie).not.toBeNull();
    }
  });

  it("ignores the other mode's cookie name", () => {
    expect(resolveDevice(`zid_device=${GOOD}`, true).id).not.toBe(GOOD);
  });

  it("sets HttpOnly, SameSite=Lax, Path=/, one year and Secure when secure", () => {
    const c = resolveDevice(null, true).setCookie!;
    expect(c).toContain("Path=/");
    expect(c).toContain("HttpOnly");
    expect(c).toContain("SameSite=Lax");
    expect(c).toContain("Max-Age=31536000");
    expect(c).toContain("Secure");
    expect(c).not.toContain("Domain");
  });

  it("uses a plain name and no Secure in non-secure mode", () => {
    expect(deviceCookieName(false)).toBe("zid_device");
    const d = resolveDevice(null, false);
    expect(d.setCookie).toContain(`zid_device=${d.id}`);
    expect(d.setCookie).not.toContain("__Host-");
    expect(d.setCookie).not.toContain("Secure");
    expect(resolveDevice(`zid_device=${GOOD}`, false).id).toBe(GOOD);
  });

  it("does nothing when switched off", () => {
    expect(resolveDevice(null, true, false)).toEqual({ id: null, setCookie: null });
  });
});

describe("applyDevice", () => {
  it("replaces a client-supplied header", () => {
    const h = new Headers({ [DEVICE_HEADER]: "spoofed" });
    applyDevice(h, { id: GOOD, setCookie: null });
    expect(h.get(DEVICE_HEADER)).toBe(GOOD);
  });

  it("removes a client-supplied header when off", () => {
    const h = new Headers({ [DEVICE_HEADER]: "spoofed" });
    applyDevice(h, { id: null, setCookie: null });
    expect(h.has(DEVICE_HEADER)).toBe(false);
  });
});

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
  apiPathPrefixes: ["/v1/"],
  passthroughPrefixes: ["/authn/"],
};

async function proxyWith(cookie: string, extra: Record<string, string> = {}, config: Partial<BffConfig> = {}) {
  const store = new MemorySessionStore();
  await store.set({ id: "sid", csrf: "c", accessToken: "at", accessTokenExpiresAt: 9999999999, createdAt: 0, subject: { sub: "u1" } }, 3600);
  let seen = new Headers();
  const bff = createBff({
    ...base,
    ...config,
    store,
    fetchImpl: async (_u, init) => {
      seen = new Headers(init?.headers);
      return new Response("{}");
    },
  });
  const res = await bff.proxy(
    new Request("https://id.test/api/bff/v1/x", { headers: { cookie: `__Host-zid_web=sid; ${cookie}`, ...extra } }),
    ["v1", "x"],
  );
  return { res, seen };
}

describe("bearer relay (proxy)", () => {
  it("mints, forwards and sets the cookie on the first request", async () => {
    const { res, seen } = await proxyWith("");
    const id = seen.get(DEVICE_HEADER);
    expect(id).toMatch(HEX32);
    expect(res.headers.get("set-cookie")).toContain(`__Host-zid_device=${id}`);
    expect(seen.get("cookie")).toBeNull();
  });

  it("reuses the browser's id, does not set it again, and overrides a spoofed header", async () => {
    const { res, seen } = await proxyWith(`__Host-zid_device=${GOOD}`, { [DEVICE_HEADER]: "spoofed" });
    expect(seen.get(DEVICE_HEADER)).toBe(GOOD);
    expect(res.headers.get("set-cookie")).toBeNull();
  });

  it("drops a spoofed header and sends none when switched off", async () => {
    const { res, seen } = await proxyWith("", { [DEVICE_HEADER]: "spoofed" }, { deviceCookie: false });
    expect(seen.has(DEVICE_HEADER)).toBe(false);
    expect(res.headers.get("set-cookie")).toBeNull();
  });
});

describe("cell-cookie relay (passthrough)", () => {
  async function relay(cookie: string, extra: Record<string, string> = {}) {
    let seen = new Headers();
    const bff = createBff({
      ...base,
      store: new MemorySessionStore(),
      fetchImpl: async (_u, init) => {
        seen = new Headers(init?.headers);
        return new Response("{}");
      },
    });
    const res = await bff.passthrough(
      new Request("https://id.test/authn/x", { method: "POST", headers: { cookie, origin: "https://id.test", ...extra }, body: "{}" }),
      "/authn/x",
    );
    return { res, seen };
  }

  it("mints, forwards as a header only, and sets the cookie", async () => {
    const { res, seen } = await relay("__Host-zid_session=cell");
    const id = seen.get(DEVICE_HEADER);
    expect(id).toMatch(HEX32);
    expect(seen.get("cookie")).toBe("__Host-zid_session=cell");
    expect(res.headers.get("set-cookie")).toContain(`__Host-zid_device=${id}`);
  });

  it("reuses a held id, overrides a spoofed header and sets nothing", async () => {
    const { res, seen } = await relay(`__Host-zid_device=${GOOD}`, { [DEVICE_HEADER]: "spoofed" });
    expect(seen.get(DEVICE_HEADER)).toBe(GOOD);
    expect(seen.get("cookie")).toBeNull();
    expect(res.headers.get("set-cookie")).toBeNull();
  });
});
