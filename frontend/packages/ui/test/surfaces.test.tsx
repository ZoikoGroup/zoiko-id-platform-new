// @vitest-environment jsdom
/** UI acceptance checks for product-surface rules that need no browser (ZID-UX-001 §4, §12, §18, §24).
 *
 * UX-04 production context is text and a pattern, never colour only.
 * UX-10 a CONFLICTED / STALE root is never drawn like an active one.
 * UX-16 a one-time secret is masked until revealed and Done is gated on an acknowledgement.
 * UX-28 stale / partial / degraded data is labelled and a missing number is never shown as zero.
 * UX-05 (UI half) a policy diff shows added and removed lines with text markers.
 * PRIV-21 the Global Privacy Control signal is sent once per page, and only when the browser advertises it.
 * PRIV-21 the Global Privacy Control signal is sent once per session, and only when the browser advertises it.
 * Server-side enforcement of each rule is proven by the backend tests named in the traceability rows. */
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  DegradedNotice,
  EnvironmentBadge,
  OneTimeSecret,
  PartialDataNotice,
  SourceDiff,
  StaleNotice,
  StatusBadge,
  lineDiff,
  sendGlobalPrivacySignal,
  stateTone,
} from "../src";

afterEach(cleanup);
const html = (n: React.ReactElement) => renderToStaticMarkup(n);
const text = (n: React.ReactElement) => html(n).replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();

describe("UX-04 production is distinct from sandbox without relying on colour", () => {
  it("states the environment in words and gives production a non-colour marker", () => {
    const prod = html(<EnvironmentBadge environment="production" />);
    const sandbox = html(<EnvironmentBadge environment="sandbox" />);
    expect(text(<EnvironmentBadge environment="production" />)).toBe("Production");
    expect(text(<EnvironmentBadge environment="sandbox" />)).toBe("sandbox");
    expect(prod).toContain("env-hatch"); // hatched pattern survives monochrome
    expect(prod).not.toContain("<svg");
    expect(sandbox).toContain("<svg"); // sandbox carries an icon and a dashed border instead
    expect(sandbox).toContain("border-dashed");
    expect(prod).not.toContain("border-dashed");
    expect(prod).not.toEqual(sandbox);
  });

  it.each(["test", "development"] as const)("%s is labelled as itself, never as production", (env) => {
    expect(text(<EnvironmentBadge environment={env} />)).toBe(env);
    expect(html(<EnvironmentBadge environment={env} />)).not.toContain("env-hatch");
  });
});

describe("UX-10 a conflicted or stale root cannot be mistaken for an active one", () => {
  it.each(["CONFLICTED", "STALE"])("%s is a warning with its own icon and an accessible state name", (state) => {
    const root = html(<StatusBadge state={state} kind="Root status" />);
    const active = html(<StatusBadge state="ACTIVE" kind="Root status" />);
    expect(stateTone(state)).toBe("warn");
    expect(stateTone("ACTIVE")).toBe("ok");
    expect(root).not.toContain("ok-bg");
    expect(root).toMatch(new RegExp(`Root status: ${state[0]}${state.slice(1).toLowerCase()}`));
    expect(root).not.toMatch(/Root status: Active/);
    expect(root).not.toEqual(active);
    expect(root.match(/<svg[\s\S]*?<\/svg>/)?.[0]).not.toEqual(active.match(/<svg[\s\S]*?<\/svg>/)?.[0]); // a different icon
  });

  it("an unknown or missing root state is neutral, never active-looking", () => {
    expect(stateTone("SOMETHING_NEW")).toBe("neutral");
    expect(stateTone(undefined)).toBe("neutral");
  });
});

describe("UX-16 a one-time secret is masked, announced as shown once and acknowledged before Done", () => {
  it("masks the value until revealed, and Done stays disabled until the acknowledgement is ticked", async () => {
    const onAck = vi.fn();
    const user = userEvent.setup();
    render(<OneTimeSecret label="Client secret" secret="s3cr3t-value-123456" onAcknowledge={onAck} />);
    expect(screen.getByText("Shown once")).toBeTruthy();
    expect(document.body.textContent).not.toContain("s3cr3t-value-123456"); // masked by default
    expect(screen.getByLabelText("Client secret (hidden)")).toBeTruthy();
    const done = screen.getByRole("button", { name: "Done" }) as HTMLButtonElement;
    expect(done.disabled).toBe(true);
    await user.click(screen.getByRole("button", { name: /reveal/i }));
    expect(document.body.textContent).toContain("s3cr3t-value-123456");
    await user.click(screen.getByRole("checkbox"));
    expect(done.disabled).toBe(false);
    await user.click(done);
    expect(onAck).toHaveBeenCalledTimes(1);
  });

  it("the secret exists only in component state: once unmounted it is gone from the page", async () => {
    const user = userEvent.setup();
    const { unmount } = render(<OneTimeSecret label="Token" secret="tok-abc-999999" onAcknowledge={() => {}} />);
    await user.click(screen.getByRole("button", { name: /reveal/i }));
    expect(document.body.textContent).toContain("tok-abc-999999");
    unmount();
    expect(document.body.textContent).not.toContain("tok-abc-999999");
  });
});

describe("UX-28 stale, partial and degraded data is labelled; missing is never shown as zero", () => {
  it("a stale notice says it is the last known state, how old it is and whether changes are blocked", () => {
    const t = text(<StaleNotice age="12 minutes ago" blocksActions />);
    expect(t).toContain("Showing last known state");
    expect(t).toContain("Last updated 12 minutes ago");
    expect(t).toContain("Changes are blocked until the state is refreshed");
    expect(text(<StaleNotice age="1 minute ago" />)).toContain("Values may have changed since");
  });

  it("a partial-data notice names the missing sources and says the totals are not zero", () => {
    const t = text(<PartialDataNotice missing={["Billing", "Usage meter"]} />);
    expect(t).toContain("Some sources did not respond");
    expect(t).toContain("Not included: Billing, Usage meter");
    expect(t).toContain("exclude these sources and are not zero");
  });

  it("a degraded notice states the capability and its impact, without vendor internals", () => {
    const t = text(<DegradedNotice capability="Search" impact="Results may be incomplete." />);
    expect(t).toContain("Search is degraded");
    expect(t).toContain("Results may be incomplete.");
  });

  it("a status with no value renders as unknown, not as healthy or zero", () => {
    const out = html(<StatusBadge state={null} kind="Cell API" />);
    expect(out).toContain("Cell API: Unknown");
    expect(out).not.toContain("ok-bg");
  });
});

describe("UX-05 policy diff (UI half)", () => {
  const before = '@id("a") permit (principal, action, resource);\n@id("b") forbid (principal, action, resource);';
  const after = '@id("a") permit (principal, action, resource);\n@id("c") permit (principal, action == Action::"x", resource);';

  it("computes added and removed lines", () => {
    const d = lineDiff(before, after);
    expect(d.filter((l) => l.kind === "same")).toHaveLength(1);
    expect(d.filter((l) => l.kind === "del").map((l) => l.text)).toEqual(['@id("b") forbid (principal, action, resource);']);
    expect(d.filter((l) => l.kind === "add")).toHaveLength(1);
  });

  it("marks lines with text markers (added line / removed line) and counts, not colour alone", () => {
    render(<SourceDiff before={before} after={after} />);
    expect(screen.getByLabelText("added line")).toBeTruthy();
    expect(screen.getByLabelText("removed line")).toBeTruthy();
    expect(screen.getByText("+1")).toBeTruthy();
    expect(screen.getByText("−1")).toBeTruthy();
  });
});

describe("PRIV-21 the browser's Global Privacy Control signal is sent once per page, only when the browser advertises it", () => {
  it("sends nothing unless the browser says GPC is on", async () => {
    const send = vi.fn().mockResolvedValue({ applied: false });
    for (const gpc of [undefined, false]) expect(await sendGlobalPrivacySignal({ gpc, memo: { sent: false }, send })).toBe(false);
    expect(send).not.toHaveBeenCalled();
  });

  it("sends once and remembers it for the rest of the page", async () => {
    const send = vi.fn().mockResolvedValue({ applied: true });
    const memo = { sent: false };
    expect(await sendGlobalPrivacySignal({ gpc: true, memo, send })).toBe(true);
    expect(await sendGlobalPrivacySignal({ gpc: true, memo, send })).toBe(false);
    expect(send).toHaveBeenCalledTimes(1);
  });

  it("a failed send is not remembered, so it is tried again, and it never throws", async () => {
    const memo = { sent: false };
    const failing = vi.fn().mockRejectedValue(new Error("down"));
    expect(await sendGlobalPrivacySignal({ gpc: true, memo, send: failing })).toBe(false);
    expect(memo.sent).toBe(false);
    const ok = vi.fn().mockResolvedValue({ applied: true });
    expect(await sendGlobalPrivacySignal({ gpc: true, memo, send: ok })).toBe(true);
  });
});
