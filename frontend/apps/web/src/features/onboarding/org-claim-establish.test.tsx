/** The local/test-only company claim shortcut in the console: with it, a claim that reads as verified creates the organization by itself and moves on to its
 * setup; without it, nothing is created until the person asks, exactly as before. */
import type { OrgClaimStatus } from "@zoiko/api-client";
import { cleanup, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const shared = vi.hoisted(() => ({
  mutate: vi.fn(),
  replace: vi.fn(),
  onSuccess: undefined as undefined | (() => void),
}));

vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: shared.replace }) }));
vi.mock("@/lib/cell", () => ({
  useCellMutation: (_fn: unknown, opts: { onSuccess?: () => void }) => {
    shared.onSuccess = opts?.onSuccess;
    return { mutate: shared.mutate, isPending: false, isError: false, data: undefined, error: null };
  },
}));
vi.mock("@zoiko/ui", () => ({
  Banner: ({ title, children }: { title: string; children?: ReactNode }) => (
    <div role="status">
      {title}
      {children}
    </div>
  ),
  Button: ({ children, onPress }: { children: ReactNode; onPress?: () => void }) => <button onClick={onPress}>{children}</button>,
  ButtonLink: ({ children, href }: { children: ReactNode; href: string }) => <a href={href}>{children}</a>,
  Card: ({ title, children }: { title: string; children?: ReactNode }) => (
    <section>
      <h2>{title}</h2>
      {children}
    </section>
  ),
  Select: () => null,
  TextField: () => null,
}));

import { EstablishCard } from "./org-claim-review";

const status = (over: Partial<OrgClaimStatus>): OrgClaimStatus =>
  ({ claim_id: "c1", outcome: "VERIFIED", closed_at: null, development_shortcut: false, checks: {}, ...over }) as unknown as OrgClaimStatus;

describe("EstablishCard and the development shortcut", () => {
  beforeEach(() => {
    shared.mutate.mockReset();
    shared.replace.mockReset();
  });
  afterEach(cleanup);

  it("creates the organization by itself, once, and warns that nothing was checked", () => {
    const { rerender } = render(<EstablishCard claimId="c1" status={status({ development_shortcut: true })} />);
    expect(shared.mutate).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/Development shortcut/)).toBeTruthy();
    expect(screen.getByText(/not checked against a register/)).toBeTruthy();
    rerender(<EstablishCard claimId="c1" status={status({ development_shortcut: true })} />);
    expect(shared.mutate).toHaveBeenCalledTimes(1); // never in a loop
  });

  it("goes straight on to the organization setup when it is established", () => {
    render(<EstablishCard claimId="c1" status={status({ development_shortcut: true })} />);
    shared.onSuccess?.();
    expect(shared.replace).toHaveBeenCalledWith("/organizations/current/setup");
  });

  it("without the shortcut it waits for the person to press the button", () => {
    render(<EstablishCard claimId="c1" status={status({})} />);
    expect(shared.mutate).not.toHaveBeenCalled();
    expect(screen.getByText("Create the organization")).toBeTruthy();
    shared.onSuccess?.();
    expect(shared.replace).not.toHaveBeenCalled();
  });

  it("does nothing for a claim that is not verified, or one that is closed", () => {
    render(<EstablishCard claimId="c1" status={status({ development_shortcut: true, outcome: "PROVISIONAL" })} />);
    expect(shared.mutate).not.toHaveBeenCalled();
    cleanup();
    render(<EstablishCard claimId="c1" status={status({ development_shortcut: true, closed_at: "2026-11-10T00:00:00Z" })} />);
    expect(shared.mutate).not.toHaveBeenCalled();
  });
});
