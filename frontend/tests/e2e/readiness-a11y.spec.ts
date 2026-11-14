import AxeBuilder from "@axe-core/playwright";
import { expect, test, type BrowserContext, type Page, type Route } from "@playwright/test";

import { addVirtualPasskey, registerCompany } from "./helpers";

/**
 * ZID-AG-WF-001 AT-22 (CR-091): the access readiness home, the setup wizard (WF-AG-01/02), the run detail (WF-AG-03) and the review handoff
 * (WF-AG-04) work at desktop, 320 px, landscape and 200 percent zoom, are keyboard operable, and have no automatically detectable WCAG 2.2 AA
 * violation.
 *
 * The person is REAL: a throwaway company is registered through the real form with a virtual passkey, so the console session is live. The access
 * readiness data is mocked at the BFF, because the feature is off by default (ZID_ACCESS_READINESS_ENABLED) and a new company has no agent.
 * This is automated evidence, not a screen-reader run on a real device, and not a manual zoom pass.
 */

const VIEWPORTS = [
  { name: "desktop", width: 1280, height: 800 },
  { name: "mobile 320", width: 320, height: 640 },
  { name: "mobile landscape", width: 667, height: 320 },
  { name: "200 percent zoom (640 css px)", width: 640, height: 400 },
];

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

const NOW = new Date().toISOString();
const SOON = new Date(Date.now() + 5 * 86_400_000).toISOString();
const AGENT = "01a19b00-0000-7000-8000-0000000000a1";
const RUN = "01a19b00-0000-7000-8000-0000000000b1";
const PROPOSAL = "01a19b00-0000-7000-8000-0000000000c1";
const HASH = "sha256:" + "a".repeat(64);

const setup = {
  setup_id: "01a19b00-0000-7000-8000-0000000000d1",
  agent_id: AGENT,
  display_name: "Access Readiness - Finance",
  purpose: "access_posture_review",
  scope: { resource_kinds: ["mandates"], max_records: 100, warning_window_days: 14, home_region: "uk-1", ai_explanation: false },
  rule_set_version: "daara-rules/1",
  policy_version: "daara-policy/1",
  status: "APPROVED",
  draft_revision: 2,
  scope_digest: HASH,
  validated: true,
  paused_reason: null,
  approval: { approval_id: "01a19b00-0000-7000-8000-0000000000e1", scope_digest: HASH, requested_by: "p1", approved_by: "p2", expires_at: SOON },
  permitted_tools: ["create_access_review_proposal", "read_agent_identity_status", "read_mandate_metadata"],
  blocked: ["grant_role", "revoke_credential", "change_mandate", "edit_policy", "spend"],
  agent: { status: "ACTIVE", sponsor_active: true, attested: true, blueprint: "daara@1.0.0" },
};

const proposal = {
  proposal_id: PROPOSAL,
  submitted_by: { type: "agent", agent_id: AGENT },
  target: "mandate:1a2b3c4d...",
  rule_id: "R-01",
  rule_version: "daara-rules/1",
  finding_code: "EXPIRED_MANDATE",
  severity: "HIGH",
  confidence: "DETERMINISTIC",
  evidence_refs: ["mandate:1a2b3c4d", "evidence:01a19b00"],
  run_id: RUN,
  status: "OPEN",
  reviewed_by: null,
  assignee: null,
  created_at: NOW,
  handoff: null,
  notice: "No access permissions will change as a result of this review proposal.",
};

const run = {
  run_id: RUN,
  agent_id: AGENT,
  state: "NEEDS_REVIEW",
  attempt: 1,
  mode: "manual",
  purpose: "access_posture_review",
  resource_selector: { kind: "mandates" },
  denial_codes: [],
  summary: { records_read: 7, findings: 6, unknown: 0, complete: true, proposals_created: 6, proposals_existing: 0, privileges_changed: 0, result_digest: HASH },
  explanation: {
    source: "MODEL",
    text: "The run checked 7 records and flagged 6 findings, including R-01 EXPIRED_MANDATE. Each one is a proposal for a person; nothing was changed.",
    fallback_reason: null,
    provider: "fake",
    model: "approved-model",
    prompt_version: "daara-explain/1",
    facts_digest: HASH,
    authoritative: false,
  },
  rule_set_version: "daara-rules/1",
  policy_hash: HASH,
  run_definition_hash: HASH,
  region: "uk-1",
  trace_id: "trace-1",
  worker_attestation_id: "01a19b00-0000-7000-8000-0000000000f1",
  created_at: NOW,
  terminal_at: NOW,
  links: { self: `/v1/access-readiness/agents/${AGENT}/runs/${RUN}` },
};

const decisions = {
  items: [
    { decision_id: "d1", run_id: RUN, actor: AGENT, subject: "tenant:t", resource: `agent:${AGENT}`, action: "run.start", purpose: "access_posture_review", outcome: "ALLOW", reason_code: "PREFLIGHT_PASSED", policy_hash: HASH, decided_at: NOW, trace_id: "trace-1" },
    { decision_id: "d2", run_id: RUN, actor: AGENT, subject: "tenant:t", resource: "mandates:page", action: "read_mandate_metadata", purpose: "access_posture_review", outcome: "ALLOW", reason_code: "READ_PERMITTED", policy_hash: HASH, decided_at: NOW, trace_id: "trace-1" },
  ],
};

const envelope = (seq: number, event_type: string) => ({
  event_id: `01a19b00-0000-7000-8000-00000000010${seq}`,
  run_id: RUN,
  seq,
  event_type,
  source_id: "run",
  source_version: "daara-rules/1",
  payload_digest: HASH,
  chain_digest: HASH,
  classification: "METADATA",
  region: "uk-1",
  retention_policy_ref: "retention/access-readiness/1",
  recorded_at: NOW,
});
const evidence = {
  verified: true,
  integrity_exceptions: [],
  items: ["preflight_passed", "credential_issued", "metadata_read", "rules_evaluated", "proposal_created", "credential_revoked", "sponsor_notified", "run_needs_review"].map((t, i) => envelope(i + 1, t)),
};

const agents = {
  items: [
    { agent_principal_id: AGENT, agent_name: "Access readiness bot", blueprint_id: "daara", blueprint_version: "1.0.0", status: "ACTIVE", sponsor_principal_id: "p1", allowed_tools: setup.permitted_tools },
  ],
};

async function mockReadiness(page: Page) {
  await page.route("**/api/bff/v1/access-readiness/**", async (route: Route) => {
    const path = new URL(route.request().url()).pathname.replace(/^.*\/api\/bff/, "");
    if (route.request().method() === "POST" && path.endsWith("/evidence:export")) {
      return route.fulfill({ json: { schema: "zid.access-readiness.evidence.v1", run_id: RUN, redacted: false, chain: { verified: true, integrity_exceptions: [] }, evidence: [], decisions: [], proposals: [] } });
    }
    if (path === "/v1/access-readiness/agents") return route.fulfill({ json: { items: [setup] } });
    if (path === `/v1/access-readiness/agents/${AGENT}`) return route.fulfill({ json: setup });
    if (path === `/v1/access-readiness/agents/${AGENT}/runs`) return route.fulfill({ json: { items: [run] } });
    if (path === `/v1/access-readiness/agents/${AGENT}/runs/${RUN}`) return route.fulfill({ json: { ...run, proposals: [proposal] } });
    if (path.endsWith("/decisions")) return route.fulfill({ json: decisions });
    if (path.endsWith("/evidence")) return route.fulfill({ json: evidence });
    if (path === "/v1/access-readiness/review-proposals") return route.fulfill({ json: { items: [proposal], notice: proposal.notice } });
    if (path === `/v1/access-readiness/review-proposals/${PROPOSAL}`) return route.fulfill({ json: proposal });
    return route.fulfill({ status: 404, json: { error: { code: "NOT_FOUND", message: "This resource is not available." } } });
  });
  await page.route("**/api/bff/v1/machine/agents**", (route: Route) => route.fulfill({ json: agents }));
}

async function unmock(page: Page) {
  await page.unroute("**/api/bff/v1/access-readiness/**");
  await page.unroute("**/api/bff/v1/machine/agents**");
}

test.describe.configure({ mode: "serial" });

let context: BrowserContext;
let page: Page;

test.beforeAll(async ({ browser }) => {
  context = await browser.newContext();
  page = await context.newPage();
  await addVirtualPasskey(context, page);
  await registerCompany(page);
});

test.afterAll(async () => {
  await context.close();
});

async function open(path: string, heading: RegExp | string) {
  await mockReadiness(page);
  await page.goto(path);
  await expect(page.getByRole("heading", { name: heading, level: 1 })).toBeVisible({ timeout: 20_000 });
  await page.waitForLoadState("networkidle");
}

for (const vp of VIEWPORTS) {
  test.describe(`at ${vp.name}`, () => {
    test.beforeEach(async () => {
      await page.setViewportSize({ width: vp.width, height: vp.height });
    });
    test.afterEach(async () => {
      await unmock(page);
    });

    test("the access readiness home lists the agent and the review queue", async () => {
      await open("/admin/agents/readiness", "Access readiness agent");
      await expect(page.getByText("Access Readiness - Finance").first()).toBeVisible();
      await expect(page.getByText("No access permissions will change").first()).toBeVisible();
      await clean(page);
    });

    test("the setup wizard names its fields, shows the stages and the AI explanation opt-in", async () => {
      await open(`/admin/agents/readiness/${AGENT}/setup`, /access readiness/i);
      await clean(page);
      const runtime = page.getByRole("button", { name: /Runtime/i }).first();
      if (await runtime.isVisible()) {
        await runtime.click();
        await expect(page.getByRole("checkbox", { name: /AI explanation/i })).toBeVisible();
        await clean(page);
      }
    });

    test("the run detail shows the result first, the labelled AI explanation and keyboard tabs", async () => {
      await open(`/admin/agents/readiness/${AGENT}/runs/${RUN}`, "Access readiness run");
      await expect(page.getByText(/Checked 7 permitted records/)).toBeVisible();
      await expect(page.getByText("AI explanation", { exact: true })).toBeVisible();
      await clean(page);
      await page.getByRole("tab", { name: "Overview" }).focus();
      await page.keyboard.press("ArrowRight");
      await expect(page.getByRole("tab", { name: "Policy Decisions" })).toBeFocused();
      await clean(page);
      await page.getByRole("button", { name: "Export permitted evidence" }).click();
      const dialog = page.getByRole("dialog");
      await expect(dialog.getByRole("textbox", { name: /Purpose of the export/ })).toBeVisible();
      await clean(page);
      await page.keyboard.press("Escape");
      await expect(dialog).toHaveCount(0);
    });

    test("the review handoff shows the notice and accessible actions", async () => {
      await open(`/admin/agent-review/${PROPOSAL}`, "Review suggested access change");
      await expect(page.getByText(/No access permissions will change/).first()).toBeVisible();
      for (const name of ["Dismiss finding", "Assign for investigation", "Open governed access workflow"]) {
        const box = await page.getByRole("button", { name }).first().boundingBox();
        expect(box!.height, name).toBeGreaterThanOrEqual(43.5);
      }
      await clean(page);
    });
  });
}
