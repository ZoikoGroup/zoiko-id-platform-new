/** The public Trust Center views (ZID-CP-WF12 v2.0, WF-12.01 to 12.08; QA-59). Structural WCAG 2.2 AA checks with axe in a simulated browser, the honest-empty and unavailable states, and the report form's keyboard path. Colour contrast and a real screen reader are separate evidence. */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import { afterEach, describe, expect, it } from "vitest";

import { availabilityText, type AdvisoryFeed, type CertFeed, type ClaimsFeed, type PrivacyFeed, type StatusFeed } from "./model";
import { ReportForm } from "./report-form";
import { AdvisoriesView, CertificationsView, ClaimsView, PrivacyView, StatusView } from "./views";

afterEach(cleanup);

async function violations(container: HTMLElement) {
  const r = await axe.run(container, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] }, rules: { "color-contrast": { enabled: false } } });
  return r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
}

const claims: ClaimsFeed = {
  as_of: "2026-11-25T10:00:00+00:00", note: "Every statement here is approved.",
  items: [{ claim_id: "c1", text: "An independent SOC 2 report is available on request", locale: "en-US", scope: { entity_id: "e", service: "verify_api", markets: ["US"], environment: "production" }, effective_as_of: "2026-11-20T00:00:00+00:00", valid_until: "2027-01-31", channels: ["trust_center"], evidence: [{ scheme: "SOC 2", type: "report", issuer: "Auditor LLP", period_end: "2026-12-31", held_by: "zoiko", restricted: true }] }],
};
const certs: CertFeed = {
  as_of: "2026-11-25T10:00:00+00:00", note: "Each entry covers only what is shown.",
  current: [{ scheme: "SOC 2", kind: "Independent audit report", issuer: "Auditor LLP", assessor: null, issued_to: "zoiko", is_zoiko: true, legal_entity: "entity", products: ["verify_api"], regions: ["US"], exclusions: [], period_start: "2026-01-01", period_end: "2026-12-31", state: "ACTIVE", full_report: "Available on request under NDA." }],
  history: [{ scheme: "ISO 27001", kind: "Certificate", issuer: "Registrar", assessor: null, issued_to: "CloudCo", is_zoiko: false, legal_entity: "CloudCo Ltd", products: ["hosting"], regions: [], exclusions: [], period_start: "2024-01-01", period_end: "2025-12-31", state: "EXPIRED", full_report: null }],
};
const privacy: PrivacyFeed = { as_of: "2026-11-25T10:00:00+00:00", market: "GB", empty: { retention: "Not yet published for this market.", biometric: "Not yet published for this market.", transfer: "Not yet published for this market." }, subprocessor: [{ subject: "CloudCo", market: "GB", revision: 1, purpose: "Hosting", country: "GB", data_categories: ["DC2"], notice_days: 30, notice_source: "DPA-7 clause 9" }], retention: [], biometric: [], transfer: [] };
const status: StatusFeed = { as_of: "2026-11-25T10:00:00+00:00", definition: "Availability is good requests divided by eligible requests.", sla: "Service level targets are targets, not measured results.", services: [{ service: "verify_api", region: "uk-1", window: "2026-10", state: "MEASURED", percent: 99.9, note: "2026-10" }, { service: "hosted_flow", region: null, window: null, state: "METRIC_UNAVAILABLE", percent: null, note: "Not yet reported." }] };
const advisories: AdvisoryFeed = { items: [{ advisory_id: "a1", latest_title: "Delayed sign-in", versions: [{ seq: 1, kind: "PUBLISH", title: "Delayed sign-in", body: "Some sign-ins were delayed.", correction_reason: null, created_at: "2026-11-24T10:00:00+00:00" }, { seq: 2, kind: "CORRECTION", title: "Delayed sign-in", body: "Delayed between 10:05 and 10:40 UTC.", correction_reason: "The first start time was wrong.", created_at: "2026-11-24T12:00:00+00:00" }] }] };

describe("the public Trust Center views", () => {
  it("have no structural accessibility violations with data", async () => {
    for (const v of [<ClaimsView key="1" feed={claims} />, <CertificationsView key="2" feed={certs} />, <PrivacyView key="3" feed={privacy} />, <StatusView key="4" feed={status} />, <AdvisoriesView key="5" feed={advisories} />]) {
      const { container } = render(<main>{v}</main>);
      expect(await violations(container)).toEqual([]);
      cleanup();
    }
  });

  it("have none when empty or unavailable, and say so plainly rather than show nothing or something invented", async () => {
    const { container } = render(
      <main>
        <ClaimsView feed={{ ...claims, items: [] }} />
        <AdvisoriesView feed={{ items: [] }} />
        <StatusView feed={null} />
        <PrivacyView feed={{ ...privacy, subprocessor: [], empty: { ...privacy.empty, subprocessor: "Not yet published for this market." } }} />
      </main>,
    );
    expect(await violations(container)).toEqual([]);
    expect(screen.getByText(/No statement is currently approved/)).toBeTruthy();
    expect(screen.getByText(/no published advisories/i)).toBeTruthy();
    expect(screen.getByText(/could not be loaded right now/)).toBeTruthy();
    expect(screen.getAllByText("Not yet published for this market.").length).toBe(4);
  });

  it("never shows an unmeasured service as a percentage", () => {
    render(<StatusView feed={status} />);
    expect(screen.getByText("99.9% available")).toBeTruthy();
    expect(screen.getAllByText("Not yet reported").length).toBe(1);
    expect(availabilityText({ state: "METRIC_UNAVAILABLE", percent: 100 })).toBe("Not yet reported");
  });

  it("attributes a supplier's assessment to the supplier and keeps an expired one under earlier", () => {
    render(<CertificationsView feed={certs} />);
    expect(screen.getByText(/a supplier\. This is the supplier's, not Zoiko iD's own/)).toBeTruthy();
    expect(screen.getByRole("list", { name: "Earlier assessments" }).textContent).toContain("ISO 27001");
    expect(screen.getByRole("list", { name: "Current assessments" }).textContent).not.toContain("ISO 27001");
  });

  it("keep every advisory version and mark a correction with its reason", () => {
    render(<AdvisoriesView feed={advisories} />);
    expect(screen.getByText(/Some sign-ins were delayed/)).toBeTruthy();
    expect(screen.getByText(/Why corrected: The first start time was wrong/)).toBeTruthy();
  });
});

describe("the vulnerability report form", () => {
  it("has no structural violations, works by keyboard and shows a reference and a target, never the report", async () => {
    const sent: unknown[] = [];
    const send = async (b: unknown) => {
      sent.push(b);
      return { ok: true, status: 201, data: { reference: "VR-ABCDEFGHJK", first_response_target: "2026-11-26T10:00:00+00:00", note: "This is a target, not a guarantee." } };
    };
    const { container } = render(<main><ReportForm send={send} /></main>);
    expect(await violations(container)).toEqual([]);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/What did you find/), "The sign-in page reveals whether an address is registered.");
    await user.tab();
    await user.tab();
    await user.tab();
    await user.tab();
    expect(document.activeElement?.textContent).toBe("Send report");
    await user.keyboard("{Enter}");
    await waitFor(() => expect(screen.getByText("VR-ABCDEFGHJK")).toBeTruthy());
    expect(screen.queryByText(/reveals whether an address/)).toBeNull();
    expect(JSON.stringify(sent[0])).toContain('"contact":null');
  });

  it("tells the reporter plainly when it is rate limited or fails, and keeps the form", async () => {
    const limited = async () => ({ ok: false, status: 429 });
    render(<main><ReportForm send={limited} /></main>);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/What did you find/), "A reasonably long description of the weakness found.");
    await user.click(screen.getByRole("button", { name: "Send report" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/Too many reports/);
    expect(screen.getByRole("button", { name: "Send report" })).toBeTruthy();
  });
});
