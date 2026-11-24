import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { SCOPES } from "../admin/developers/verify-api-logic";
import { ERRORS, OPERATIONS, SECTIONS } from "./verify-api-docs";

/** The documentation is held to the facts: the published contract, the cell's own list of partner operations and its own error map. If any of them changes and this page does not, this fails. */

const REPO = resolve(process.cwd(), "../../..");
const contract = JSON.parse(readFileSync(resolve(REPO, "contracts/openapi/public/verify-api.json"), "utf-8")) as { paths: Record<string, Record<string, unknown>> };
const partnerSpec = readFileSync(resolve(REPO, "backend/app/src/zid_platform/modules/verify_api/application/partner_spec.py"), "utf-8");
const sessionsApi = readFileSync(resolve(REPO, "backend/app/src/zid_platform/modules/verify_api/api/sessions.py"), "utf-8");

const documented = new Set(OPERATIONS.map((o) => `${o.method.toLowerCase()} ${o.path}`));

describe("verify api documentation", () => {
  it("lists exactly the operations the cell publishes to partners", () => {
    const published = new Set([...partnerSpec.matchAll(/\("(get|post)", "(\/v1\/verify-api\/[^"]+)"\)/g)].map((m) => `${m[1]} ${m[2]}`));
    expect(published.size).toBeGreaterThan(10);
    expect(documented).toEqual(published);
  });
  it("only documents operations that exist in the published contract", () => {
    for (const o of OPERATIONS) {
      expect(contract.paths[o.path], `${o.method} ${o.path}`).toBeDefined();
      expect(Object.keys(contract.paths[o.path]!), `${o.method} ${o.path}`).toContain(o.method.toLowerCase());
    }
  });
  it("names the scope each operation needs, and every scope is a real one", () => {
    const known = new Set(SCOPES.map((s) => s.id));
    for (const o of OPERATIONS) {
      if (o.scope.startsWith("(")) continue;
      expect(known.has(o.scope), `${o.path} needs ${o.scope}`).toBe(true);
    }
  });
  it("lists exactly the errors the cell can answer, with their statuses", () => {
    const block = sessionsApi.slice(sessionsApi.indexOf("_STATUS = {"), sessionsApi.indexOf("}", sessionsApi.indexOf("_STATUS = {")));
    const cell = new Map([...block.matchAll(/"([A-Z_]+)":\s*(\d{3})/g)].map((m) => [m[1]!, Number(m[2])]));
    expect(cell.size).toBeGreaterThan(8);
    expect(new Map(ERRORS.map((e) => [e.code, e.status]))).toEqual(cell);
    for (const e of ERRORS) {
      expect(e.meaning.length).toBeGreaterThan(10);
      expect(e.action.length).toBeGreaterThan(5);
    }
  });
  it("has unique section ids and tables whose rows match their headings", () => {
    const ids = SECTIONS.map((s) => s.id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const s of SECTIONS) {
      for (const b of s.blocks) {
        if (b.kind === "table") for (const r of b.rows) expect(r.length, `${s.id}: ${r[0]}`).toBe(b.head.length);
      }
    }
  });
  it("shows a first sandbox flow in every language and never a real-looking secret", () => {
    const quick = SECTIONS.find((s) => s.id === "quickstart")!;
    const codes = quick.blocks.filter((b) => b.kind === "code");
    expect(codes.length).toBeGreaterThanOrEqual(3);
    for (const c of codes) expect(c.kind === "code" && c.examples.map((e) => e.lang).sort()).toEqual(["curl", "node", "python"]);
    const everything = JSON.stringify(SECTIONS);
    expect(everything).not.toMatch(/zps_[A-Za-z0-9_-]{16,}/); // a placeholder, never a secret
    expect(everything).not.toMatch(/zpt_[A-Za-z0-9_-]{16,}/);
    expect(everything).toContain("Idempotency-Key");
    expect(everything).toContain("YOUR-API-ADDRESS");
  });
});
