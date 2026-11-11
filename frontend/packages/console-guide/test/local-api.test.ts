import { readdirSync, readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";

import { createGuideFetch, type GuideLoaders } from "../src/localApi";
import { GUIDE_LINKS } from "../src/pages";
import type { MyPermissions, RoleMatrix } from "../src/types";

const matrix = JSON.parse(readFileSync(new URL("./fixtures/role-matrix.json", import.meta.url), "utf8")) as RoleMatrix;
const perms = (roles: string[]): MyPermissions => ({ roles, policy_hash: matrix.policy_hash, enforcement: false, rights: {} });

function setup(roles = ["ADMIN"], now = { t: 1_000_000 }) {
  const calls = { matrix: 0, perms: 0 };
  const loaders: GuideLoaders = {
    loadMatrix: async () => (calls.matrix++, matrix),
    loadPermissions: async () => (calls.perms++, perms(roles)),
  };
  const f = createGuideFetch(loaders, { now: () => now.t, cacheMs: 30_000 });
  return { f, calls, now };
}
const post = (f: typeof fetch, path: string, body: unknown) => f(`/guide/${path}`, { method: "POST", body: JSON.stringify(body) });

afterEach(() => vi.restoreAllMocks());

describe("the widget's contract, answered locally", () => {
  it("bootstrap carries the fields the widget needs, with every server feature off", async () => {
    const { f } = setup(["VIEWER"]);
    const b = await (await f("/guide/bootstrap")).json();
    expect(Object.keys(b).sort()).toEqual(
      ["display_name", "enabled", "flags", "limits", "locale", "notice", "revision", "sample_content", "snapshot_id", "starters", "static_help"].sort(),
    );
    expect(b.enabled).toBe(true);
    expect(b.sample_content).toBe(false);
    expect(Object.values(b.flags).every((v) => v === false)).toBe(true);
    expect(b.notice).toMatch(/stay in this browser/);
    expect(b.starters.length).toBeGreaterThan(3);
    for (const l of b.static_help) expect(GUIDE_LINKS[l.link_id]).toBeDefined();
  });

  it("sessions and feedback are inert, and the intake forms are never offered", async () => {
    const { f } = setup();
    const s = await post(f, "sessions", {});
    expect(s.status).toBe(201);
    expect((await s.json()).idle_seconds).toBe(1800);
    expect((await f("/guide/sessions", { method: "DELETE" })).status).toBe(204);
    expect((await post(f, "feedback", { response_id: "rsp_x", rating: "USEFUL" })).status).toBe(204);
    const cfg = await (await f("/guide/intake/config")).json();
    expect(cfg.sales.available).toBe(false);
    expect(cfg.support.available).toBe(false);
  });

  it("a message returns a packet in the exact shape the widget renders", async () => {
    const { f } = setup(["ADMIN"]);
    const r = await post(f, "messages", { client_message_id: "m-0001-aaaa", text: "Can I invite a viewer?", locale: "en-US" });
    expect(r.status).toBe(200);
    const p = await r.json();
    expect(Object.keys(p).sort()).toEqual(
      ["actions", "answer_text", "capabilities", "citations", "claims", "decision", "intent_id", "policy_version", "response_id", "safety", "session_expires_at", "snapshot_id"].sort(),
    );
    expect(p.decision).toBe("ANSWER");
    expect(p.response_id).toMatch(/^rsp_[0-9a-f]{32}$/);
    expect(p.answer_text).toContain("run it yourself");
    expect(p.citations[0]).toMatchObject({ source_id: "ROLE-MATRIX", title: "Published role matrix" });
    expect(p.safety).toEqual({ reason: "OK", sensitive_input_blocked: false, sample_content: false, ai_assisted: false });
    for (const a of p.actions) {
      expect(a.type).toBe("OPEN_APPROVED_LINK");
      expect(GUIDE_LINKS[a.link_id]).toBeDefined(); // only fixed console paths, by id
    }
  });

  it("the answer follows the signed-in person's roles", async () => {
    const asViewer = await (await post(setup(["VIEWER"]).f, "messages", { text: "Can I invite a viewer?" })).json();
    const asOwner = await (await post(setup(["OWNER"]).f, "messages", { text: "Can I invite a viewer?" })).json();
    expect(asViewer.answer_text).toMatch(/not part of your role/);
    expect(asOwner.answer_text).toMatch(/run it yourself/);
  });

  it("a secret is refused like the public assistant refuses it (422) and is not echoed", async () => {
    const { f } = setup();
    const r = await post(f, "messages", { text: "my password is hunter2-CANARY" });
    expect(r.status).toBe(422);
    const body = await r.text();
    expect(body).toContain("SENSITIVE_CONTENT_BLOCKED");
    expect(body).not.toContain("CANARY");
  });

  it("bad requests are rejected and unknown routes are closed", async () => {
    const { f } = setup();
    expect((await post(f, "messages", { text: "" })).status).toBe(400);
    expect((await post(f, "messages", { text: "x".repeat(501) })).status).toBe(400);
    expect((await post(f, "messages", { nope: 1 })).status).toBe(400);
    expect((await f("/guide/messages", { method: "POST", body: "{not json" })).status).toBe(400);
    expect((await f("/guide/nope")).status).toBe(404);
    expect((await f("/elsewhere/bootstrap")).status).toBe(404);
    expect((await f("/guide/messages", { method: "GET" })).status).toBe(404);
  });

  it("when the catalogues cannot be loaded the widget gets 503, never an invented answer", async () => {
    const f = createGuideFetch({ loadMatrix: async () => { throw new Error("signed out"); }, loadPermissions: async () => perms([]) });
    const r = await post(f, "messages", { text: "Can I invite a viewer?" });
    expect(r.status).toBe(503);
    expect(await r.text()).toContain("UNAVAILABLE");
    expect((await f("/guide/bootstrap")).status).toBe(503);
  });

  it("the two catalogues are reused briefly, then fetched again", async () => {
    const { f, calls, now } = setup();
    for (let i = 0; i < 3; i++) await post(f, "messages", { text: "What can I do?" });
    expect(calls).toEqual({ matrix: 1, perms: 1 });
    now.t += 31_000;
    await post(f, "messages", { text: "What can I do?" });
    expect(calls).toEqual({ matrix: 2, perms: 2 });
  });
});

describe("what leaves the browser: nothing", () => {
  it("never calls fetch itself; it only asks its injected loaders", async () => {
    const spy = vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("no network expected"));
    const { f } = setup();
    await post(f, "messages", { text: "Who can approve role grants? and my password is hunter2xyz" });
    await post(f, "messages", { text: "Where do I manage my passkeys?" });
    expect(spy).not.toHaveBeenCalled();
  });

  it("the source has no storage, cookie, beacon, socket or network call", () => {
    const dir = new URL("../src/", import.meta.url);
    for (const name of readdirSync(dir)) {
      const code = readFileSync(new URL(name, dir), "utf8").replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
      for (const banned of ["localStorage", "sessionStorage", "indexedDB", "document.cookie", "sendBeacon", "XMLHttpRequest", "WebSocket", "EventSource", "console.log", "navigator."]) {
        expect(code.includes(banned), `${name} uses ${banned}`).toBe(false);
      }
      expect(/\bfetch\(/.test(code), `${name} calls fetch()`).toBe(false);
    }
  });
});
