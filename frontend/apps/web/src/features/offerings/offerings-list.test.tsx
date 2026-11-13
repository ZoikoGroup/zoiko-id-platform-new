import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { DELIVERY_RULE, OfferingsList } from "./offerings-list";

afterEach(cleanup);

describe("public offerings list", () => {
  it("lists each available offering with its countries", () => {
    render(
      <OfferingsList
        items={[{ offering_id: "GLOBAL_APIS_INTEGRATIONS", title: "Global APIs & integrations", wording: "Provide APIs and SDKs.", countries: ["GB", "US"] }]}
      />,
    );
    expect(screen.getByRole("heading", { name: "Global APIs & integrations" })).toBeTruthy();
    expect(screen.getByText("GB, US")).toBeTruthy();
  });

  it("says plainly that nothing is published yet instead of implying coverage", () => {
    render(<OfferingsList items={[]} />);
    expect(screen.getByRole("status").textContent).toContain("No offering is published");
  });

  it("never claims certification", () => {
    expect(DELIVERY_RULE).toContain("not production certification");
    expect(/certified\b/i.test(DELIVERY_RULE)).toBe(false);
  });
});
