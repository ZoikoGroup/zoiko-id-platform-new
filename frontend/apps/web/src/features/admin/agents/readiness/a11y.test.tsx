/** AT-22 evidence without axe (the repo has only @axe-core/playwright for e2e): structural checks with testing-library on the real @zoiko/ui components,
 * and the keyboard paths. Every readiness screen in its main states: names on every control, a sane heading order, valid ARIA use, unique ids. */
import type { ReadinessEvidence, ReadinessProposal, ReadinessRunDetail, ReadinessSetup } from "@zoiko/api-client";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({ byKey: {} as Record<string, unknown>, saved: vi.fn() }));

vi.mock("@/lib/cell", () => ({
  useCellQuery: (key: string[]) => {
    const k = key.join("/");
    const hit = Object.entries(state.byKey).find(([prefix]) => k.endsWith(prefix));
    return hit?.[1] ?? { isPending: true, fetchStatus: "fetching" };
  },
  useCellMutation: () => ({ isPending: false, mutateAsync: (v: unknown) => Promise.resolve(state.saved(v) ?? {}) }),
  newIdempotencyKey: () => "key",
  ApiErrorView: ({ error }: { error: Error }) => <div role="alert">api error: {error.message}</div>,
}));
vi.mock("@/lib/runtime", () => ({ useRuntime: () => ({}) }));
vi.mock("../../authority/shared", () => ({ useMe: () => "me-1" }));
vi.mock("next/link", () => ({ default: ({ href, children, className }: { href: string; children: ReactNode; className?: string }) => <a href={href} className={className}>{children}</a> }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("../pickers", () => ({
  PersonSelect: ({ label }: { label: string }) => (
    <label>
      {label}
      <input />
    </label>
  ),
}));

import { ReadinessHomePage } from "./readiness-home";
import { ReviewProposalPage } from "./review-page";
import { RunDetailPage } from "./run-detail";
import { SetupWizardPage } from "./setup-wizard";

if (!(globalThis as { CSS?: unknown }).CSS) Object.defineProperty(globalThis, "CSS", { value: { escape: (v: string) => v.replace(/[^\w-]/g, (c) => `\\${c}`) } });

afterEach(cleanup);
beforeEach(() => {
  state.byKey = {};
  state.saved.mockReset();
});

const query = (data: unknown) => ({ isPending: false, isError: false, isSuccess: true, fetchStatus: "idle", data, refetch: vi.fn() });

const setup: ReadinessSetup = {
  setup_id: "s1",
  agent_id: "agent-1",
  display_name: "Access Readiness",
  purpose: "access_posture_review",
  scope: { resource_kinds: ["mandates"], max_records: 100, warning_window_days: 14, home_region: "eu" },
  rule_set_version: "r1",
  policy_version: "p1",
  status: "SANDBOX_READY",
  draft_revision: 1,
  scope_digest: `sha256:${"a".repeat(64)}`,
  validated: true,
  paused_reason: null,
  approval: null,
  permitted_tools: ["read_mandate_metadata"],
  blocked: ["grant_role"],
  agent: { status: "ACTIVE", sponsor_active: true, attested: true, blueprint: "b@1" },
};
const proposal: ReadinessProposal = {
  proposal_id: "p1",
  submitted_by: { type: "agent", agent_id: "agent-1" },
  target: "mandate:1234abcd...",
  rule_id: "R-01",
  rule_version: "r1",
  finding_code: "EXPIRED_MANDATE",
  severity: "HIGH",
  confidence: "DETERMINISTIC",
  evidence_refs: ["evidence:1"],
  run_id: "run-1",
  status: "OPEN",
  reviewed_by: null,
  assignee: null,
  created_at: "2026-11-14T10:00:00Z",
  handoff: null,
  notice: "x",
};
const runDetail = {
  run_id: "run-1", agent_id: "agent-1", state: "COMPLETED", attempt: 1, mode: "m", purpose: "p", resource_selector: {}, denial_codes: [],
  summary: { records_read: 3, findings: 1, proposals_created: 1, proposals_existing: 0, privileges_changed: 0 }, rule_set_version: "r1", policy_hash: "h",
  run_definition_hash: "d", region: "eu", trace_id: null, worker_attestation_id: "att", created_at: "2026-11-14T10:00:00Z", terminal_at: null, links: { self: "/" }, proposals: [proposal],
} as unknown as ReadinessRunDetail;
const evidence: ReadinessEvidence = { verified: true, integrity_exceptions: [], items: [] };

/** A small accessible-name computation: aria-labelledby, aria-label, a wrapping or `for` label, then content and title. Enough to catch an unnamed control. */
function computeAccessibleName(el: HTMLElement): string {
  const by = (el.getAttribute("aria-labelledby") ?? "").split(/\s+/).filter(Boolean).map((id) => el.ownerDocument.getElementById(id)?.textContent ?? "").join(" ");
  if (by.trim()) return by;
  const label = el.getAttribute("aria-label");
  if (label?.trim()) return label;
  const labels = (el as HTMLInputElement).labels ? [...(el as HTMLInputElement).labels!].map((l) => l.textContent ?? "").join(" ") : "";
  if (labels.trim()) return labels;
  return el.textContent?.trim() || el.getAttribute("title") || "";
}

/** The structural rules axe would check on these screens, in plain DOM terms. Returns the violations found. */
function audit(root: HTMLElement): string[] {
  const out: string[] = [];
  for (const el of root.querySelectorAll<HTMLElement>("button, a[href], input:not([type=hidden]), select, textarea, [role=tab], [role=button]")) {
    if (el.closest("[aria-hidden=true]")) continue;
    if (!computeAccessibleName(el).trim()) out.push(`no accessible name: <${el.tagName.toLowerCase()} ${el.className.slice(0, 40)}>`);
  }
  const levels = [...root.querySelectorAll("h1,h2,h3,h4,h5,h6")].map((h) => Number(h.tagName[1]));
  if (levels.filter((l) => l === 1).length > 1) out.push("more than one h1");
  levels.forEach((l, i) => {
    if (i > 0 && l > levels[i - 1]! + 1) out.push(`heading level skipped: h${levels[i - 1]} to h${l}`);
  });
  for (const el of root.querySelectorAll<HTMLElement>("[aria-invalid]")) {
    const role = el.getAttribute("role");
    const okTag = ["INPUT", "SELECT", "TEXTAREA"].includes(el.tagName) || ["textbox", "combobox", "checkbox", "listbox", "radiogroup", "spinbutton", "switch", "slider", "searchbox"].includes(role ?? "");
    if (!okTag) out.push(`aria-invalid on <${el.tagName.toLowerCase()}>`);
  }
  for (const el of root.querySelectorAll<HTMLElement>("p[aria-label], span[aria-label], div[aria-label]:not([role])")) out.push(`aria-label on a generic <${el.tagName.toLowerCase()}>`);
  const ids = [...root.querySelectorAll("[id]")].map((e) => e.id);
  for (const id of new Set(ids.filter((x, i) => ids.indexOf(x) !== i))) out.push(`duplicate id ${id}`);
  for (const el of root.querySelectorAll<HTMLElement>("[aria-labelledby],[aria-describedby]")) {
    for (const attr of ["aria-labelledby", "aria-describedby"]) {
      for (const id of (el.getAttribute(attr) ?? "").split(/\s+/).filter(Boolean)) if (!root.ownerDocument.getElementById(id)) out.push(`${attr} points at missing #${id}`);
    }
  }
  for (const img of root.querySelectorAll("img:not([alt])")) out.push(`img without alt: ${img.outerHTML.slice(0, 40)}`);
  return out;
}

const wizardState = (s: ReadinessSetup | null) => {
  state.byKey = { "admin/agents/readiness": query({ items: s ? [s] : [] }), "admin/agents/": query({ items: [{ agent_principal_id: "agent-1", agent_name: "Reader", status: "ACTIVE", sponsor_principal_id: "sp-1", parent_agent_id: null }] }) };
};

describe("AT-22 structure", () => {
  it("setup wizard: every stage", async () => {
    wizardState(setup);
    const { container } = render(<SetupWizardPage agentId="agent-1" />);
    const user = userEvent.setup();
    for (const stage of ["Purpose", "Scope", "Runtime", "Test", "Publish"]) {
      await user.click(screen.getByRole("button", { name: new RegExp(`^${stage}`) }));
      expect(screen.getByRole("button", { name: new RegExp(`^${stage} \\(current`) }).getAttribute("aria-current")).toBe("step");
      expect(audit(container), stage).toEqual([]);
    }
  });

  it("setup wizard: create flow with no agent chosen, and a refused field", async () => {
    wizardState(null);
    const { container } = render(<SetupWizardPage />);
    expect(audit(container)).toEqual([]);
    await userEvent.setup().click(screen.getByRole("button", { name: "Validate and continue" }));
    await waitFor(() => expect(screen.getByText("Check these fields")).toBeTruthy());
    expect(audit(container)).toEqual([]);
  });

  it("home, run detail and review page", () => {
    state.byKey = {
      "admin/agents/readiness": query({ items: [setup] }),
      "runs/agent-1": query({ items: [{ run_id: "abcdef123456", state: "COMPLETED", created_at: "2026-11-14T10:00:00Z" }] }),
      "readiness/proposals": query({ items: [proposal] }),
    };
    const home = render(<ReadinessHomePage />);
    expect(screen.getByRole("link", { name: "Run abcdef12" })).toBeTruthy();
    expect(audit(home.container), "home").toEqual([]);
    cleanup();

    state.byKey = {
      "run/agent-1/run-1": query(runDetail),
      "decisions": query({ items: [] }),
      "evidence": query(evidence),
      "setup/agent-1": query(setup),
    };
    const run = render(<RunDetailPage agentId="agent-1" runId="run-1" />);
    expect(screen.getAllByRole("tab")).toHaveLength(4);
    expect(audit(run.container), "run detail").toEqual([]);
    cleanup();

    state.byKey = { "proposal/p1": query(proposal) };
    const review = render(<ReviewProposalPage proposalId="p1" />);
    expect(screen.getByRole("button", { name: "Dismiss finding" })).toBeTruthy();
    expect(audit(review.container), "review").toEqual([]);
  });
});

describe("AT-22 keyboard and focus", () => {
  it("the review page offers the assignee field up front, so assigning is possible by keyboard", async () => {
    state.byKey = { "proposal/p1": query(proposal) };
    render(<ReviewProposalPage proposalId="p1" />);
    const field = screen.getByLabelText("Assign to");
    await userEvent.setup().click(field);
    expect(document.activeElement).toBe(field);
    expect((screen.getByRole("button", { name: "Assign for investigation" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("the wizard advances from the keyboard: Enter in the name field saves and moves to Scope", async () => {
    wizardState(null);
    state.saved.mockReturnValue({ ...setup, validated: false, status: "DRAFT" });
    render(<SetupWizardPage agentId="agent-1" />);
    const user = userEvent.setup();
    const name = screen.getByLabelText(/^Display name/);
    await user.type(name, "x{Enter}");
    await waitFor(() => expect(screen.getByRole("button", { name: /^Scope \(current/ })).toBeTruthy());
    expect(screen.getByLabelText(/^Records per run/)).toBeTruthy();
  });

  it("the stepper is in a labeled navigation landmark and a locked stage cannot take focus by click", () => {
    wizardState(null);
    render(<SetupWizardPage agentId="agent-1" />);
    const nav = screen.getByRole("navigation", { name: "Setup stages" });
    expect(nav).toBeTruthy();
    expect((screen.getByRole("button", { name: /^Publish/ }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("the pause dialog traps focus inside and Escape closes it", async () => {
    state.byKey = { "run/agent-1/run-1": query(runDetail), "decisions": query({ items: [] }), "evidence": query(evidence), "setup/agent-1": query(setup) };
    render(<RunDetailPage agentId="agent-1" runId="run-1" />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Pause future runs" }));
    const dialog = screen.getByRole("alertdialog");
    expect(dialog.contains(document.activeElement)).toBe(true);
    await user.tab();
    expect(dialog.contains(document.activeElement)).toBe(true);
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
  });
});
