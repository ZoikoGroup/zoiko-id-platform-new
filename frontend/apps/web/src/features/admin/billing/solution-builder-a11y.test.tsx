/** The B06 panel rendered for real in each step (ZID-COM-WF-2026-11 WF-C02, CAT-38). Structural WCAG 2.2 AA checks with axe in a simulated browser, plus what each step
 * must say. A screen-reader run on real devices is separate evidence. */
import type { CapabilityDependencies, OfferCard, QuotePreview } from "@zoiko/api-client";
import { cleanup, render, screen } from "@testing-library/react";
import axe from "axe-core";
import { afterEach, describe, expect, it } from "vitest";

import { EMPTY_DRAFT, STEPS, type Draft, type StepId } from "./solution-builder";
import { SolutionBuilderView, type BuilderProps } from "./solution-builder-view";

afterEach(cleanup);

const card = (family: string, name: string, over: Partial<OfferCard> = {}): OfferCard => ({
  family, name, unit: "check", sales_qualifier: "q", regulated: false, depends_on: [], sellable: false, next_action: "REQUEST_QUOTE",
  causes: [], refusals: [], state: "MARKET_REQUIRED", reasons: [], market: null, ...over,
});

const catalogue = [card("IDV", "Identity verification"), card("SIGN", "Electronic signatures", { regulated: true, depends_on: ["IDV"] }), card("API", "Developer integrations")];
const deps: CapabilityDependencies = {
  version: "t",
  families: [
    { family: "IDV", depends_on: [], needs_provider: true, regulated: false },
    { family: "SIGN", depends_on: ["IDV"], needs_provider: true, regulated: true },
    { family: "API", depends_on: [], needs_provider: false, regulated: false },
  ],
};
const preview: QuotePreview = {
  binding: false, configurable: true, scope_hash: "ab".repeat(32), market_matrix_version: 3, problems: [], estimate: null,
  scope: { purpose: "ONBOARDING", families: ["API", "SIGN"], subject_country: "DE", payer_country: "DE", operating_region: "eu-central", volumes: {} },
  families: [
    card("API", "Developer integrations", { state: "AVAILABLE", sellable: true }),
    card("SIGN", "Electronic signatures", { state: "NOT_ELIGIBLE", regulated: true, reasons: [{ cause: "LEGAL", text: "The legal basis or the regulated status for this market is not proven." }], provider_attribution: "A regulated service is provided by an authorised partner where one is recorded for the market; Zoiko iD does not itself hold qualified status." }),
  ],
  request_quote: ["SIGN"],
  assumptions: ["Volumes are the buyer's forecast. A forecast is never billed usage.", "No rate is applied: a binding price needs a signed quote or order form."],
};

function props(step: StepId, draft: Partial<Draft> = {}, over: Partial<BuilderProps> = {}): BuilderProps {
  return {
    catalogue, deps, draft: { ...EMPTY_DRAFT, purpose: "ONBOARDING", families: ["API", "SIGN"], subjectCountry: "DE", payerCountry: "DE", region: "eu-central", ...draft },
    onDraft: () => undefined, step, onStep: () => undefined, preview, previewPending: false, previewError: null, saved: undefined,
    onSave: () => undefined, onRequest: () => undefined, saving: false, requesting: false, mayRequest: true, ...over,
  };
}

async function violations(container: HTMLElement) {
  const r = await axe.run(container, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] }, rules: { "color-contrast": { enabled: false } } });
  return r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
}

describe("the solution builder in every step", () => {
  it.each(STEPS.map((s) => s.id))("has no structural accessibility violation in the %s step", async (step) => {
    const { container } = render(<main><SolutionBuilderView {...props(step)} /></main>);
    expect(await violations(container)).toEqual([]);
  });

  it("marks the current step and shows no price anywhere", () => {
    const { container } = render(<SolutionBuilderView {...props("review")} />);
    expect(screen.getByRole("button", { name: /4\. Review/ }).getAttribute("aria-current")).toBe("step");
    expect(container.textContent).not.toMatch(/[$€£]\s?\d|\bper (month|check)\b/i);
    expect(container.textContent).toMatch(/signed quote or order form/);
  });

  it("tells the person what a service needs and stops them continuing without it", () => {
    render(<SolutionBuilderView {...props("services", { families: ["SIGN"] })} />);
    expect(screen.getByText(/Electronic signatures needs Identity verification/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Continue" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("tells coverage apart and says who provides a regulated service", () => {
    render(<SolutionBuilderView {...props("coverage")} />);
    expect(screen.getByText("Confirmed")).toBeTruthy();
    expect(screen.getByText("Unavailable")).toBeTruthy();
    expect(screen.getByText(/does not itself hold qualified status/)).toBeTruthy();
    expect(screen.getByText(/regulated status for this market is not proven/)).toBeTruthy();
  });

  it("lets only an administrator save or send, and only a saved draft is sent", () => {
    const { rerender } = render(<SolutionBuilderView {...props("review", {}, { mayRequest: false })} />);
    expect((screen.getByRole("button", { name: "Save draft" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/Only company administrators/)).toBeTruthy();
    rerender(<SolutionBuilderView {...props("review")} />);
    expect((screen.getByRole("button", { name: "Save draft" }) as HTMLButtonElement).disabled).toBe(false);
    expect((screen.getByRole("button", { name: "Send to the deal desk" }) as HTMLButtonElement).disabled).toBe(true);
    const saved = { draft_id: "d1", purpose: "ONBOARDING" as const, families: ["API"], subject_country: "DE", payer_country: "DE", operating_region: "eu-central", volumes: {}, scope_hash: "a".repeat(64), status: "DRAFT" as const, state: "DRAFT" as const, created_at: "2026-11-13T00:00:00Z", expires_at: "2026-12-13T00:00:00Z", quote_requested_at: null };
    rerender(<SolutionBuilderView {...props("review", {}, { saved })} />);
    expect((screen.getByRole("button", { name: "Send to the deal desk" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("shows a failed coverage check as an alert, not as an empty list", () => {
    render(<SolutionBuilderView {...props("coverage", {}, { preview: undefined, previewError: "Coverage could not be checked right now. Try again." })} />);
    expect(screen.getByRole("alert").textContent).toMatch(/could not be checked/);
  });
});
