/** A retired screen says so, does nothing, and points to the one operator screen still needed for verification: Case reviews. */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { RetiredPage } from "./retired";

afterEach(cleanup);

describe("a retired verification screen", () => {
  it("explains that nothing is waiting for an operator and offers Case reviews", () => {
    render(<RetiredPage title="Policy packs" what="Per-country policy packs were released and activated here." />);
    expect(screen.getByRole("heading", { name: "Policy packs" })).toBeTruthy();
    expect(screen.getByText(/no longer wait for an operator to release or activate anything/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Open Case reviews" }).getAttribute("href")).toBe("/case-reviews");
  });

  it("has no button that releases, approves or activates anything", () => {
    render(<RetiredPage title="Market releases" what="x" />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByText(/^(Activate|Approve|Publish|Release)/)).toBeNull();
  });
});
