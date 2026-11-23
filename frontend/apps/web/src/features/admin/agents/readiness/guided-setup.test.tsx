/** Guided setup page: an empty company is told its own next step; nothing is pressed or attested for the user. */
import { cleanup, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const ok = (data: unknown) => ({ isSuccess: true, isError: false, isPending: false, data });
vi.mock("@/lib/cell", () => ({
  useCellQuery: (key: string[]) => {
    if (key[0] === "tenant") return ok({ organization_id: "org-1" });
    if (key[0] === "admin-picker") return ok({ items: [{ principal_id: "srv-1", principal_type: "SERVICE", status: "ACTIVE" }] });
    if (key.includes("detail") || key.includes("api-binding")) return ok(undefined);
    return ok({ items: [] });
  },
  useCellMutation: () => ({ mutate: vi.fn(), isPending: false, error: null }),
  ApiErrorView: ({ error }: { error: Error }) => <div role="alert">{error.message}</div>,
}));
vi.mock("../../authority/shared", () => ({ useMe: () => "me-1" }));
vi.mock("../api-agent", () => ({ RegisterApiClientDialog: () => null }));
vi.mock("../pickers", () => ({ AuthorityNotice: () => null, PersonSelect: () => null }));
vi.mock("next/link", () => ({ default: ({ href, children }: { href: string; children: ReactNode }) => <a href={href}>{children}</a> }));

import { GuidedSetupPage } from "./guided-setup";

afterEach(cleanup);

describe("guided setup", () => {
  it("shows progress, the first step as the person's own, and the two-person explanation", () => {
    render(<GuidedSetupPage />);
    expect(screen.getByText(/0 of 12 steps done/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Create the two tools" })).toBeTruthy();
    expect(screen.getByText(/two people/)).toBeTruthy();
    expect(screen.getAllByText("Your turn").length).toBeGreaterThan(0);
  });

  it("lists all twelve steps", () => {
    render(<GuidedSetupPage />);
    expect(document.querySelectorAll("li[aria-current], ol:not([aria-label]) > li").length).toBe(12);
  });
});
