/** Refunds, disputes and the reconciliation board, rendered for real (ZID-UX-COM-009 F03, F04) and checked with axe: no structural WCAG 2.2 AA violation, every field has an
 * accessible name, an invalid refund is said in words and not sent, and a state is never worded as more than the processor has said. Automated structural evidence in a simulated
 * browser: colour contrast, viewports and a screen-reader run on real devices are separate. */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ToastProvider } from "@zoiko/ui";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";

const NOW = new Date().toISOString();
const HASH = "ab".repeat(32);
const ID = "0194a2b3-1111-7222-8333-444455556666";

const refund = {
  refund_id: ID, tenant_id: ID, invoice_id: ID, payment_attempt_id: ID, issuing_entity: "ZOIKO", currency: "USD", amount_minor: 3000, usd_minor: 3000, exposure_usd_minor: 3000,
  category: "GOODWILL", reason: "duplicate click", evidence_ref: null, credit_note_id: null, snapshot_hash: HASH, policy_version: "v1", required_roles: ["CONTROLLER"], state: "UNKNOWN",
  label: "Outcome unknown: a controlled investigation is open", never_claim: "Safe to issue a second refund", requested_by: "p-1", created_at: NOW, expires_at: NOW, approved_at: NOW,
  approval_expires_at: NOW, approvals: [{ role: "CONTROLLER", principal_id: "p-2", decision: "APPROVE", decided_at: NOW }],
  processor: { refund_ref: null, state: null, amount_minor: null, last_verified_at: null, failure_code: "PROVIDER_UNAVAILABLE", attempts: 1 },
  overlap: { open_dispute: false, credited_minor: 0, refunded_other_minor: 0, refundable_minor: 2000 },
};
const dispute = {
  dispute_id: ID, provider_dispute_ref: "dp_1", merchant_ref: "", livemode: false, tenant_id: ID, invoice_id: ID, issuing_entity: "ZOIKO", payment_intent_ref: "pi_1", reason: "fraudulent",
  amount_minor: 12000, currency: "USD", provider_status: "needs_response", deadline: { due_by: new Date(Date.now() + 9 * 86_400_000).toISOString(), zone: "UTC", seconds_left: 777_600, status: "OK" },
  state: "OPEN", owner: null, strategy: null, strategy_reason: null,
  evidence: { version: 0, complete: false, items: { terms_or_consent: { present: false, ref: null }, transaction_proof: { present: true, ref: "EV-1" }, usage_proof: { present: false, ref: null }, communications: { present: false, ref: null }, cancellation_refund_chronology: { present: false, ref: null } } },
  submission: { reference: null, recorded_at: null, verified_at: null }, outcome: { provider_status: null, fee_minor: null, funds_withdrawn_minor: 0 }, overlap: { refunds: [] },
  timeline: [{ kind: "CREATED", actor: null, detail: {}, at: NOW }], last_synced_at: NOW,
};
const board = {
  issuing_entity: "ZOIKO", period: "2026-10", state: "OPEN",
  rows: [{ kind: "PAYMENT", item_id: ID, ref: "pi_1", amount_minor: 5000, currency: "USD", occurred_on: "2026-10-10", state: "PROVISIONALLY_MATCHED", reason: "bank evidence is outstanding", difference_minor: 0, payout_ref: "po_1", legs: ["PAYOUT_LINE"], resolved: false }],
  summary: { UNMATCHED: 0, PROVISIONALLY_MATCHED: 1, MATCHED: 0, items: 1 }, settlement_note: "Provider receipt, payout, bank settlement and the ledger are separate facts. None of these totals is revenue.",
  totals_minor: { USD: { payments: 5000, refunds: 0, disputes: 0 } }, blockers: ["PAYMENT x: bank evidence is outstanding"], ready: false, snapshot_hash: HASH,
};

const fake = vi.hoisted(() => ({ payments: {} as Record<string, unknown> }));
Object.assign(fake.payments, {
  refunds: async () => ({ items: [refund] }),
  refund: async () => refund,
  disputes: async () => ({ items: [dispute] }),
  dispute: async () => dispute,
  board: async () => board,
  exceptions: async () => ({ items: [{ exception_id: ID, source_kind: "PAYMENT", source_id: ID, reason: "bank evidence is outstanding", owner: null, resolution: null, resolved_at: null }] }),
  openRefund: vi.fn(async () => refund),
});

vi.mock("@/lib/runtime", () => ({
  useRuntime: () => ({ api: fake, protect: <T,>(run: () => Promise<T>) => run(), session: { authenticated: true, subject: { sub: "op-1" }, csrf: "x" } }),
}));

import { DisputesView, ReconciliationView, RefundsView } from "./payments-page";

afterEach(cleanup);

function mount(ui: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <main>{ui}</main>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

async function clean(container: HTMLElement) {
  const result = await axe.run(container, { rules: { "color-contrast": { enabled: false }, region: { enabled: false } }, runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"] } });
  expect(result.violations.map((v) => [v.id, v.nodes.map((n) => n.target)])).toEqual([]);
}

describe("refunds, disputes and reconciliation screens", () => {
  it("F03-A names every field, lists refunds in a captioned table and an invalid request is said in words, not sent", async () => {
    const { container } = mount(<RefundsView />);
    await screen.findByText("Ask for a refund");
    for (const label of [/Organization tenant ID/, /Payment ID/, /Amount/, /Category/, /Evidence reference/, /Reason/]) expect(screen.getAllByLabelText(label).length).toBeGreaterThan(0);
    expect(await screen.findByRole("grid", { name: "Refunds" }).catch(() => screen.findByRole("table", { name: "Refunds" }))).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Ask for the refund" }));
    await waitFor(() => expect(screen.getAllByText(/Enter the organization's tenant ID|An amount above zero|Say why/).length).toBeGreaterThan(0));
    expect(fake.payments.openRefund).not.toHaveBeenCalled();
    await clean(container);
  });

  it("an unknown refund says it is unknown and never offers a second send", async () => {
    mount(<RefundsView />);
    await userEvent.click(await screen.findByRole("button", { name: "30.00 USD" }));
    expect(await screen.findByText(/Outcome unknown: a controlled investigation is open/)).toBeTruthy();
    expect(screen.getByText(/Do not issue another refund/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Queue it for sending/ })).toBeNull();
  });

  it("F03-B shows the processor's deadline and no outcome control, and has no structural violation", async () => {
    const { container } = mount(<DisputesView />);
    await screen.findByText(/A dispute is won, lost or closed by the processor/);
    expect(await screen.findByRole("grid", { name: "Disputes" }).catch(() => screen.findByRole("table", { name: "Disputes" }))).toBeTruthy();
    expect(screen.queryByRole("button", { name: /won|lost|mark/i })).toBeNull();
    await clean(container);
  });

  it("F04 states that provider confirmation is not bank settlement and lists what blocks a close", async () => {
    const { container } = mount(<ReconciliationView />);
    await screen.findByText(/not the statutory ledger/);
    await userEvent.type(screen.getByLabelText(/Issuing entity/), "ZOIKO");
    await userEvent.click(screen.getByRole("button", { name: "Open the board" }));
    await screen.findByText("What stops this period closing");
    expect(screen.getAllByText(/bank evidence is outstanding/).length).toBeGreaterThan(0);
    expect(screen.getByText(/None of these totals is revenue/)).toBeTruthy();
    await clean(container);
  });
});
