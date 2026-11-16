/** Case reviews, rendered for real against a mocked client: a person rules, the system never does, and a screening decision can be decided again. */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError } from "@zoiko/api-client";
import { ToastProvider } from "@zoiko/ui";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

// jsdom has no CSS.escape, which the select list needs when it opens
if (typeof globalThis.CSS === "undefined" || typeof globalThis.CSS.escape !== "function") {
  (globalThis as unknown as { CSS: { escape: (v: string) => string } }).CSS = { escape: (v: string) => v };
}

const CASE = "11111111-2222-4333-8444-555555555555";
const detail = {
  case_id: CASE,
  status: "REVIEW_REQUIRED",
  reasons: ["PEP_MATCH"],
  versions: [
    { version: 1, status: "REVIEW_REQUIRED", decided_by: null },
    { version: 2, status: "REJECTED", decided_by: "reviewer-1" },
  ],
  alerts: [{ list_id: "PEP", score: 0.93, list_version: "v7", reasons: ["NAME_MATCH"] }],
  appeals: [],
  register: null,
};

const fake = vi.hoisted(() => ({
  caseReviews: { screening: vi.fn(), replay: vi.fn(), screeningRuling: vi.fn(), kybRuling: vi.fn() },
}));

vi.mock("@/lib/runtime", () => ({
  useRuntime: () => ({ api: fake, protect: <T,>(run: () => Promise<T>) => run(), session: { authenticated: true, subject: { sub: "op-1" }, csrf: "x" } }),
}));

import { CaseReviewsPage, errorText, isCaseId, mandateAllowed, rulingReady } from "./case-reviews";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <main>
          <CaseReviewsPage />
        </main>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe("rules in words", () => {
  it("accepts only a case id shaped like one and a reason of ten characters", () => {
    expect(isCaseId(CASE)).toBe(true);
    expect(isCaseId(` ${CASE.toUpperCase()} `)).toBe(true);
    expect(isCaseId("not-an-id")).toBe(false);
    expect(rulingReady("too short")).toBe(false);
    expect(rulingReady("a proper reason")).toBe(true);
  });
  it("lets only a verifying screening ruling confirm the mandate", () => {
    expect(mandateAllowed("SCREENING", "VERIFIED")).toBe(true);
    expect(mandateAllowed("SCREENING", "REJECTED")).toBe(false);
    expect(mandateAllowed("KYB", "VERIFIED")).toBe(false);
  });
  it("says a known refusal in words and an unknown one as itself", () => {
    expect(errorText(new ApiError({ status: 404, code: "CASE_NOT_FOUND", message: "x" }))).toBe("No case with this id.");
    expect(errorText(new ApiError({ status: 409, code: "SOMETHING_NEW", message: "m" }))).toBe("SOMETHING_NEW: m");
  });
});

describe("the page", { timeout: 20_000 }, () => {
  it("starts with no case open and will not open one without a proper id", async () => {
    mount();
    expect(screen.getByText("No case open")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Open case" }) as HTMLButtonElement).disabled).toBe(true);
    await userEvent.type(screen.getByLabelText(/Case id/), "nope");
    expect(screen.getByText(/looks like 8-4-4-4-12/)).toBeTruthy();
    expect(fake.caseReviews.screening).not.toHaveBeenCalled();
  });

  it("shows the alerts and versions of a screening case and records a ruling with the reason", async () => {
    fake.caseReviews.screening.mockResolvedValue(detail);
    fake.caseReviews.screeningRuling.mockResolvedValue({ case_id: CASE, status: "REJECTED", version: 3 });
    mount();
    await userEvent.type(screen.getByLabelText(/Case id/), CASE);
    await userEvent.click(screen.getByRole("button", { name: "Open case" }));
    expect(await screen.findByText(/A match is a reason to look, not a finding/)).toBeTruthy();
    expect(screen.getByText("PEP")).toBeTruthy();
    expect(screen.getByText("0.93")).toBeTruthy();
    expect(screen.getByText("person")).toBeTruthy();
    expect(screen.getByText("automatic")).toBeTruthy();

    const submit = screen.getByRole("button", { name: "Record ruling" }) as HTMLButtonElement;
    expect(submit.disabled).toBe(true); // no reason yet
    await userEvent.type(screen.getByLabelText(/Reason/), "Namesake: another birth year and country.");
    await userEvent.click(submit);
    await waitFor(() => expect(fake.caseReviews.screeningRuling).toHaveBeenCalled());
    expect(fake.caseReviews.screeningRuling).toHaveBeenCalledWith(CASE, { outcome: "REJECTED", rationale: "Namesake: another birth year and country.", confirm_mandate: false });
  });

  it("decides a past version again and says whether it matches", async () => {
    fake.caseReviews.screening.mockResolvedValue(detail);
    fake.caseReviews.replay.mockResolvedValue({ replayable: true, version: 1, matches: false });
    mount();
    await userEvent.type(screen.getByLabelText(/Case id/), CASE);
    await userEvent.click(screen.getByRole("button", { name: "Open case" }));
    await userEvent.click(await screen.findByRole("button", { name: "Decide version 1 again" }));
    expect(await screen.findByText("Version 1 does not decide the same again")).toBeTruthy();
    expect(fake.caseReviews.replay).toHaveBeenCalledWith(CASE, 1);
  });

  it("says a person's ruling is not replayed", async () => {
    fake.caseReviews.screening.mockResolvedValue(detail);
    fake.caseReviews.replay.mockResolvedValue({ replayable: false, version: 2, reason: "REVIEWER_RULING" });
    mount();
    await userEvent.type(screen.getByLabelText(/Case id/), CASE);
    await userEvent.click(screen.getByRole("button", { name: "Open case" }));
    await userEvent.click(await screen.findByRole("button", { name: "Decide version 2 again" }));
    expect(await screen.findByText("Version 2 cannot be decided again")).toBeTruthy();
    expect(screen.getByText(/ruling is not replayed/)).toBeTruthy();
  });

  it("says Withheld, not an empty list, to a role that may not review", async () => {
    fake.caseReviews.screening.mockRejectedValue(new ApiError({ status: 403, code: "protected_action_denied", message: "no" }));
    mount();
    await userEvent.type(screen.getByLabelText(/Case id/), CASE);
    await userEvent.click(screen.getByRole("button", { name: "Open case" }));
    expect(await screen.findByText("Withheld for your role")).toBeTruthy();
  });

  it("offers a company verification ruling with its own outcomes and no mandate box", async () => {
    fake.caseReviews.kybRuling.mockResolvedValue({ case_id: CASE, status: "VERIFIED", version: 2 });
    mount();
    await userEvent.click(screen.getByRole("button", { name: /Kind of case/ }));
    await userEvent.click(await screen.findByRole("option", { name: /Company verification/ }));
    await userEvent.type(screen.getByLabelText(/Case id/), CASE);
    await userEvent.click(screen.getByRole("button", { name: "Open case" }));
    expect(await screen.findByText("Company verification case")).toBeTruthy();
    expect(screen.queryByText(/delegated mandate/)).toBeNull();
    await userEvent.type(screen.getByLabelText(/Reason/), "Register extract matches the claim.");
    await userEvent.click(screen.getByRole("button", { name: "Record ruling" }));
    await waitFor(() => expect(fake.caseReviews.kybRuling).toHaveBeenCalledWith(CASE, { outcome: "REJECTED", rationale: "Register extract matches the claim." }));
  });
});
