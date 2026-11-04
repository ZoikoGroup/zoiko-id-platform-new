/** The step-up confirmation dialog, rendered for real (ZID-ENG-DEC-009; EDR09-T12). It must be a labelled modal dialog with no structural WCAG 2.2 AA violation, say exactly
 * what will happen and how long is left, name its buttons, keep keyboard focus inside and return it, and Escape must cancel without granting anything. Automated structural
 * evidence in a simulated browser: a screen-reader run on real devices is separate. */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: () => undefined }) }));

import { RuntimeProvider, useRuntime } from "./runtime";

const EXPIRES = new Date(Date.now() + 90_000).toISOString();
const CHALLENGE = {
  required: true,
  challenge_id: "chal-1",
  tier: "S2",
  action: "billing.grant.give",
  resource: "tenant:acme",
  summary: "Give Priya Billing Admin access until 30 November",
  command_hash: "ab".repeat(32),
  requested_assurance: "AAL3",
  reasons: ["RISK_TIER_S2"],
  expires_at: EXPIRES,
  policy_version: "v1",
  why: "A change to who can see or move money needs a fresh proof from you.",
  public_key: { challenge: "Zm9v", rpId: "localhost", allowCredentials: [] },
};

const calls: string[] = [];

beforeEach(() => {
  calls.length = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push(`${init?.method ?? "GET"} ${url}`);
      const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
      if (url.includes("/api/auth/session")) return json({ authenticated: true, csrf: "x", subject: { sub: "p-1" } });
      if (url.includes("/step-up/challenges/chal-1/cancel")) return json({ challenge_id: "chal-1", status: "CANCELED", grants_nothing: true });
      if (url.includes("/step-up/challenges")) return json(CHALLENGE);
      return json({});
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function Starter({ onResult }: { onResult: (r: string) => void }) {
  const { proveAction } = useRuntime();
  return (
    <main>
      <button type="button" onClick={() => proveAction({ action: "billing.grant.give", resource: "tenant:acme" }).then((p) => onResult(`proof:${p}`), (e: Error) => onResult(`rejected:${e.name}`))}>
        Give access
      </button>
    </main>
  );
}

function mount(onResult: (r: string) => void) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <RuntimeProvider clientId="web">
        <Starter onResult={onResult} />
      </RuntimeProvider>
    </QueryClientProvider>,
  );
}

describe("the step-up confirmation dialog (EDR09-T12)", () => {
  it("opens as a named modal dialog that says what will happen and has no structural violation", async () => {
    mount(() => undefined);
    await userEvent.click(screen.getByRole("button", { name: "Give access" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog.getAttribute("aria-labelledby") || dialog.getAttribute("aria-label")).toBeTruthy();
    expect(screen.getByText("Give Priya Billing Admin access until 30 November")).toBeTruthy();
    expect(screen.getByRole("timer").textContent).toMatch(/Time left to confirm: \d+:\d{2}/);
    expect(screen.getByRole("button", { name: "Use my passkey" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeTruthy();
    expect(screen.getByText("Why this is required")).toBeTruthy();
    const result = await axe.run(dialog, {
      rules: { "color-contrast": { enabled: false }, region: { enabled: false } },
      runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"] },
    });
    expect(result.violations.map((v) => [v.id, v.nodes.map((n) => n.target)])).toEqual([]);
  });

  it("keeps keyboard focus inside the dialog and Escape cancels without granting anything", async () => {
    const results: string[] = [];
    mount((r) => results.push(r));
    await userEvent.click(screen.getByRole("button", { name: "Give access" }));
    const dialog = await screen.findByRole("dialog");
    for (let i = 0; i < 6; i += 1) {
      await userEvent.tab();
      expect(dialog.contains(document.activeElement)).toBe(true);
    }
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await waitFor(() => expect(results).toEqual(["rejected:StepUpCancelled"]));
    expect(calls.some((c) => c.includes("/step-up/challenges/chal-1/cancel"))).toBe(true); // the server is told it grants nothing
  });
});
