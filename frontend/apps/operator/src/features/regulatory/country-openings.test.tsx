/** Country features (CR-127), rendered for real against a mocked client. */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ToastProvider } from "@zoiko/ui";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

// jsdom has no CSS.escape, which react-aria's Select uses to focus the option it opens on.
if (!(globalThis as { CSS?: unknown }).CSS) Object.defineProperty(globalThis, "CSS", { value: { escape: (v: string) => v.replace(/[^\w-]/g, (c) => `\\${c}`) } });

const fake = vi.hoisted(() => ({ regulatory: { countryOpenings: vi.fn(), recordCountryOpening: vi.fn() } }));

vi.mock("@/lib/runtime", () => ({
  useRuntime: () => ({ api: fake, protect: <T,>(run: () => Promise<T>) => run(), session: { authenticated: true, subject: { sub: "op-1" }, csrf: "x" } }),
}));

import { CountryOpeningsPage } from "./country-openings";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const APPROVALS = [
  { key: "legal_assessment", label: "Legal assessment", owner: "Legal" },
  { key: "supplier_coverage", label: "Supplier coverage", owner: "Product / Trust" },
  { key: "privacy_and_data", label: "Privacy and data", owner: "Privacy" },
  { key: "security_and_resilience", label: "Security and resilience", owner: "Security / SRE" },
  { key: "local_support", label: "Local support", owner: "Support" },
  { key: "signoff", label: "Sign-off", owner: "Founder" },
];

const FEATURES = [
  { capability: "REGISTRATION", label: "Sign-up", unlocks: "People create a Zoiko iD account" },
  { capability: "INDIVIDUAL_VERIFICATION", label: "Identity checks", unlocks: "A person's identity is verified" },
  { capability: "BUSINESS_VERIFICATION", label: "Organization claims", unlocks: "A company is claimed and verified" },
  { capability: "CREDENTIAL_ISSUANCE", label: "Credentials", unlocks: "Verifiable credentials are issued" },
  { capability: "ENTERPRISE_USE", label: "Enterprise use", unlocks: "Organization workspaces and approvals" },
];

const closed = (capability: string, file_status = "DISABLED") => ({ capability, file_status, in_force: "DISABLED", opening: null });

const view = {
  environment: "production",
  features: FEATURES,
  approvals: APPROVALS,
  cells: [{ cell_id: "uk-1", region: "eu-west-2", country: "GB" }],
  countries: [
    { country: "GB", name: "United Kingdom", wave: 1, features: FEATURES.map((f) => closed(f.capability, f.capability === "REGISTRATION" ? "SANDBOX" : "DISABLED")) },
    {
      country: "FR",
      name: "France",
      wave: 1,
      features: FEATURES.map((f) =>
        f.capability === "REGISTRATION"
          ? {
              capability: "REGISTRATION",
              file_status: "DISABLED",
              in_force: "ENABLED",
              opening: { capability: "REGISTRATION", version: 1, status: "OPEN", effective_from: "2026-11-01T00:00:00+00:00", approvals: {}, home_cells: ["eu-1"], reason: null, recorded_by: "op-1", recorded_at: "2026-11-19T00:00:00+00:00" },
            }
          : closed(f.capability),
      ),
    },
  ],
};

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <main>
          <CountryOpeningsPage />
        </main>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

async function confirmAll(user: ReturnType<typeof userEvent.setup>) {
  for (const a of APPROVALS) await user.click(screen.getByRole("button", { name: `Confirm ${a.label}` }));
}

describe("country features", () => {
  it("shows each country with a switch per feature, counts what still needs approving, and says a test-only file setting does not count when deployed", async () => {
    fake.regulatory.countryOpenings.mockResolvedValue(view);
    mount();
    expect(await screen.findByText("1 of 10 features open. 9 still need their six approvals.")).toBeTruthy();
    for (const f of FEATURES) expect(screen.getAllByText(f.label).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Disable Sign-up for France" })).toBeTruthy(); // open: the button turns it off
    expect(screen.getByRole("button", { name: "Enable Organization claims for United Kingdom" })).toBeTruthy(); // closed: the button turns it on
    expect(screen.getByText(/never opens a feature/)).toBeTruthy();
  });

  it("enables organization claims without a home cell, once all six approvals are confirmed", async () => {
    fake.regulatory.countryOpenings.mockResolvedValue(view);
    fake.regulatory.recordCountryOpening.mockResolvedValue({ version: 1, status: "OPEN" });
    const user = userEvent.setup();
    mount();
    await user.click(await screen.findByRole("button", { name: "Enable Organization claims for United Kingdom" }));
    const submit = (await screen.findByRole("button", { name: "Enable Organization claims" })) as HTMLButtonElement;
    expect(screen.queryByText("Home cell")).toBeNull(); // only sign-up decides which cell holds an account
    expect(submit.disabled).toBe(true);
    expect(screen.getByText(/0 of 6 confirmed/)).toBeTruthy();
    for (const a of APPROVALS.slice(0, 5)) await user.click(screen.getByRole("button", { name: `Confirm ${a.label}` }));
    expect(screen.getByText(/5 of 6 confirmed/)).toBeTruthy();
    expect(submit.disabled).toBe(true); // the sixth is still waiting
    await user.click(screen.getByRole("button", { name: "Confirm Sign-off" }));
    await waitFor(() => expect(submit.disabled).toBe(false));
    await user.click(submit);
    await waitFor(() => expect(fake.regulatory.recordCountryOpening).toHaveBeenCalledTimes(1));
    const [country, body] = fake.regulatory.recordCountryOpening.mock.calls[0] as [string, { capability: string; action: string; home_cells: string[]; approvals: Record<string, string> }];
    expect(country).toBe("GB");
    expect(body.capability).toBe("BUSINESS_VERIFICATION");
    expect(body.action).toBe("OPEN");
    expect(body.home_cells).toEqual([]);
    expect(Object.keys(body.approvals).sort()).toEqual(APPROVALS.map((a) => a.key).sort());
    // nothing typed: each approval is recorded as confirmed in the console, never as a made-up document number
    for (const ref of Object.values(body.approvals)) expect(ref).toMatch(/^Confirmed in the Operator Console on \d{4}-\d{2}-\d{2}$/);
  }, 60_000);

  it("asks sign-up for its home cell", async () => {
    fake.regulatory.countryOpenings.mockResolvedValue(view);
    fake.regulatory.recordCountryOpening.mockResolvedValue({ version: 1, status: "OPEN" });
    const user = userEvent.setup();
    mount();
    await user.click(await screen.findByRole("button", { name: "Enable Sign-up for United Kingdom" }));
    const submit = (await screen.findByRole("button", { name: "Enable Sign-up" })) as HTMLButtonElement;
    expect(screen.getAllByText("Home cell").length).toBeGreaterThan(0);
    await confirmAll(user);
    await waitFor(() => expect(submit.disabled).toBe(false));
    await user.click(submit);
    await waitFor(() => expect(fake.regulatory.recordCountryOpening).toHaveBeenCalledTimes(1));
    const [, body] = fake.regulatory.recordCountryOpening.mock.calls[0] as [string, { capability: string; home_cells: string[] }];
    expect(body.capability).toBe("REGISTRATION");
    expect(body.home_cells).toEqual(["uk-1"]);
  }, 60_000);

  it("moves an approval by dragging it into Confirmed, back again, and ignores anything else dropped there", async () => {
    fake.regulatory.countryOpenings.mockResolvedValue(view);
    const user = userEvent.setup();
    mount();
    await user.click(await screen.findByRole("button", { name: "Enable Organization claims for United Kingdom" }));
    const confirmed = await screen.findByRole("region", { name: "Confirmed" });
    const toConfirm = screen.getByRole("region", { name: "To confirm" });
    const dragging = (key: string) => ({ dataTransfer: { getData: () => key, setData: vi.fn(), effectAllowed: "" } });
    fireEvent.drop(confirmed, dragging("legal_assessment"));
    expect(await screen.findByText(/1 of 6 confirmed/)).toBeTruthy();
    expect(within(confirmed).getByText("Legal assessment")).toBeTruthy();
    expect(within(toConfirm).queryByText("Legal assessment")).toBeNull();
    fireEvent.drop(confirmed, dragging("not-an-approval"));
    expect(screen.getByText(/1 of 6 confirmed/)).toBeTruthy();
    fireEvent.drop(toConfirm, dragging("legal_assessment"));
    expect(await screen.findByText(/0 of 6 confirmed/)).toBeTruthy();
    expect(within(toConfirm).getByText("Legal assessment")).toBeTruthy();
  });

  it("disables a feature with a record, not a deletion", async () => {
    fake.regulatory.countryOpenings.mockResolvedValue(view);
    fake.regulatory.recordCountryOpening.mockResolvedValue({ version: 2, status: "CLOSED" });
    const user = userEvent.setup();
    mount();
    await user.click(await screen.findByRole("button", { name: "Disable Sign-up for France" }));
    await user.click(await screen.findByRole("button", { name: "Disable Sign-up" }));
    await waitFor(() => expect(fake.regulatory.recordCountryOpening).toHaveBeenCalledWith("FR", { capability: "REGISTRATION", action: "CLOSE", reason: undefined }));
  });
});
