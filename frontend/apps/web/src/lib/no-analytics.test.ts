/** UX-27: the console sends no analytics or telemetry of its own. Nothing here can leak a person, a token or raw
 * evidence to a third party because nothing is sent: a vendor SDK or beacon needs this test, and a reviewed
 * allowlist, changed first. (What the cell itself logs is scrubbed and tested in the telemetry library.) */
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

const SRC = path.resolve(import.meta.dirname, "..");
const VENDORS = /\b(google-analytics|googletagmanager|gtag\(|segment\.(io|com)|analytics\.js|mixpanel|posthog|amplitude|fullstory|hotjar|heap\.io|datadoghq|newrelic|sentry\.io|@sentry\/|logrocket|rudderstack)\b/i;
const ALLOWED_FILES = new Set([path.join("lib", "no-analytics.test.ts")]);

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((n) => {
    const p = path.join(dir, n);
    return statSync(p).isDirectory() ? files(p) : /\.(ts|tsx|js|mjs|json)$/.test(n) ? [p] : [];
  });
}

describe("UX-27 analytics privacy", () => {
  it("has no analytics vendor SDK or beacon in the source", () => {
    const hits = files(SRC)
      .filter((f) => !ALLOWED_FILES.has(path.relative(SRC, f)))
      .filter((f) => VENDORS.test(readFileSync(f, "utf-8")))
      .map((f) => path.relative(SRC, f));
    expect(hits).toEqual([]);
  });

  it("declares no analytics dependency", () => {
    const pkg = JSON.parse(readFileSync(path.resolve(SRC, "..", "package.json"), "utf-8")) as { dependencies?: Record<string, string> };
    const deps = Object.keys(pkg.dependencies ?? {});
    expect(deps.filter((d) => VENDORS.test(d))).toEqual([]);
  });

  it("never puts a token or raw evidence into the browser's storage", () => {
    const writes = files(SRC)
      .filter((f) => /\.(ts|tsx)$/.test(f) && !f.endsWith(".test.ts") && !f.endsWith(".test.tsx"))
      .filter((f) => /\b(localStorage|sessionStorage)\.setItem\(/.test(readFileSync(f, "utf-8")))
      .map((f) => path.relative(SRC, f));
    expect(writes).toEqual([]);
  });
});
