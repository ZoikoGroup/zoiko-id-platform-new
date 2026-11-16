/** The regulatory screens (ZID-RCG-003 WF-01..08), rendered for real against a mocked client. */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError } from "@zoiko/api-client";
import { ToastProvider } from "@zoiko/ui";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const HASH = "ab".repeat(32);

// jsdom has no CSS.escape, which react-aria's Select uses to focus the option it opens on.
if (!(globalThis as { CSS?: unknown }).CSS) Object.defineProperty(globalThis, "CSS", { value: { escape: (v: string) => v.replace(/[^\w-]/g, (c) => `\\${c}`) } });

const fake = vi.hoisted(() => ({
  regulatory: {
    readiness: vi.fn(),
    offerings: vi.fn(),
    releases: vi.fn(),
    marketStatus: vi.fn(),
    approveRelease: vi.fn(),
    suspensions: vi.fn(),
    suspensionImpact: vi.fn(),
    suspend: vi.fn(),
    claims: vi.fn(),
    evidenceBundle: vi.fn(),
    conformanceMatrix: vi.fn(),
    conformanceRuns: vi.fn(),
    conformanceRun: vi.fn(),
    reviewConformanceRun: vi.fn(),
    issuers: vi.fn(),
    issuerStatements: vi.fn(),
    recordIssuerStatement: vi.fn(),
  },
}));

vi.mock("@/lib/runtime", () => ({
  useRuntime: () => ({ api: fake, protect: <T,>(run: () => Promise<T>) => run(), session: { authenticated: true, subject: { sub: "op-1" }, csrf: "x" } }),
}));

import { ClaimsPage } from "./claims";
import { ConformancePage } from "./conformance";
import { HoldsPage } from "./holds";
import { IssuersPage } from "./issuers";
import { OfferingsPage } from "./offerings";
import { ReadinessPage } from "./readiness";
import { ReleasesPage } from "./releases";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function mount(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <main>{node}</main>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe("service offerings", () => {
  it("shows where each offering is available and, on request, every reason it is not", async () => {
    fake.regulatory.offerings.mockResolvedValue({
      items: [
        {
          offering_id: "GLOBAL_APIS_INTEGRATIONS", title: "Global APIs & integrations", wording: "Provide APIs.", capabilities: [], biometric: false,
          cases: ["T34"], code: ["modules/webhook"], countries: [{ country: "GB", status: "AVAILABLE", reasons: [] }, { country: "US", status: "AVAILABLE", reasons: [] }],
        },
        {
          offering_id: "IDENTITY_DOCUMENTS", title: "Identity & documents", wording: "Confirm identities.", capabilities: ["INDIVIDUAL_VERIFICATION"], biometric: false,
          cases: ["T01"], code: ["modules/verification"],
          countries: [{ country: "GB", status: "NOT_AVAILABLE", reasons: ["PRIVACY_OVERLAY_MISSING", "TEST_NOT_PASSED:T01"] }],
        },
      ],
    });
    mount(<OfferingsPage />);
    expect(await screen.findByText("GB, US")).toBeTruthy();
    expect(screen.getByText("Nowhere yet")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Why: Identity & documents" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("PRIVACY_OVERLAY_MISSING")).toBeTruthy();
    expect(within(dialog).getByText("TEST_NOT_PASSED:T01")).toBeTruthy();
  });
});

describe("enforce readiness", () => {
  it("shows per market what enforce would refuse, the switch modes and empty configuration", async () => {
    fake.regulatory.readiness.mockResolvedValue({
      countries: [
        {
          country: "GB",
          capabilities: [
            { capability: "INDIVIDUAL_VERIFICATION", availability: "SANDBOX", provider: "simulator", would_refuse: ["pack:NO_APPROVED_PACK", "release:NO_RELEASE"], missing: ["pack", "release"] },
            { capability: "BUSINESS_VERIFICATION", availability: "ENABLED", provider: "acme", would_refuse: [], missing: [] },
          ],
        },
      ],
      modes: { regulatory_packs: "shadow", market_releases: "enforce", kill_switch: "always enforced" },
      empty_configuration: ["claim templates", "privacy overlays"],
    });
    mount(<ReadinessPage />);
    expect(await screen.findByText("1 of 2 would be allowed under enforce")).toBeTruthy();
    expect(screen.getByText("pack:NO_APPROVED_PACK")).toBeTruthy();
    expect(screen.getByText("Refused")).toBeTruthy();
    expect(screen.getByText("Allowed")).toBeTruthy();
    expect(screen.getByText(/regulatory packs: shadow/)).toBeTruthy();
    expect(screen.getByText(/claim templates, privacy overlays/)).toBeTruthy();
  });
});

const release = {
  release_id: "gb-idv",
  version: 1,
  status: "FROZEN",
  market: { country: "GB", product: "INDIVIDUAL_VERIFICATION", purpose: "ENGAGEMENT" },
  author: "op-author",
  release_hash: HASH,
  pack_refs: [{ pack_id: "gb-pack", version: 1, pack_hash: HASH }],
  supplier_scopes: [],
  gates: [{ gate: "RG-01", title: "Legal basis", owner_role: "legal", status: "PASSED", evidence_ref: "LEG-1", recorded_by: "op-l", recorded_at: "2026-11-01T00:00:00Z" }],
  blockers: [],
  approvals: {},
  missing_approvals: ["legal", "security_grc", "qa", "product_owner"],
};

describe("market releases", () => {
  it("signs the exact release hash the approver was shown", async () => {
    fake.regulatory.releases.mockResolvedValue({ items: [release] });
    fake.regulatory.approveRelease.mockResolvedValue({ ...release, approvals: { legal: { approver: "op-1", release_hash: HASH } }, missing_approvals: ["security_grc", "qa", "product_owner"] });
    mount(<ReleasesPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Open gb-idv v1" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Passed")).toBeTruthy();
    await userEvent.click(within(dialog).getByRole("button", { name: "Sign this hash" }));
    await waitFor(() => expect(fake.regulatory.approveRelease).toHaveBeenCalledWith("gb-idv", 1, "legal", HASH));
  });

  it("explains an approver who recorded a gate is not independent", async () => {
    fake.regulatory.releases.mockResolvedValue({ items: [release] });
    fake.regulatory.approveRelease.mockRejectedValue(new ApiError({ status: 403, code: "RELEASE_APPROVER_NOT_INDEPENDENT", message: "no" }));
    mount(<ReleasesPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Open gb-idv v1" }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Sign this hash" }));
    expect(await screen.findByText(/so you cannot also approve it/)).toBeTruthy();
  });
});

describe("holds", () => {
  it("cannot apply a hold before its impact is previewed and acknowledged", async () => {
    fake.regulatory.suspensions.mockResolvedValue({ items: [] });
    fake.regulatory.suspensionImpact.mockResolvedValue({ scope: { provider: "acme" }, impact: { markets: ["gb-idv:v1"], entitlements: [], certifications: [], claims: ["uk-certified"] } });
    fake.regulatory.suspend.mockResolvedValue({});
    mount(<HoldsPage />);
    expect(await screen.findByText("Nothing is suspended.")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Apply hold" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText(/Hold ID/), "hold-acme");
    await userEvent.type(within(dialog).getByLabelText(/^Provider/), "acme");
    await userEvent.type(within(dialog).getByLabelText(/Evidence reference/), "INC-1");
    await userEvent.type(within(dialog).getByLabelText(/^Reason/), "certificate withdrawn on the register");
    const apply = within(dialog).getByRole("button", { name: "Apply hold" });
    expect(apply.hasAttribute("disabled") || apply.getAttribute("aria-disabled") === "true").toBe(true);
    await userEvent.click(within(dialog).getByRole("button", { name: "Preview impact" }));
    expect(await within(dialog).findByText("gb-idv:v1")).toBeTruthy();
    await userEvent.click(within(dialog).getByText("I have read what this hold stops"));
    await userEvent.click(apply);
    await waitFor(() =>
      expect(fake.regulatory.suspend).toHaveBeenCalledWith(
        expect.objectContaining({ suspension_id: "hold-acme", scope: expect.objectContaining({ provider: "acme" }), trigger: "CERTIFICATION_WITHDRAWN" }),
      ),
    );
  });
});

describe("regulated claims", () => {
  it("exports proof only with a legal basis, and shows the digest", async () => {
    fake.regulatory.claims.mockResolvedValue({
      items: [{ template_id: "uk-certified", version: 1, text: "Certified under UK DIATF", audiences: ["SALES"], countries: ["GB"], template_hash: HASH, publishable: false, reasons: ["EVIDENCE_EXPIRED"] }],
    });
    fake.regulatory.evidenceBundle.mockResolvedValue({ digest: HASH, signature: null, signature_status: "NO_SIGNING_KEY", content: { claim: "uk-certified" } });
    mount(<ClaimsPage />);
    expect(await screen.findByText("Must not be published")).toBeTruthy();
    expect(screen.getByText("EVIDENCE_EXPIRED")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Export proof for uk-certified" }));
    const dialog = await screen.findByRole("dialog");
    const exportButton = within(dialog).getByRole("button", { name: "Export" });
    expect(exportButton.hasAttribute("disabled") || exportButton.getAttribute("aria-disabled") === "true").toBe(true);
    await userEvent.type(within(dialog).getByLabelText(/Legal basis reference/), "REG-REQ-7");
    await userEvent.click(exportButton);
    await waitFor(() => expect(fake.regulatory.evidenceBundle).toHaveBeenCalledWith("claim:uk-certified", "REGULATOR_REQUEST", "REG-REQ-7"));
    expect(await within(dialog).findByText("NO_SIGNING_KEY")).toBeTruthy();
  });
});

describe("conformance", () => {
  it("shows the matrix coverage with what is missing", async () => {
    fake.regulatory.conformanceMatrix.mockResolvedValue({
      matrix_hash: HASH,
      counts: { COVERED: 1, PARTIAL: 0, GAP: 1 },
      as_of: "2026-11-12T00:00:00Z",
      cases: [
        { case: "T01", title: "Unreleased market", expected: "refused", coverage: "COVERED", tests: ["a.py::t"], note: "" },
        { case: "T45", title: "Regulator export", expected: "bundle", coverage: "GAP", tests: [], note: "no automated test" },
      ],
    });
    fake.regulatory.conformanceRuns.mockResolvedValue({ items: [] });
    mount(<ConformancePage />);
    expect(await screen.findByText("T45")).toBeTruthy();
    expect(screen.getByText("no automated test")).toBeTruthy();
    expect(screen.getByText("GAP: 1")).toBeTruthy();
  });
});

const SOURCE_HASH = "cd".repeat(32);

/** Paste rather than type: a long value typed key by key outlasts the timeout when the whole suite runs at once. */
async function fill(field: HTMLElement, text: string) {
  await userEvent.click(field);
  await userEvent.paste(text);
}

describe("issuer statements", () => {
  it("records a first statement with exactly the body the backend model takes, then refreshes the issuers", async () => {
    fake.regulatory.issuers.mockResolvedValue({ items: [] });
    fake.regulatory.issuerStatements.mockResolvedValue({ items: [] });
    fake.regulatory.recordIssuerStatement.mockResolvedValue({ issuer_id: "uni-of-example", seq: 1, statement: "UPDATE", retroactive_changes: [] });
    mount(<IssuersPage />);
    expect(await screen.findByText("No issuer history")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Record issuer statement" }));
    const dialog = await screen.findByRole("dialog");
    const record = within(dialog).getByRole("button", { name: "Record statement" });
    expect(record.hasAttribute("disabled") || record.getAttribute("aria-disabled") === "true").toBe(true);
    await fill(within(dialog).getByLabelText(/^Issuer ID/), "uni-of-example");
    await fill(within(dialog).getByLabelText(/^Claim types/), "degree");
    await userEvent.click(within(dialog).getByRole("button", { name: /Countries/ }));
    await userEvent.click(await screen.findByRole("option", { name: "GB - United Kingdom" }));
    await fill(within(dialog).getByLabelText(/^Name 1(?! used)/), "University of Example");
    await fill(within(dialog).getByLabelText(/^Source URL/), "https://register.example.gov/institutions/1234");
    await userEvent.click(within(dialog).getByLabelText(/^Source hash/));
    await userEvent.paste(SOURCE_HASH.toUpperCase());
    await fill(within(dialog).getByLabelText(/^Evidence locator/), "s3://evidence/issuers/uni-of-example.html");
    await userEvent.click(record);
    await waitFor(() =>
      expect(fake.regulatory.recordIssuerStatement).toHaveBeenCalledWith({
        issuer_id: "uni-of-example",
        issuer_kind: "UNIVERSITY",
        authority: "OFFICIAL",
        countries: ["GB"],
        claim_types: ["DEGREE"],
        names: [{ name: "University of Example", from: null, until: null }],
        accreditations: [],
        revoked_on: null,
        successor_of: null,
        statement: "UPDATE",
        reason: null,
        source_url: "https://register.example.gov/institutions/1234",
        source_hash: SOURCE_HASH,
        evidence_locator: "s3://evidence/issuers/uni-of-example.html",
      }),
    );
    await waitFor(() => expect(fake.regulatory.issuers).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("starts from the latest statement and explains a retroactive change the server refuses", async () => {
    fake.regulatory.issuers.mockResolvedValue({ items: ["uni-of-example"] });
    fake.regulatory.issuerStatements.mockResolvedValue({
      items: [
        {
          issuer_id: "uni-of-example", seq: 1, statement_id: "iss-uni-of-example-1", statement: "UPDATE", reason: null,
          record: {
            issuer_id: "uni-of-example", kind: "UNIVERSITY", authority: "OFFICIAL", countries: ["GB"], claim_types: ["DEGREE"],
            names: [{ name: "University of Example", from: "1990-01-01" }],
            accreditations: [{ body: "example-accreditor", program: "BSc Nursing", from: "2010-09-01" }],
          },
          source_url: "https://register.example.gov/institutions/1234", source_hash: HASH, evidence_locator: "s3://e/1", recorded_by: "op-2", recorded_at: "2026-11-01T00:00:00Z",
        },
      ],
    });
    fake.regulatory.recordIssuerStatement.mockRejectedValue(
      new ApiError({ status: 422, code: "ISSUER_STATEMENT_RETROACTIVE", message: "accreditation_removed:example-accreditor/BSc Nursing: record it as a CORRECTION with a reason" }),
    );
    mount(<IssuersPage />);
    await userEvent.click(await screen.findByRole("button", { name: /Issuer/ }));
    await userEvent.click(await screen.findByRole("option", { name: "uni-of-example" }));
    expect(await screen.findByText("History of uni-of-example")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Record issuer statement" }));
    const dialog = await screen.findByRole("dialog");
    expect((within(dialog).getByLabelText(/^Name 1(?! used)/) as HTMLInputElement).value).toBe("University of Example");
    await userEvent.click(within(dialog).getByRole("button", { name: "Remove accreditation 1" }));
    await fill(within(dialog).getByLabelText(/^Source URL/), "https://register.example.gov/institutions/1234");
    await userEvent.click(within(dialog).getByLabelText(/^Source hash/));
    await userEvent.paste(SOURCE_HASH);
    await fill(within(dialog).getByLabelText(/^Evidence locator/), "s3://evidence/issuers/2.html");
    await userEvent.click(within(dialog).getByRole("button", { name: "Record statement" }));
    await waitFor(() =>
      expect(fake.regulatory.recordIssuerStatement).toHaveBeenCalledWith(
        expect.objectContaining({ issuer_id: "uni-of-example", names: [{ name: "University of Example", from: "1990-01-01", until: null }], accreditations: [], claim_types: ["DEGREE"] }),
      ),
    );
    expect(await within(dialog).findByText(/Keep the earlier facts and end them from today/)).toBeTruthy();
    expect(within(dialog).getByText(/record it as a CORRECTION with a reason$/)).toBeTruthy();
  });
});

const run = {
  run_id: "run-1",
  commit: "a".repeat(40),
  environment: "ci",
  matrix_hash: "ef".repeat(32),
  started_at: "2026-11-14T10:00:00Z",
  finished_at: "2026-11-14T10:20:00Z",
  recorded_by: "op-recorder",
  run_hash: HASH,
  results: [
    { case: "T01", outcome: "PASS", passed: ["tests/t01.py::test_refused"], failed: [], not_run: [] },
    { case: "T02", outcome: "FAIL", passed: [], failed: ["tests/t02.py::test_kill_switch"], not_run: [] },
    { case: "T03", outcome: "NOT_RUN", passed: [], failed: [], not_run: ["tests/t03.py::test_slow"] },
  ],
  reviews: [{ reviewer: "op-9", decision: "REJECTED", note_ref: "QA-0", reviewed_at: "2026-11-15T00:00:00Z" }],
  p0_exit: { met: false, reasons: ["P0_CASE_NOT_PASSING:T02"], counts: { PASS: 1, FAIL: 1, NOT_RUN: 1 } },
};

describe("conformance run detail", () => {
  it("lists every case outcome with its tests, makes the FAIL visible, and only then lets the reviewer sign the run hash", async () => {
    fake.regulatory.conformanceMatrix.mockResolvedValue({ matrix_hash: HASH, counts: {}, as_of: "2026-11-12T00:00:00Z", cases: [] });
    fake.regulatory.conformanceRuns.mockResolvedValue({ items: [run] });
    fake.regulatory.conformanceRun.mockResolvedValue(run);
    fake.regulatory.reviewConformanceRun.mockResolvedValue(run);
    mount(<ConformancePage />);
    await userEvent.click(await screen.findByRole("tab", { name: "Runs" }));
    await userEvent.click(await screen.findByRole("button", { name: "Open run run-1" }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("1 case failed")).toBeTruthy();
    expect(fake.regulatory.conformanceRun).toHaveBeenCalledWith("run-1");
    expect(within(dialog).getByText("tests/t02.py::test_kill_switch")).toBeTruthy();
    expect(within(dialog).getByText("tests/t03.py::test_slow")).toBeTruthy();
    expect(within(dialog).getByText("FAIL: 1")).toBeTruthy();
    expect(within(dialog).getByText("NOT_RUN: 1")).toBeTruthy();
    expect(within(dialog).getByText("PASS: 1")).toBeTruthy();
    expect(within(dialog).getByText("a".repeat(40))).toBeTruthy();
    expect(within(dialog).getByText("ef".repeat(32))).toBeTruthy();
    expect(within(dialog).getByText(HASH)).toBeTruthy();
    expect(within(dialog).getByText(/QA-0/)).toBeTruthy();
    const table = within(dialog).getByRole("table", { name: "Case outcomes in this run" });
    expect(within(table).getByText("FAIL")).toBeTruthy();

    const sign = within(dialog).getByRole("button", { name: "Sign run hash" });
    await userEvent.type(within(dialog).getByLabelText(/^Note reference/), "QA-1");
    expect(sign.hasAttribute("disabled") || sign.getAttribute("aria-disabled") === "true").toBe(true);
    await userEvent.click(within(dialog).getByText("I have read this run's outcomes"));
    await userEvent.click(sign);
    await waitFor(() => expect(fake.regulatory.reviewConformanceRun).toHaveBeenCalledWith("run-1", "ACCEPTED", HASH, "QA-1"));
  });
});
