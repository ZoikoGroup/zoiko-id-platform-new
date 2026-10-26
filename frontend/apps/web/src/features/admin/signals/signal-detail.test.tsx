/** UX-13: a security signal's detail shows the state that was applied, its revision, and how far it has propagated. */
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const shared = vi.hoisted(() => ({ data: undefined as unknown }));

vi.mock("@/lib/cell", () => ({
  useCellQuery: () => ({ data: shared.data, isLoading: false, isError: false }),
  useCellMutation: () => ({ mutate: () => undefined, isPending: false, error: null }),
  QueryView: ({ query, children }: { query: { data: unknown }; children: (d: never) => ReactNode }) => children(query.data as never),
  ApiErrorView: () => null,
}));

import { SecurityStateSection } from "./security-state";
import { Propagation } from "./stream-detail";

afterEach(cleanup);

const propagation = {
  acknowledged: 7,
  pending: 2,
  p50_ms: 120,
  p99_ms: 900,
  max_ms: 1500,
  oldest_pending_ms: 8000,
  slo_breaches: 0,
  slo_ms_by_risk: { "3": 5000 },
};

describe("UX-13 signal detail", () => {
  it("shows the applied state and its revision", async () => {
    shared.data = {
      subject_type: "PRINCIPAL",
      subject_id: "00000000-0000-4000-8000-000000000001",
      state: "SUSPENDED",
      revision: 4,
      effective_at: "2026-10-26T10:00:00Z",
      committed_at: "2026-10-26T10:00:01Z",
    };
    render(<SecurityStateSection />);
    await userEvent.type(screen.getByLabelText(/subject id/i), "00000000-0000-4000-8000-000000000001");
    await userEvent.click(screen.getByRole("button", { name: /look up/i }));
    expect(screen.getAllByText(/suspended/i).length).toBeGreaterThan(0); // the state, also named for assistive tech
    expect(screen.getByLabelText("Security revision 4")).toBeTruthy(); // the revision the state was committed at
    expect(screen.getByText("Revision")).toBeTruthy();
  });

  it("shows propagation: how many are acknowledged, how many wait, and for how long", () => {
    shared.data = propagation;
    render(<Propagation streamId="s-1" />);
    expect(screen.getByText("Acknowledged")).toBeTruthy();
    expect(screen.getByText("Pending")).toBeTruthy();
    expect(screen.getByText("Oldest pending")).toBeTruthy();
    expect(screen.queryByText(/fail closed/i)).toBeNull();
  });

  it("says plainly when the remote side must fail closed (SIG-22)", () => {
    shared.data = { ...propagation, overdue_restricting: 1, remote_fail_closed: true };
    render(<Propagation streamId="s-1" />);
    expect(screen.getByText(/told to fail closed/i)).toBeTruthy();
    expect(screen.getByText(/1 restricting event not acknowledged/i)).toBeTruthy();
  });
});
