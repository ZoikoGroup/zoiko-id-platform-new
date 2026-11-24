/** The link screen after an invitation is made: the link, an email box with a Send by email button, and what happened to the email. */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ToastProvider } from "@zoiko/ui";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

const fake = vi.hoisted(() => ({ tenant: { emailInvitation: vi.fn() } }));

vi.mock("@/lib/runtime", () => ({
  useRuntime: () => ({ api: fake, protect: <T,>(run: () => Promise<T>) => run(), session: { authenticated: true, subject: { sub: "u-1" }, csrf: "x" } }),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }), useSearchParams: () => ({ get: () => null }), usePathname: () => "/admin/team" }));

import type { Invitation } from "@zoiko/api-client";

import { ShareLink } from "./team-page";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const base = {
  assignment_id: "as-1",
  principal_id: "p-1",
  role: "OPERATOR",
  status: "INVITED",
  origin: "INVITATION",
  granted_by: null,
  approved_by: null,
  created_at: "2026-11-23T08:00:00Z",
  activated_at: null,
  person: { principal_id: "p-1", member_no: 7, name: null, code: "M-0007", label: "M-0007" },
  enrollment_ticket: "TICKETaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  enrollment_ticket_expires_at: "2026-11-24T07:00:00Z",
} as unknown as Invitation;

function mount(invitation: Invitation, extra: { defaultEmail?: string; onUpdated?: (i: Invitation) => void } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <ShareLink invitation={invitation} workspace="acme" onDone={() => undefined} {...extra} />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe("the invitation link screen", () => {
  it("shows the link and a Send by email box that waits for a usable address", async () => {
    const user = userEvent.setup();
    mount(base);
    expect(screen.getByLabelText("Passkey setup link").textContent).toContain("/enroll?ticket=TICKET");
    const send = screen.getByRole("button", { name: "Send by email" }) as HTMLButtonElement;
    expect(send.disabled).toBe(true); // nothing typed yet
    await user.type(screen.getByLabelText("Email address"), "not-an-address");
    expect(send.disabled).toBe(true);
    await user.clear(screen.getByLabelText("Email address"));
    await user.type(screen.getByLabelText("Email address"), "asha.rao@example.com");
    await waitFor(() => expect(send.disabled).toBe(false));
  });

  it("sends the typed address for this invitation and replaces the link with the fresh one", async () => {
    const fresh = { ...base, enrollment_ticket: "FRESHbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", email: { status: "SENT", to: "a***@example.com" } } as Invitation;
    fake.tenant.emailInvitation.mockResolvedValue(fresh);
    const onUpdated = vi.fn();
    const user = userEvent.setup();
    mount(base, { onUpdated });
    await user.type(screen.getByLabelText("Email address"), "asha.rao@example.com");
    await user.click(screen.getByRole("button", { name: "Send by email" }));
    await waitFor(() => expect(fake.tenant.emailInvitation).toHaveBeenCalledWith("as-1", "asha.rao@example.com"));
    await waitFor(() => expect(onUpdated).toHaveBeenCalledWith(fresh)); // the page now holds the new single-use link
  });

  it("says plainly that the invitation was emailed, offers Send again, and lets the person finish", () => {
    mount({ ...base, email: { status: "SENT", to: "a***@example.com" } } as Invitation, { defaultEmail: "asha.rao@example.com" });
    expect(screen.getByText("Invitation emailed")).toBeTruthy();
    expect(screen.getByText(/a\*\*\*@example\.com/)).toBeTruthy(); // the masked address, never the whole one
    expect(screen.getByRole("button", { name: "Send again" })).toBeTruthy();
    expect((screen.getByRole("button", { name: "Done" }) as HTMLButtonElement).disabled).toBe(false); // no need to tick "I shared it"
  });

  it("keeps Done locked until the person confirms they shared the link, when nothing was emailed", async () => {
    const user = userEvent.setup();
    mount(base);
    expect((screen.getByRole("button", { name: "Done" }) as HTMLButtonElement).disabled).toBe(true);
    await user.click(screen.getByRole("checkbox"));
    await waitFor(() => expect((screen.getByRole("button", { name: "Done" }) as HTMLButtonElement).disabled).toBe(false));
  });

  it("explains a failed email without losing the link", () => {
    mount({ ...base, email: { status: "NOT_CONFIGURED" } } as Invitation);
    expect(screen.getByText("Email is not switched on")).toBeTruthy();
    expect(screen.getByLabelText("Passkey setup link")).toBeTruthy(); // the link is still there to copy
    expect(screen.queryByText("NOT_CONFIGURED")).toBeNull(); // no raw code
  });
});
