/** The public pricing page's interactive part, rendered for real (ZID-UX-PRICE-004 WF-P02 to P07; QA P01, P03, P26, P27, P28, P31, P38). Structural WCAG 2.2 AA checks with axe in a
 * simulated browser, plus what the page must and must not say. A screen-reader run on real devices is separate evidence. */
import type { PublicAvailability, PublicPricingPage } from "@zoiko/api-client";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PricingExplorer } from "./explorer";

// The page renders about 250 country options in each of several selects and axe walks all of it, so these tests take a few seconds alone and longer when the whole suite runs in parallel.
vi.setConfig({ testTimeout: 60_000 });

const FAMILIES = ["IDV", "DOC", "BIO", "KYB", "SCR", "QUAL", "PRO", "WORK", "AUTHN", "DELEG", "VC", "SIGN", "EVID", "API", "SOV", "REV"];
const state = (over: Record<string, "included" | "optional" | "quote"> = {}) => Object.fromEntries(FAMILIES.map((f) => [f, over[f] ?? "included"]));

const PAGE: PublicPricingPage = {
  version: "t",
  packaging_status: "PROPOSED",
  pricing_status: "UNPRICED",
  families: FAMILIES.map((f) => ({ family: f, name: `${f} service`, unit: `${f} unit`, sales_qualifier: "", regulated: f === "SIGN", depends_on: f === "SIGN" ? ["IDV"] : [] })),
  tiers: [
    { code: "ESSENTIALS", label: "Essentials", fit: "Launch trusted onboarding", cta_kind: "VIEW_RATES", cta_label: "View eligible rates", family_state: state({ KYB: "quote", SIGN: "quote" }) },
    { code: "BUSINESS", label: "Business", fit: "Expand verification", cta_kind: "CONFIGURE", cta_label: "Configure Business", family_state: state({ KYB: "optional", SIGN: "quote" }) },
    { code: "ENTERPRISE", label: "Enterprise", fit: "Global, multi-entity assurance", cta_kind: "BUILD_SOLUTION", cta_label: "Request enterprise quote", family_state: state({ KYB: "optional", SIGN: "quote" }) },
  ],
  paths: [
    { code: "SANDBOX", label: "Developer sandbox", text: "Test integrations without live charges.", cta_kind: "EXPLORE_SANDBOX", cta_label: "Explore the sandbox" },
    { code: "PARTNER", label: "Embedded partner", text: "Agreed by contract.", cta_kind: "PARTNER_INQUIRY", cta_label: "Partner inquiry" },
  ],
  use_cases: [
    { code: "CUSTOMER_KYC", label: "Customer onboarding", families: ["IDV", "DOC"] },
    { code: "BUSINESS_ONBOARDING", label: "Business onboarding", families: ["KYB", "IDV"] },
    { code: "SIGNING", label: "Regulated transaction", families: ["SIGN"] },
  ],
  disclosures: { price_basis: "p", tax: "t", commitment: "c", no_surprise: "n" },
};

const AVAILABILITY: PublicAvailability = {
  market_matrix_version: 4,
  as_of: "2026-11-13T00:00:00Z",
  dimensions: { subject_country: "DE", payer_country: null, operating_region: null, issuer_country: { value: null, judged: false, note: "Recorded for your quote." } },
  cards: FAMILIES.map((f) => ({
    family: f,
    name: `${f} service`,
    unit: `${f} unit`,
    regulated: f === "SIGN",
    state: f === "IDV" ? "AVAILABLE" : f === "SIGN" ? "RESTRICTED" : "SALES_ASSISTED",
    reason_codes: f === "IDV" ? [] : ["PROVIDER"],
    reasons: f === "IDV" ? [] : [{ cause: "PROVIDER", text: "Provider or issuer rights for this market are not in force." }],
    terms: f === "IDV" ? { seller_entity: "Zoiko Tech GmbH", currency: "EUR", data_region: "eu-central" } : null,
    provider_attribution: null,
  })),
  released_markets: ["DE"],
  released_regions: ["eu-central"],
  note: "n",
};

const calls: { url: string; init?: RequestInit }[] = [];

beforeEach(() => {
  calls.length = 0;
  // jsdom has no CSS.escape, which React Aria's Select uses to find the option it focuses. Stubbed for each test because afterEach unstubs every global.
  vi.stubGlobal("CSS", { escape: (v: string) => v.replace(/[^a-zA-Z0-9_-]/g, (ch) => "\\" + ch), supports: () => false });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, init });
      const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
      if (url.includes("/api/pricing/availability")) return json(AVAILABILITY);
      if (url.includes("/api/pricing/leads")) {
        const sent = JSON.parse(String(init?.body)) as { company: string };
        if (sent.company === "Boom Ltd") return json({ success: false, error: { message: "The service is temporarily unavailable." } }, 503);
        return json({ reference: "ref-123", status: "RECEIVED", duplicate: sent.company === "Twice Ltd", message: "Thank you. Sales will be in touch. This inquiry has not created an account, a quote, a contract or a payment, and you will not be charged." }, 201);
      }
      return json({}, 404);
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

async function violations(container: HTMLElement) {
  const r = await axe.run(container, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] }, rules: { "color-contrast": { enabled: false } } });
  return r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
}

async function pick(user: ReturnType<typeof userEvent.setup>, label: RegExp, option: RegExp) {
  await user.click(screen.getByRole("button", { name: label }));
  await user.click(await screen.findByRole("option", { name: option }));
}

describe("the pricing explorer", () => {
  it("has no structural accessibility violation and shows no price or free offer in its first view", async () => {
    const { container } = render(<main><PricingExplorer page={PAGE} /></main>);
    expect(await violations(container)).toEqual([]);
    expect(container.textContent).not.toMatch(/[$€£]\s?\d|\bper (month|check)\b|\bfree\b|\btrial\b/i);
    expect(screen.queryByRole("heading", { level: 1 })).toBeNull();
    expect(screen.getAllByText("Price: Quote required")).toHaveLength(3);
    expect(screen.getByText(/Plan contents are being finalised/)).toBeTruthy();
  });

  it("shows each plan's packaging in words and, before a market is chosen, no market column and no availability", () => {
    render(<PricingExplorer page={PAGE} />);
    const table = screen.getByRole("table");
    expect(within(table).queryByText("In your market")).toBeNull();
    expect(within(table).getAllByText("Quote only").length).toBeGreaterThan(0);
    expect(screen.getByText(/no service is shown as available anywhere/)).toBeTruthy();
  });

  it("checks the market on the server, then tells availability apart in words and with the reasons", async () => {
    const user = userEvent.setup();
    const { container } = render(<main><PricingExplorer page={PAGE} /></main>);
    expect((screen.getByRole("button", { name: "Check availability" }) as HTMLButtonElement).disabled).toBe(true);
    await pick(user, /Country of the people checked/, /Germany/);
    await user.click(screen.getByRole("button", { name: "Check availability" }));
    await waitFor(() => expect(within(screen.getByRole("table")).getByText("In your market")).toBeTruthy());
    expect(calls.at(-1)?.url).toBe("/api/pricing/availability?subject_country=DE");
    const cov = screen.getByRole("region", { name: "Available" });
    expect(within(cov).getByText(/Zoiko Tech GmbH/)).toBeTruthy();
    expect(within(screen.getByRole("region", { name: "Restricted" })).getByText("SIGN service")).toBeTruthy();
    expect(within(screen.getByRole("region", { name: "Sales-assisted" })).getAllByText(/rights for this market are not in force/).length).toBeGreaterThan(0);
    expect(await violations(container)).toEqual([]);
    // the market can only make a cell more cautious: an included service the market restricts reads Not in market
    expect(within(screen.getByRole("table")).getAllByText("Not in market").length).toBe(3);
  });

  it("drops the old answer the moment the market changes", async () => {
    const user = userEvent.setup();
    render(<PricingExplorer page={PAGE} />);
    await pick(user, /Country of the people checked/, /Germany/);
    await user.click(screen.getByRole("button", { name: "Check availability" }));
    await waitFor(() => expect(within(screen.getByRole("table")).getByText("In your market")).toBeTruthy());
    await pick(user, /Country of the paying company/, /France/);
    expect(within(screen.getByRole("table")).queryByText("In your market")).toBeNull();
    expect(screen.getByText(/no service is shown as available anywhere/)).toBeTruthy();
  });

  it("preloads services from a use case, names a missing prerequisite and recommends a plan only by the rule", async () => {
    const user = userEvent.setup();
    render(<PricingExplorer page={PAGE} />);
    await user.click(screen.getByRole("checkbox", { name: "Customer onboarding" }));
    expect(screen.getByText(/Your starting scope: DOC service, IDV service/)).toBeTruthy();
    expect(screen.getByText(/Recommended for Customer onboarding/)).toBeTruthy();
    // a signature without a verified identity: the missing service is named, and no plan is recommended because every plan leaves signatures to Sales
    await user.click(screen.getByRole("checkbox", { name: "Customer onboarding" }));
    await user.click(screen.getByRole("checkbox", { name: "Regulated transaction" }));
    expect(screen.getByText(/SIGN service needs IDV service/)).toBeTruthy();
    expect(screen.queryByText(/Recommended for/)).toBeNull();
    await user.click(screen.getByRole("button", { name: "Add what is missing" }));
    expect(screen.queryByText(/needs IDV service/)).toBeNull();
    expect(screen.getByText(/Your starting scope: IDV service, SIGN service/)).toBeTruthy();
  });
});

describe("the inquiry form", () => {
  async function fill(user: ReturnType<typeof userEvent.setup>, company = "Acme Bank") {
    await user.type(screen.getByRole("textbox", { name: /Your name/ }), "Ana Silva");
    await user.type(screen.getByRole("textbox", { name: /Work email/ }), "ana@example.com");
    await user.type(screen.getByRole("textbox", { name: /^Company/ }), company);
    await pick(user, /Country of your company/, /Portugal/);
    await pick(user, /Expected volume/, /10,000 to 100,000/);
  }

  it("has no structural violation, hides its robot trap from people and says plainly that nothing is bought", async () => {
    const { container } = render(<main><PricingExplorer page={PAGE} /></main>);
    expect(await violations(container)).toEqual([]);
    const trap = container.querySelector('input[name="website"]') as HTMLInputElement;
    expect(trap.tabIndex).toBe(-1);
    expect(trap.closest("[aria-hidden]")).not.toBeNull();
    expect(screen.getByText(/does not create an account, a quote, a contract or a payment/)).toBeTruthy();
  });

  it("lists what is wrong in an alert before sending anything, and refuses an identity number", async () => {
    const user = userEvent.setup();
    render(<PricingExplorer page={PAGE} />);
    await user.click(screen.getByRole("button", { name: "Request enterprise quote" }));
    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText(/Enter your name/)).toBeTruthy();
    expect(calls.filter((c) => c.url.includes("/leads"))).toHaveLength(0);
    await fill(user);
    await user.type(screen.getByRole("textbox", { name: /Security or procurement needs/ }), "passport 123456789");
    await user.click(screen.getByRole("button", { name: "Request enterprise quote" }));
    expect((await screen.findAllByText(/identity, document or account numbers/)).length).toBeGreaterThan(0);
    expect(calls.filter((c) => c.url.includes("/leads"))).toHaveLength(0);
  });

  it("sends the chosen scope with the inquiry and confirms without promising a quote, a contract or a charge", async () => {
    const user = userEvent.setup();
    render(<PricingExplorer page={PAGE} />);
    await user.click(screen.getByRole("checkbox", { name: "Customer onboarding" }));
    await fill(user);
    await user.click(screen.getByRole("button", { name: "Request enterprise quote" }));
    expect(await screen.findByText(/Reference/)).toBeTruthy();
    const sent = JSON.parse(String(calls.find((c) => c.url.includes("/leads"))?.init?.body)) as Record<string, unknown>;
    expect(sent).toMatchObject({ kind: "ENTERPRISE", work_email: "ana@example.com", country: "PT", capabilities: ["DOC", "IDV"], volume_band: "FROM_10K_TO_100K", website: "" });
    expect(JSON.stringify(sent)).not.toMatch(/price|amount|tenant|entitlement/i);
    const confirmation = screen.getAllByRole("status").find((el) => /not created an account/.test(el.textContent ?? ""));
    expect(confirmation).toBeTruthy();
  });

  it("says when an inquiry repeats an open one and shows a failure as an alert", async () => {
    const user = userEvent.setup();
    const { unmount } = render(<PricingExplorer page={PAGE} />);
    await fill(user, "Twice Ltd");
    await user.click(screen.getByRole("button", { name: "Request enterprise quote" }));
    expect(await screen.findByText(/already have an open inquiry/)).toBeTruthy();
    unmount();
    cleanup();
    const again = userEvent.setup();
    render(<PricingExplorer page={PAGE} />);
    await fill(again, "Boom Ltd");
    await again.click(screen.getByRole("button", { name: "Request enterprise quote" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/temporarily unavailable/);
  });

  it("switches to a partner inquiry that asks for the kind of partner", async () => {
    const user = userEvent.setup();
    render(<PricingExplorer page={PAGE} />);
    await user.click(screen.getByRole("radio", { name: /A partner arrangement/ }));
    expect(screen.getByRole("button", { name: "Send partner inquiry" })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Kind of partner/ })).toBeTruthy();
  });
});
