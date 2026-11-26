import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import { afterEach, describe, expect, it } from "vitest";

import { PeopleCalculator, type PeoplePlan } from "./people-calculator";

afterEach(cleanup);

const plans: PeoplePlan[] = [
  { code: "essentials", name: "Essentials", custom: false, rate_per_verification: "0.95", monthly_minimum: "49" },
  { code: "business", name: "Business", custom: false, rate_per_verification: "1.29", monthly_minimum: "149" },
  { code: "enterprise", name: "Enterprise", custom: true, rate_per_verification: null, monthly_minimum: null },
];
const est = (total: string, applied = false) => ({ plan: "Business", currency: "USD", verifications: 600, rate_per_verification: "1.29", usage: total, monthly_minimum: "149", total, minimum_applied: applied, table_version: "5.0-page", table_status: "PROPOSED", note: "n" });

describe("the Verify People calculator", () => {
  it("shows the cell's estimate, says the prices are proposed, and has no structural violations", async () => {
    const get = async () => ({ ok: true, data: { estimate: est("774.00") } });
    const { container } = render(<main><PeopleCalculator plans={plans} currency="USD" status="PROPOSED" get={get} /></main>);
    await waitFor(() => expect(screen.getByText("$774.00")).toBeTruthy());
    expect(screen.getByText(/may change/)).toBeTruthy();
    const r = await axe.run(container, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag22aa"] }, rules: { "color-contrast": { enabled: false } } });
    expect(r.violations).toEqual([]);
  });
  it("offers no number for a plan priced by agreement and never calls the cell for it", async () => {
    let calls = 0;
    const get = async () => {
      calls += 1;
      return { ok: true, data: { estimate: est("1.00") } };
    };
    render(<main><PeopleCalculator plans={plans} currency="USD" status="PROPOSED" get={get} /></main>);
    await waitFor(() => expect(calls).toBe(1));
    await userEvent.setup().selectOptions(screen.getByLabelText("Plan"), "enterprise");
    expect(screen.getByText(/priced by agreement/)).toBeTruthy();
    expect(calls).toBe(1);
  });
  it("says so plainly when the estimate is unavailable", async () => {
    render(<main><PeopleCalculator plans={plans} currency="USD" status="PROPOSED" get={async () => ({ ok: false })} /></main>);
    expect(await screen.findByText(/not available right now/)).toBeTruthy();
  });
});
