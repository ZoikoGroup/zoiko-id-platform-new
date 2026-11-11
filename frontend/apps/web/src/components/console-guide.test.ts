import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { GUIDE_LINKS, PAGES, guideHref, linkId } from "@zoiko/console-guide";

import { GET } from "@/app/guide/out/[id]/route";

const get = (id: string) => GET(new Request(`http://app.test/guide/out/${encodeURIComponent(id)}`), { params: Promise.resolve({ id }) });

describe("the console guide's page list matches the console's real navigation", () => {
  const shell = readFileSync(resolve(process.cwd(), "src/components/console-shell.tsx"), "utf8");
  const nav = [...shell.matchAll(/\{ id: "([a-z0-9-]+)", label: "([^"]+)", href: "(\/[^"]+)"/g)].map((m) => ({ id: m[1], label: m[2], href: m[3] }));

  it("finds the navigation in the shell", () => {
    expect(nav.length).toBeGreaterThan(25);
  });

  it("every page in the shell navigation is one the guide can point to, with the same label and path", () => {
    const byId = new Map(PAGES.map((p) => [p.id, p]));
    for (const n of nav) {
      const p = byId.get(n.id as string);
      expect(p, `guide is missing the "${n.id}" page`).toBeDefined();
      expect(p?.href, n.id).toBe(n.href);
      expect(p?.label, n.id).toBe(n.label);
    }
  });

  it("the guide points at no page that the console does not have", () => {
    const ids = new Set(nav.map((n) => n.id));
    for (const p of PAGES) expect(ids.has(p.id), `the guide lists "${p.id}" but the shell has no such page`).toBe(true);
  });
});

describe("the guide's link redirect", () => {
  it("sends a known link id to its fixed console path, relative, with no caching", async () => {
    const r = await get("PAGE-TEAM");
    expect(r.status).toBe(302);
    expect(r.headers.get("location")).toBe("/admin/team");
    expect(r.headers.get("cache-control")).toBe("no-store");
    expect(r.headers.get("referrer-policy")).toBe("no-referrer");
  });

  it("every id the guide can issue resolves, and only to a console path", async () => {
    for (const id of Object.keys(GUIDE_LINKS)) {
      const r = await get(id);
      expect(r.status, id).toBe(302);
      expect(r.headers.get("location"), id).toMatch(/^\/[a-z0-9/-]+$/);
    }
    expect(linkId("team")).toBe("PAGE-TEAM");
  });

  it("refuses anything that is not a registered id (no open redirect, no prototype lookups)", async () => {
    for (const id of ["https://evil.example", "//evil.example", "PAGE-EVIL", "page-team", "constructor", "__proto__", "toString", "PAGE-TEAM?next=https://evil.example", "../admin", ""]) {
      const r = await get(id);
      expect(r.status, id).toBe(404);
      expect(r.headers.get("location"), id).toBeNull();
      expect(guideHref(id), id).toBeUndefined();
    }
  });
});
