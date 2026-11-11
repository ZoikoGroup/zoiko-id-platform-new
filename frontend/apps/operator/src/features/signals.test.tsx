/** The signals queue, rendered for real against a mocked client. */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError } from "@zoiko/api-client";
import { ToastProvider } from "@zoiko/ui";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

const HASH = "cd".repeat(32);
const NOW = new Date().toISOString();
const base = { dedupe_key: "k", notify_state: "SENT", outcome: null, resolved_by: null, rationale: null, evidence_ref: null, created_at: NOW };
const velocity = {
  ...base, review_id: "r-1", signal_id: "s-1", status: "OPEN", source: "VELOCITY", rule: "velocity.device.signup", risk_tier: "ELEVATED",
  detail: { rule: "velocity.device.signup", dimension: "DEVICE", action: "SIGNUP", count: 5, threshold: 3, window_seconds: 3600, key_hash: HASH },
};
const media = {
  ...base, review_id: "r-2", signal_id: "s-2", status: "CHALLENGED", notify_state: "FAILED", source: "ADVERSE_MEDIA", rule: "adverse_media.hit", risk_tier: "HIGH",
  detail: { provider: "acme-screen", queried_at: NOW, outcome: "HIT", hit_count: 2, severity: "HIGH", rationale: "two articles", reasons: ["Named in a fraud report"] },
};

const fake = vi.hoisted(() => ({
  items: [] as unknown[],
  signalReviews: vi.fn(),
  challengeSignal: vi.fn(),
  resolveSignal: vi.fn(),
}));

vi.mock("@/lib/runtime", () => ({
  useRuntime: () => ({ api: fake, protect: <T,>(run: () => Promise<T>) => run(), session: { authenticated: true, subject: { sub: "op-1" }, csrf: "x" } }),
}));

import { SignalsPage } from "./signals";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function mount() {
  fake.signalReviews.mockImplementation(async () => ({ items: fake.items }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <main>
          <SignalsPage />
        </main>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe("signals page", { timeout: 20_000 }, () => {
  it("lists the queue and warns about a failed notification", async () => {
    fake.items = [velocity, media];
    mount();
    expect(await screen.findByText("5 sign-ups from one device in 1 hour (limit 3)")).toBeTruthy();
    expect(screen.getByText(/Adverse media: 2 possible matches/)).toBeTruthy();
    expect(screen.getByText("Some notifications failed")).toBeTruthy();
    expect(screen.getAllByText("Notification failed").length).toBeGreaterThan(0);
  });

  it("shows an empty state when nothing is waiting", async () => {
    fake.items = [];
    mount();
    expect(await screen.findByText("Nothing is waiting")).toBeTruthy();
  });

  it("shows the whole detail with a shortened hash and never the raw key", async () => {
    fake.items = [velocity];
    mount();
    await userEvent.click(await screen.findByRole("button", { name: /Review signal/ }));
    expect(await screen.findByText("Threshold")).toBeTruthy();
    const hash = screen.getByTitle(HASH);
    expect(hash.textContent).not.toBe(HASH);
  });

  it("submits a challenge with the right body", async () => {
    fake.items = [velocity];
    fake.challengeSignal.mockResolvedValue({ ...velocity, status: "CHALLENGED" });
    mount();
    await userEvent.click(await screen.findByRole("button", { name: /Review signal/ }));
    const form = await screen.findByRole("form", { name: "Challenge" });
    const [statement, ref] = Array.from(form.querySelectorAll("textarea,input")) as [HTMLInputElement, HTMLInputElement];
    await userEvent.type(statement, "The same office shares one device for sign-ups.");
    await userEvent.type(ref, "TICKET-12");
    await userEvent.click(screen.getByRole("button", { name: "Record challenge" }));
    await waitFor(() => expect(fake.challengeSignal).toHaveBeenCalledWith("r-1", { statement: "The same office shares one device for sign-ups.", evidence_ref: "TICKET-12" }));
  });

  it("submits a resolution and shows the server's error", async () => {
    fake.items = [velocity];
    fake.resolveSignal.mockRejectedValue(new ApiError({ status: 409, code: "SIGNAL_ALREADY_RESOLVED", message: "Already resolved by another reviewer." }));
    mount();
    await userEvent.click(await screen.findByRole("button", { name: /Review signal/ }));
    const form = await screen.findByRole("form", { name: "Resolve" });
    const [rationale, ref] = Array.from(form.querySelectorAll("textarea,input:not([type=hidden])")).filter((e) => e.getAttribute("aria-hidden") !== "true") as [HTMLInputElement, HTMLInputElement];
    await userEvent.type(rationale, "Checked the account history, nothing odd.");
    await userEvent.type(ref, "CASE-77");
    await userEvent.click(screen.getByRole("button", { name: "Resolve signal" }));
    await waitFor(() => expect(fake.resolveSignal).toHaveBeenCalledWith("r-1", { outcome: "CLEARED_BY_REVIEWER", rationale: "Checked the account history, nothing odd.", evidence_ref: "CASE-77" }));
    expect(await screen.findByText(/Someone already resolved this signal/)).toBeTruthy();
    expect(screen.getByText("Already resolved by another reviewer.")).toBeTruthy();
  });
});
