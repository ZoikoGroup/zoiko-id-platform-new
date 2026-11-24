/** "What you are allowed to do": a Member sees the permissions given to them, in plain words, on My limits and permissions. */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const fake = vi.hoisted(() => ({ authority: { myGrants: vi.fn() } }));

vi.mock("@/lib/runtime", () => ({
  useRuntime: () => ({ api: fake, protect: <T,>(run: () => Promise<T>) => run(), session: { authenticated: true, subject: { sub: "u-1" }, csrf: "x" } }),
}));

import { MyPermissions } from "./my-permissions-card";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MyPermissions />
    </QueryClientProvider>,
  );
}

const future = new Date(Date.now() + 10 * 86_400_000).toISOString();

describe("what you are allowed to do", () => {
  it("lists the permission a Member was given in plain words, with its end and limit", async () => {
    fake.authority.myGrants.mockResolvedValue({
      items: [
        {
          authority_grant_id: "g1",
          status: "ACTIVE",
          granted_by: "owner",
          actions: ["invoice.approve", "payment.release", "purchase.create"],
          value_limit: { amount: "500.00", currency: "USD" },
          valid_from: new Date().toISOString(),
          valid_to: future,
          delegation_allowed: false,
        },
      ],
    });
    mount();
    const list = await screen.findByRole("list", { name: "My permissions" });
    const actions = within(list).getByRole("list", { name: "Allowed actions" });
    expect(within(actions).getByText("Approve an invoice")).toBeTruthy();
    expect(within(actions).getByText("Release a payment")).toBeTruthy();
    expect(within(actions).getByText("Create a purchase")).toBeTruthy();
    expect(within(list).getByText(/Expires in (9|10) days/)).toBeTruthy();
    expect(within(list).getByText("Up to 500.00 USD")).toBeTruthy();
    expect(screen.queryByText("payment.release")).toBeNull(); // no raw action id reaches the person
  });

  it("says plainly when there is nothing, and what they can still do", async () => {
    fake.authority.myGrants.mockResolvedValue({ items: [] });
    mount();
    expect(await screen.findByText("No extra permissions")).toBeTruthy();
    expect(screen.getByText(/manage your passkeys and sessions/)).toBeTruthy();
    expect(screen.queryByRole("list", { name: "My permissions" })).toBeNull();
  });

  it("shows a permission with no list of actions as everything the company allows", async () => {
    fake.authority.myGrants.mockResolvedValue({
      items: [{ authority_grant_id: "g2", status: "ACTIVE", granted_by: "o", actions: null, value_limit: null, valid_from: new Date().toISOString(), valid_to: null, delegation_allowed: true }],
    });
    mount();
    expect(await screen.findByText("Everything the company allows")).toBeTruthy();
    expect(screen.getByText("No end date")).toBeTruthy();
    expect(screen.getByText(/pass part of this on/)).toBeTruthy();
  });
});
