/** The regulatory screens (ZID-RCG-003 WF-01..08), rendered for real against a mocked client. */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError } from "@zoiko/api-client";
import { ToastProvider } from "@zoiko/ui";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const HASH = "ab".repeat(32);

const fake = vi.hoisted(() => ({
  regulatory: {
    readiness: vi.fn(),
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
  },
}));

vi.mock("@/lib/runtime", () => ({
  useRuntime: () => ({ api: fake, protect: <T,>(run: () => Promise<T>) => run(), session: { authenticated: true, subject: { sub: "op-1" }, csrf: "x" } }),
}));

import { ClaimsPage } from "./claims";
import { ConformancePage } from "./conformance";
import { HoldsPage } from "./holds";
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
