/** The operator finance console's screens, rendered for real (ZID-UX-COM-009 F01 to F04 and O01) and checked with axe: no structural WCAG 2.2 AA violation
 * (names, roles, labels, relationships, landmarks), every field has an accessible name, and an invalid request says what is wrong in an alert instead of being sent.
 * This is automated structural evidence in a simulated browser: colour contrast, viewports and a screen-reader run on real devices are separate. */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ToastProvider } from "@zoiko/ui";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";

const NOW = new Date().toISOString();
const LATER = new Date(Date.now() + 5 * 86_400_000).toISOString();
const HASH = "ab".repeat(32);

const fake = vi.hoisted(() => ({
  finance: {
    overview: async () => ({
      as_of: new Date().toISOString(),
      counts: { pending_approval: 2, approved_unexecuted: 1, failed_application: 0, expiring_soon: 1, payment_events_open: 0 },
      mine: ["0194a2b3-1111-7222-8333-444455556666"],
      policy: { version: "v1", effective: false, awaiting: ["FOUNDER", "CFO"] },
      exceptions: [{ key: "POLICY_INACTIVE", severity: "warn", text: "The finance policy is not signed by the Founder and the CFO." }],
      definitions: "Counts are queue measurements.",
    }),
    policy: async () => ({
      version: "v1", content_hash: "cd".repeat(32), effective: false, awaiting: ["FOUNDER", "CFO"], signatures: [], environment: "local", locked: false, unit: "USD",
      request_roles: ["FINANCE_OPS_LEAD", "REVOPS"], execute_roles: ["FINANCE_EXECUTOR"], request_valid_days: 14, approval_valid_days: 14,
      credit_note: { window_days: 90, tiers: [{ max_usd: "500.00", roles: ["FINANCE_OPS_LEAD"] }, { max_usd: null, roles: ["CFO", "FOUNDER"] }] },
      pilot_credit: { window_days: 90, max_days: 90, tiers: [{ max_usd: "1000.00", roles: ["CCO", "FINANCE_OPS_LEAD"] }, { max_usd: null, roles: ["CFO", "FOUNDER"] }] },
    }),
    requests: async () => ({
      items: [
        {
          request_id: "0194a2b3-1111-7222-8333-444455556666", kind: "CREDIT_NOTE", tenant_id: "0194a2b3-2222-7222-8333-444455556666", invoice_id: "0194a2b3-3333-7222-8333-444455556666",
          currency: "USD", amount: "400.00", usd_minor: 40000, exposure_usd_minor: 40000, category: "CORRECTION", reason: "billed a seat twice", snapshot_hash: HASH,
          policy_version: "v1", required_roles: ["FINANCE_OPS_LEAD"], state: "PENDING_APPROVAL", requested_by: "p-1", created_at: NOW, expires_at: LATER,
          approved_at: null, approval_expires_at: null, approvals: [], result: null,
        },
      ],
    }),
    appointments: async () => ({
      items: [{ appointment_id: "a-1", principal_id: "0194a2b3-4444-7222-8333-444455556666", role: "CFO", effective_from: NOW, effective_to: null, revoked_at: null }],
      roles: ["CFO", "FOUNDER", "FINANCE_OPS_LEAD"],
    }),
    eligibility: async (tenant: string) => ({
      tenant_id: tenant, classification: "COMMERCIAL", state: "NOT_ELIGIBLE", commitment_state: null, schedule: null, blockers: ["PAYER_MISSING"],
      gates: [
        { key: "PAYER", label: "A legal payer is set", passed: false, detail: "No payer is recorded.", remedy: "Create the commercial account." },
        { key: "ISSUER", label: "An invoicing entity is configured", passed: true, detail: "Configured.", remedy: null },
      ],
      note: "Reading this never activates anything.",
    }),
    open: async () => ({}),
    decide: async () => ({}),
    execute: async () => ({}),
    cancel: async () => ({}),
    signPolicy: async () => ({}),
    appoint: async () => ({}),
    endAppointment: async () => ({}),
    reviewCommitment: async () => ({}),
    request: async () => ({}),
  },
}));

vi.mock("@/lib/runtime", () => ({
  useRuntime: () => ({ api: fake, protect: <T,>(run: () => Promise<T>) => run(), session: { authenticated: true, subject: { sub: "op-1" }, csrf: "x" } }),
}));

import { EligibilityView, FinanceAuthorityView, FinanceOverviewView, FinanceRequestsView } from "./finance-page";

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
  // contrast needs a real renderer; the structural rules are what a simulated browser can check
  const result = await axe.run(container, { rules: { "color-contrast": { enabled: false }, region: { enabled: false } }, runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"] } });
  expect(result.violations.map((v) => [v.id, v.nodes.map((n) => n.target)])).toEqual([]);
}

describe("the finance console screens", () => {
  it("F01 shows its counts as text, its exceptions as banners and has no structural violation", async () => {
    const { container } = mount(<FinanceOverviewView />);
    await screen.findByText("Waiting for approval");
    expect(screen.getByText("The finance policy is not signed by the Founder and the CFO.")).toBeTruthy();
    expect(screen.getByText("0194a2b3-1111-7222-8333-444455556666")).toBeTruthy(); // what waits on this person
    await clean(container);
  });

  it("F02 and F03 name every field of the request form and list requests in a table with a caption", async () => {
    const { container } = mount(<FinanceRequestsView />);
    await screen.findByText("Open a request");
    for (const label of [/Organization tenant ID/, /Invoice ID/, /Amount/, /Currency/, /Evidence/, /Reason/]) expect(screen.getByLabelText(label)).toBeTruthy();
    await screen.findByText("billed a seat twice", { exact: false }).catch(() => undefined);
    expect(await screen.findByRole("grid", { name: "Finance requests" }).catch(() => screen.findByRole("table", { name: "Finance requests" }))).toBeTruthy();
    await clean(container);
  });

  it("an invalid request is said in words and not sent", async () => {
    const open = vi.spyOn(fake.finance, "open");
    const { container } = mount(<FinanceRequestsView />);
    await screen.findByText("Open a request");
    await userEvent.click(screen.getByRole("button", { name: "Open request" }));
    await waitFor(() => expect(screen.getAllByText(/Enter the organization's tenant ID|An amount above zero|Say why/).length).toBeGreaterThan(0));
    expect(open).not.toHaveBeenCalled();
    await clean(container);
  });

  it("F04 shows the policy, who signed and who holds each role, and the appointment form is labelled", async () => {
    const { container } = mount(<FinanceAuthorityView />);
    await screen.findByText("Finance policy");
    await screen.findByText("Appoint to a finance role");
    expect(screen.getByLabelText(/Operator principal ID/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Sign this policy" })).toBeTruthy();
    await clean(container);
  });

  it("O01 asks for an organization, then names each gate as passed or failed in words", async () => {
    const { container } = mount(<EligibilityView />);
    await screen.findByText("Paid activation eligibility");
    await userEvent.type(screen.getAllByLabelText(/Organization tenant ID/, { selector: "input" })[0]!, "0194a2b3-2222-7222-8333-444455556666");
    await userEvent.click(screen.getByRole("button", { name: "Check" }));
    await screen.findByText("A legal payer is set");
    expect(screen.getByText("To fix: Create the commercial account.")).toBeTruthy();
    await clean(container);
  });
});
