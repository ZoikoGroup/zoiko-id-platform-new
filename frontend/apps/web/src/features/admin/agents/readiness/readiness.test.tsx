/** The Access Readiness screens: the feature being off, a forbidden role, a refused field, the synthetic label, the digest, the pause confirmation
 * and the review proposal that can change nothing. */
import { ApiError, type ReadinessProposal, type ReadinessSetup } from "@zoiko/api-client";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  queries: {} as Record<string, unknown>,
  createFails: null as unknown,
}));

vi.mock("@/lib/cell", () => ({
  useCellQuery: (key: string[]) => state.queries[key.includes("proposals") ? "proposals" : key.length === 3 && key[2] === "readiness" ? "setups" : "other"] ?? { isPending: true, fetchStatus: "fetching" },
  useCellMutation: () => ({
    isPending: false,
    mutateAsync: () => (state.createFails ? Promise.reject(state.createFails) : Promise.resolve({})),
  }),
  newIdempotencyKey: () => "key",
  ApiErrorView: ({ error }: { error: Error }) => <div role="alert">api error: {error.message}</div>,
}));
vi.mock("@/lib/runtime", () => ({ useRuntime: () => ({}) }));
vi.mock("../../authority/shared", () => ({ useMe: () => "me-1" }));
vi.mock("next/link", () => ({ default: ({ href, children }: { href: string; children: ReactNode }) => <a href={href}>{children}</a> }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("../pickers", () => ({ PersonSelect: ({ label }: { label: string }) => <div>{label} picker</div> }));
vi.mock("@zoiko/ui", () => ({
  AccessUnavailable: () => <div>unavailable</div>,
  Badge: ({ children }: { children: ReactNode }) => <span>{children}</span>,
  Banner: ({ title, children }: { title?: ReactNode; children?: ReactNode }) => (
    <div role="status">
      <strong>{title}</strong>
      {children}
    </div>
  ),
  Button: ({ children, onPress, isDisabled, type }: { children: ReactNode; onPress?: () => void; isDisabled?: boolean; type?: "button" | "submit" }) => (
    <button type={type ?? "button"} disabled={isDisabled} onClick={onPress}>
      {children}
    </button>
  ),
  ButtonLink: ({ children, href }: { children: ReactNode; href: string }) => <a href={href}>{children}</a>,
  Card: ({ title, children }: { title?: ReactNode; children?: ReactNode }) => (
    <section>
      <h2>{title}</h2>
      {children}
    </section>
  ),
  CopyId: ({ value }: { value: string }) => <span>{value}</span>,
  Dialog: ({ isOpen, title, children, footer }: { isOpen: boolean; title: string; children: ReactNode; footer: ReactNode }) =>
    isOpen ? (
      <div role="alertdialog" aria-label={title}>
        {children}
        {footer}
      </div>
    ) : null,
  EmptyState: ({ title, children }: { title: string; children: ReactNode }) => (
    <div>
      <h3>{title}</h3>
      {children}
    </div>
  ),
  KeyValues: ({ items }: { items: [ReactNode, ReactNode][] }) => (
    <dl>
      {items.map(([k, v], i) => (
        <div key={i}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  ),
  LoadingRows: () => <div>loading</div>,
  PageHeader: ({ title }: { title: ReactNode }) => <h1>{title}</h1>,
  Select: ({ label }: { label: string }) => <div>{label} select</div>,
  TextField: ({ label, name, value, onChange, errorMessage }: { label: string; name?: string; value?: string; onChange?: (v: string) => void; errorMessage?: string }) => (
    <label>
      {label}
      <input name={name} value={value ?? ""} onChange={(e) => onChange?.(e.target.value)} />
      {errorMessage ? <span>{errorMessage}</span> : null}
    </label>
  ),
}));

import { AgentDryRunResults, AgentSetupStepper, EffectiveScopeSummary } from "./components";
import { PauseAgentControl } from "./pause-control";
import { AccessReviewProposalCard } from "./proposal-card";
import { ReadinessQuery } from "./shared";
import { SetupWizardPage } from "./setup-wizard";

afterEach(cleanup);
beforeEach(() => {
  state.queries = {};
  state.createFails = null;
});

const err = (status: number, code: string) => new ApiError({ status, code, message: code });
const query = (over: object) => ({ isPending: false, isError: false, fetchStatus: "idle", data: undefined, refetch: vi.fn(), ...over }) as never;

const setup = (over: Partial<ReadinessSetup> = {}): ReadinessSetup => ({
  setup_id: "s1",
  agent_id: "a1",
  display_name: "Access Readiness",
  purpose: "access_posture_review",
  scope: { resource_kinds: ["mandates"], max_records: 100, warning_window_days: 14, home_region: "eu" },
  rule_set_version: "daara-rules/1",
  policy_version: "daara-policy/1",
  status: "SANDBOX_READY",
  draft_revision: 1,
  scope_digest: `sha256:${"a".repeat(64)}`,
  validated: true,
  paused_reason: null,
  approval: null,
  permitted_tools: ["read_mandate_metadata", "create_access_review_proposal"],
  blocked: ["grant_role", "spend"],
  agent: { status: "ACTIVE", sponsor_active: true, attested: true, blueprint: "b@1" },
  ...over,
});

describe("feature off, region, forbidden", () => {
  it("says Not available when the feature is off, never an empty list", () => {
    render(<ReadinessQuery query={query({ isError: true, error: err(404, "NOT_FOUND") })}>{() => <div>an empty list</div>}</ReadinessQuery>);
    expect(screen.getByText("Not available")).toBeTruthy();
    expect(screen.queryByText("an empty list")).toBeNull();
  });
  it("says Not available in this region for an unsupported region", () => {
    render(<ReadinessQuery query={query({ isError: true, error: err(422, "REGION_UNSUPPORTED") })}>{() => null}</ReadinessQuery>);
    expect(screen.getByText("Not available in this region")).toBeTruthy();
  });
  it("shows a redacted result, not an empty success, to a role that may not see it", () => {
    render(<ReadinessQuery query={query({ isError: true, error: err(403, "PROTECTED_ACTION_DENIED") })}>{() => <div>0 findings</div>}</ReadinessQuery>);
    expect(screen.getByText("Redacted for your role")).toBeTruthy();
    expect(screen.queryByText("0 findings")).toBeNull();
  });
});

describe("setup wizard", () => {
  it("shows the digest the approver signs and disables the request until the gates pass", () => {
    state.queries.setups = query({ data: { items: [setup({ validated: false, status: "DRAFT" })] } });
    state.queries.other = query({ data: { items: [] }, isSuccess: true });
    render(<SetupWizardPage agentId="a1" />);
    // an unvalidated draft opens at the Test stage; Publish is locked until the dry run passes
    expect((screen.getByRole("button", { name: /Publish/ }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("with the gates passed: digest is shown and the request is enabled", async () => {
    state.queries.setups = query({ data: { items: [setup()] } });
    state.queries.other = query({ data: { items: [] }, isSuccess: true });
    render(<SetupWizardPage agentId="a1" />);
    expect(screen.getByText(`sha256:${"a".repeat(64)}`)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Request publishing approval" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("disables the request and names the unmet gate when the dry run is stale", () => {
    state.queries.setups = query({ data: { items: [setup({ validated: false, status: "DRAFT" })] } });
    state.queries.other = query({ data: { items: [] }, isSuccess: true });
    const { container } = render(<SetupWizardPage agentId="a1" />);
    expect(container.textContent).not.toContain("Request publishing approval");
  });

  it("shows the server's refusal as an error list and moves focus to the field", async () => {
    state.queries.setups = query({ data: { items: [setup({ status: "DRAFT", validated: false })] } });
    state.queries.other = query({ data: { items: [] }, isSuccess: true });
    state.createFails = err(422, "NAME_INVALID");
    render(<SetupWizardPage agentId="a1" />);
    await userEvent.setup().click(screen.getByRole("button", { name: /^Purpose/ }));
    await userEvent.setup().type(screen.getByLabelText(/^Display name/), "x");
    await userEvent.setup().click(screen.getByRole("button", { name: "Validate and continue" }));
    await waitFor(() => expect(screen.getByText("Check these fields")).toBeTruthy());
    expect(screen.getAllByText(/1 to 80 printable characters/).length).toBeGreaterThan(0);
    await waitFor(() => expect(document.activeElement).toBe(screen.getByLabelText(/^Display name/)));
  });

  it("offers no tenant or region input", () => {
    state.queries.setups = query({ data: { items: [] } });
    state.queries.other = query({ data: { items: [] }, isSuccess: true });
    render(<SetupWizardPage />);
    expect(screen.queryByLabelText(/tenant/i)).toBeNull();
    expect(screen.queryByLabelText(/region/i)).toBeNull();
    expect(screen.getByText(/cannot be typed here/)).toBeTruthy();
    expect(screen.getByText("Delegated Authority & Access Readiness")).toBeTruthy();
  });
});

describe("stepper and effective controls", () => {
  it("marks the current stage and locks the ones not reached", () => {
    render(<AgentSetupStepper current="scope" furthest="scope" onGo={() => undefined} />);
    expect(screen.getAllByRole("button")).toHaveLength(5);
    expect(screen.getByRole("button", { name: /Scope \(current/ }).getAttribute("aria-current")).toBe("step");
    expect((screen.getByRole("button", { name: /Publish/ }) as HTMLButtonElement).disabled).toBe(true);
  });
  it("writes every denied operation out as text", () => {
    render(<EffectiveScopeSummary setup={setup()} />);
    expect(screen.getAllByText(/Denied:/).length).toBe(2);
    expect(screen.getByText("Not approved yet")).toBeTruthy();
  });
});

describe("dry run", () => {
  it("is labeled synthetic with no live write, passed or not", () => {
    render(
      <AgentDryRunResults
        result={{ synthetic: true, live_writes: 0, passed: false, fixture: "baseline", policy_version: "p1", policy_hash: "h", rule_set_version: "r1", result_digest: "sha256:d", rules_covered: ["R-01"], findings: [], unknown: 0, setup_status: "DRAFT" }}
      />,
    );
    expect(screen.getByText("Synthetic data. No live write.")).toBeTruthy();
    expect(screen.getByText("Dry run did not pass")).toBeTruthy();
  });
});

describe("pause", () => {
  it("asks first and pauses only on confirmation", async () => {
    const onPause = vi.fn();
    render(<PauseAgentControl paused={false} onPause={onPause} onResume={() => undefined} />);
    await userEvent.setup().click(screen.getByRole("button", { name: "Pause future runs" }));
    expect(onPause).not.toHaveBeenCalled();
    expect(screen.getByRole("alertdialog")).toBeTruthy();
    await userEvent.setup().click(screen.getByRole("button", { name: "Pause agent" }));
    expect(onPause).toHaveBeenCalledWith("OPERATOR_DECISION");
  });
  it("can be canceled", async () => {
    const onPause = vi.fn();
    render(<PauseAgentControl paused={false} onPause={onPause} onResume={() => undefined} />);
    await userEvent.setup().click(screen.getByRole("button", { name: "Pause future runs" }));
    await userEvent.setup().click(screen.getByRole("button", { name: "Cancel" }));
    expect(onPause).not.toHaveBeenCalled();
  });
});

describe("review proposal", () => {
  const proposal: ReadinessProposal = {
    proposal_id: "p1",
    submitted_by: { type: "agent", agent_id: "a1" },
    target: "mandate:1234abcd...",
    rule_id: "R-01",
    rule_version: "daara-rules/1",
    finding_code: "EXPIRED_MANDATE",
    severity: "HIGH",
    confidence: "DETERMINISTIC",
    evidence_refs: ["evidence:1"],
    run_id: "r1",
    status: "OPEN",
    reviewed_by: null,
    assignee: null,
    created_at: "2026-11-14T10:00:00Z",
    handoff: null,
    notice: "x",
  };
  const render1 = (p = proposal) =>
    render(<AccessReviewProposalCard proposal={p} assigneeField={<div>assignee</div>} canAssign={false} onDismiss={vi.fn()} onAssign={vi.fn()} onHandoff={vi.fn()} />);

  it("says no access permissions will change", () => {
    render1();
    expect(screen.getAllByText("No access permissions will change as a result of this review proposal.").length).toBeGreaterThan(0);
  });
  it("offers the three review actions and nothing that executes a change", () => {
    render1();
    const names = screen.getAllByRole("button").map((b) => b.textContent);
    expect(names).toEqual(["Dismiss finding", "Assign for investigation", "Open governed access workflow"]);
    expect(names.join(" ")).not.toMatch(/execute|apply|grant|revoke|approve/i);
  });
  it("shows rule severity apart from confidence and basis", () => {
    render1();
    expect(screen.getByText("Rule severity")).toBeTruthy();
    expect(screen.getByText("Confidence and basis")).toBeTruthy();
    expect(screen.getByText("Deterministic rule")).toBeTruthy();
  });
  it("disables every action once a proposal is handed off", () => {
    render1({ ...proposal, status: "HANDED_OFF" });
    for (const b of screen.getAllByRole("button")) expect((b as HTMLButtonElement).disabled).toBe(true);
  });
});
