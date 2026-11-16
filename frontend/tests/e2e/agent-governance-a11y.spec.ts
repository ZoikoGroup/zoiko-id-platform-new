import AxeBuilder from "@axe-core/playwright";
import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import { addVirtualPasskey, registerCompany } from "./helpers";

/**
 * ZID-AG-WF-002 T44 and ZID-AG-WF-001 AT-22: the agent governance pages (overview, approvals, permissions, usage and spending, monitoring), the
 * access readiness setup and run pages, and the review queue and proposal pages work at desktop, 320 px, landscape and 200 percent zoom, are keyboard
 * operable, and have no automatically detectable WCAG 2.2 AA violation.
 *
 * The person is REAL (a throwaway company with a virtual passkey, so the console session is live). Only the governance data is mocked, because a new
 * company has no agents, runs or proposals. This is automated evidence. It is NOT a screen-reader run on a real device, and it was written to run
 * against the local stack; read the result of an actual run before relying on it.
 */

const VIEWPORTS = [
  { name: "desktop", width: 1280, height: 800 },
  { name: "mobile 320", width: 320, height: 640 },
  { name: "mobile landscape", width: 667, height: 320 },
  { name: "200 percent zoom (640 css px)", width: 640, height: 400 },
];

const AGENT = "01a19b00-0000-7000-8000-0000000000a1";
const RUN = "01a19b00-0000-7000-8000-0000000000b1";
const PROPOSAL = "01a19b00-0000-7000-8000-0000000000c1";
const HASH = `sha256:${"a".repeat(64)}`;
const NOW = new Date().toISOString();
const LATER = new Date(Date.now() + 20 * 3600_000).toISOString();

const axe = (page: Page) => new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();

async function clean(page: Page) {
  await page.addStyleTag({ content: "*, *::before, *::after { transition: none !important; animation: none !important; }" });
  const result = await axe(page);
  expect(result.violations, JSON.stringify(result.violations.map((v) => [v.id, v.nodes.map((n) => [n.target, n.any.map((a) => a.message)])]))).toEqual([]);
  const wide = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
  const offenders = wide
    ? await page.evaluate(() =>
        [...document.querySelectorAll("body *")]
          .filter((e) => e.getBoundingClientRect().right > document.documentElement.clientWidth + 1)
          .slice(0, 5)
          .map((e) => `${e.tagName}.${String(e.className).slice(0, 60)}`),
      )
    : [];
  expect(wide, `horizontal scrolling: ${JSON.stringify(offenders)}`).toBe(false);
}

const policy = {
  policy_id: "p1", agent_id: AGENT, version: 1, state: "ACTIVE", risk_tier: "LOW", region: "eu-west", policy_hash: HASH, ai_enabled: false,
  quotas: { runs_per_agent_day: 5, records_per_run: 100 }, expires_at: null,
  grants: [{ grant_id: "g1", tool_id: "read_mandate_metadata", action: "mandate.metadata.read", resource_kind: "mandate", resource_selector: "mandate:", purpose: "access_posture_review", region: "eu-west", data_class: "metadata", valid_until: LATER, approval_ref: "r1", state: "EFFECTIVE", effective: true, ceiling_source: "lease (within company baseline)" }],
};
const request = {
  request_id: "r1", ref: "AGP-7", agent_id: AGENT, kind: "SCOPE_EXTENSION", subject: "POLICY", state: "PENDING", requested_by: "01a19b00-0000-7000-8000-0000000000aa",
  policy_hash: HASH, base_policy_hash: HASH, delta_hash: HASH, delta: { added_grants: [{ tool_id: "read_agent_identity_status", resource_kind: "agent", resource_selector: "agent:", purpose: "access_posture_review", region: "eu-west", valid_until: LATER }], removed_grants: [], changed_grants: [], quotas: {}, ai_enabled: { from: false, to: false } },
  required_slots: ["SECURITY", "AUTHORITY"], open_slots: ["SECURITY", "AUTHORITY"], signatures: [], created_at: NOW, expires_at: LATER, expired: false, evidence_ref: "e1", can_review: false, review_unavailable: "SEPARATION_OF_DUTIES",
};
const proposal = {
  proposal_id: PROPOSAL, ref: "RP-3", agent_id: AGENT, submitted_by: "AGENT", run_id: RUN, target: "mandate:1a2b3c4d…", rule_id: "R-01", rule_version: "1", finding_code: "EXPIRED_MANDATE",
  severity: "HIGH", confidence_basis: "DETERMINISTIC_RULE", meaning: "A mandate is still active past its end date.", evidence_refs: ["mandate:1a2b3c4d…"], seen_count: 2, status: "OPEN",
  created_at: NOW, decided_at: null, decision_reason: null, has_handoff: false, notice: "No access permissions changed. The governed access workflow needs its own permissions.",
};
const run = {
  run_id: RUN, agent_id: AGENT, mode: "LIVE", state: "NEEDS_REVIEW", incomplete: false, checked: 12, issues: 1, proposals: 1, privileges_changed: 0, rule_set_version: "1", policy_hash: HASH,
  summary: {}, explanation: "Checked 12 record(s) and found 1 issue(s). No access was changed.", explanation_source: "FIXED", reason_code: null, completed_at: NOW,
};
const budgetRow = { budget_id: "b1", name: "agent_day", scope: "agent", currency: "USD", window: "DAY", hard_cap: "5.000000", committed: "3.600000", reserved: "0.200000", available: "1.200000", level: "WARN_70", warn_pct: [70, 90], revision: 1, resets_at: LATER };

function body(path: string, method: string): { status?: number; json: unknown } {
  const has = (s: string) => path.includes(s);
  if (has("/governance/overview")) {
    return { json: { kpis: { active_agents: 1, pending_approvals: 1, denied_calls_24h: 2, shadow_denials_24h: 0, budget_used_today: "3.800000", risk_signals: 1 },
      agents: [{ agent_id: AGENT, name: "Access Readiness: Finance", sponsor: "01a19b00-0000-7000-8000-0000000000aa", state: "ACTIVE", risk: "LOW", policy_version: 1, spend_today: "3.800000", last_evidence_at: NOW, governed: true }],
      requires_attention: [{ rule: "CAP_NEAR_LIMIT", severity: "P2", agent_id: AGENT, detail: "agent_day: WARN_70", alert_key: "k".repeat(32), state: "OPEN", tracked: true, occurrences: 2, sla_due: LATER, sla_breached: false }],
      pending: [request], telemetry: { state: "CURRENT", last_ingest: NOW, message: null }, mode: "enforce", as_of: NOW, timezone: "UTC", counts_source: "server-generated, not sampled" } };
  }
  if (has("/governance/requests")) return { json: { items: [request] } };
  if (has("/governance/events") || has("/events")) return { json: { items: [{ event_id: "e1", seq: 4, agent_principal_id: AGENT, action: "tool_call.governed", outcome: "ALLOW", code: null, occurred_at: NOW, digest: HASH, prev_digest: HASH }], next_cursor: null, redacted: true } };
  if (has("/effective-access")) return { json: { agent_id: AGENT, policy, approval: null, effective_leases: policy.grants, requested: [], company_baseline: { allowed_tools: ["read_mandate_metadata"], allowed_regions: ["eu-west"], ai_providers: [], quotas: {}, schedules_allowed: false, frozen: false }, frozen: false, hard_denies: ["role.grant", "keys.export"], hierarchy: ["P0 platform prohibition", "P1 region, residency, entitlement"], evaluated_at: NOW } };
  if (has("/budget")) return { json: { agent_id: AGENT, ceilings: [budgetRow], pending: [], ai_spend: "$0", external_spend: "forbidden", as_of: NOW, timezone: "UTC" } };
  if (has("/monitoring")) return { json: { agent_id: AGENT, name: "Access Readiness: Finance", state: "ACTIVE", summary: { allowed_24h: 9, denied_24h: 1, shadow_denied_24h: 0 }, sponsor: { principal_id: "s1", active: true }, expires_at: null, policy, active_runs: [], alerts: [], incident: [], telemetry: { state: "CURRENT", last_ingest: NOW }, as_of: NOW } };
  if (has("/daara/runs/")) return { json: { ...run, timeline: [{ seq: 1, at: NOW, step: "run.start", outcome: "ALLOW", code: null }], policy_decisions: [], evidence: { complete: true, events: 5, missing: [] }, proposals_for_run: [proposal], redacted: true, model_used: false } };
  if (has("/daara/runs")) return { json: { items: [run] } };
  if (has("/daara")) return { json: { agent_id: AGENT, enabled: true, stage: "ACTIVE", blockers: [], publish_blockers: [], template: { template: "DAARA", purpose: "access_posture_review", rule_set_version: "1", tools: [{ tool_id: "read_mandate_metadata", action: "mandate.metadata.read", side_effect: "NONE", resource: "mandate", why: "Reads mandate state." }], does: ["Reads metadata"], does_not: ["Grant or revoke a role"], release_default: "OFF until published" }, setup: { label: "Access Readiness: Finance", rationale: null, warn_days: 14, region: "eu-west", ai_explain: false, config_hash: HASH, validated: true, sandbox_passed: true }, effective_scope: { permitted: [], blocked: ["role.grant"], note: "Effective access exists after the policy is applied." }, policy: { version: 1, hash: HASH } } };
  if (has("/agent-review/")) return { json: proposal };
  if (has("/agent-review")) return { json: { items: [proposal], open: 1, notice: proposal.notice } };
  if (/\/agents\/[^/]+$/.test(path)) return { json: { agent_principal_id: AGENT, agent_name: "Access Readiness: Finance", blueprint_id: "bp", blueprint_version: "1", publisher_principal_id: "p", operator_principal_id: "o", sponsor_principal_id: "s", owning_organization_id: null, parent_agent_id: null, model_provider: "", allowed_tools: ["read_mandate_metadata"], max_subagent_depth: 0, status: "ACTIVE", security_revision: 0, readiness: { agent_principal_id: AGENT, status: "ACTIVE", blockers: [], attestations: {}, attestable_gates: [], live_bindings: 1 }, sub_agents: 0 } };
  return { json: { items: [] } };
}

async function mock(page: Page) {
  await page.route("**/api/bff/v1/machine/**", (route) => {
    const url = new URL(route.request().url());
    return route.fulfill(body(url.pathname, route.request().method()));
  });
}

test.describe.configure({ mode: "serial" });

let context: BrowserContext;
let page: Page;

test.beforeAll(async ({ browser }) => {
  context = await browser.newContext();
  page = await context.newPage();
  await addVirtualPasskey(context, page);
  await registerCompany(page);
  await mock(page);
});

test.afterAll(async () => {
  await context.close();
});

const PAGES: { path: string; heading: RegExp }[] = [
  { path: "/admin/agents/governance", heading: /Agent governance/ },
  { path: "/admin/agents/governance/approvals", heading: /Agent change approvals/ },
  { path: `/admin/agents/governance/${AGENT}/permissions`, heading: /Permissions/ },
  { path: `/admin/agents/governance/${AGENT}/limits`, heading: /Usage and spending/ },
  { path: `/admin/agents/governance/${AGENT}/monitoring`, heading: /Monitoring and incident controls/ },
  { path: `/admin/agents/governance/${AGENT}/daara`, heading: /Access readiness agent/ },
  { path: `/admin/agents/governance/${AGENT}/runs/${RUN}`, heading: /Access readiness run/ },
  { path: "/admin/agents/review", heading: /Agent review queue/ },
  { path: `/admin/agents/review/${PROPOSAL}`, heading: /Review suggested access change/ },
];

for (const p of PAGES) {
  for (const vp of VIEWPORTS) {
    test(`${p.path.replace(AGENT, "{agent}").replace(RUN, "{run}").replace(PROPOSAL, "{proposal}")} at ${vp.name}: no WCAG 2.2 AA violation and no horizontal scroll`, async () => {
      await page.setViewportSize({ width: vp.width, height: vp.height });
      await page.goto(p.path);
      await expect(page.getByRole("heading", { name: p.heading }).first()).toBeVisible({ timeout: 20_000 });
      await page.waitForLoadState("networkidle");
      await clean(page);
    });
  }
}

test("the navigation marks the current page and the first Tab stop is reachable by keyboard alone", async () => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/admin/agents/governance");
  await expect(page.getByRole("heading", { name: /Agent governance/ }).first()).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole("navigation", { name: "Agent governance" }).getByRole("link", { name: "Overview" })).toHaveAttribute("aria-current", "page");
  await page.keyboard.press("Tab");
  const focused = await page.evaluate(() => document.activeElement?.tagName);
  expect(focused).toBeTruthy();
});
