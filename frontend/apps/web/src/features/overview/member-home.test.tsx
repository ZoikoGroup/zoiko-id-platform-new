/** The member's home page: who they are here, how they sign in, their own account, and how to ask for more. No company administration. */
import type { TenantView } from "@zoiko/api-client";
import { cleanup, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const shared = vi.hoisted(() => ({ passkeys: 0, codes: 0, open: 0 }));

vi.mock("@/lib/cell", () => ({
  useCellQuery: (key: string[]) => {
    if (key.includes("authenticators")) return { data: { items: Array.from({ length: shared.passkeys }, () => ({ status: "ACTIVE" })) } };
    if (key.includes("recovery-status")) return { data: { codes_remaining: shared.codes } };
    return { data: { open: shared.open } };
  },
}));

vi.mock("@zoiko/ui", () => ({
  Banner: ({ title, children }: { title: string; children?: ReactNode }) => (
    <div role="note">
      <b>{title}</b>
      {children}
    </div>
  ),
  Card: ({ title, children }: { title?: string; children: ReactNode }) => (
    <section>
      {title ? <h3>{title}</h3> : null}
      {children}
    </section>
  ),
}));

import { MemberHome } from "./member-home";

const tenant = { display_name: "Acme Widgets Limited", tenant_slug: "u-1" } as unknown as TenantView;

afterEach(() => {
  cleanup();
  Object.assign(shared, { passkeys: 0, codes: 0, open: 0 });
});

describe("MemberHome", () => {
  it("names the company and the role and offers the person's own account only", () => {
    render(<MemberHome tenant={tenant} roleLabel="Member" />);
    expect(screen.getByText("Acme Widgets Limited")).toBeTruthy();
    expect(screen.getByText("Member")).toBeTruthy();
    for (const label of ["Passkeys", "Sessions", "Recovery codes", "Connected apps", "Identity verification", "Privacy", "Alerts & tasks"]) {
      expect(screen.getByText(label)).toBeTruthy();
    }
    for (const admin of ["Team & invitations", "Approvals", "Billing", "Company settings", "Identity providers"]) {
      expect(screen.queryByText(admin)).toBeNull();
    }
    expect(screen.getByText("Need more access?")).toBeTruthy();
  });

  it("tells a person with no passkey they sign in through the company account, without a warning about a single passkey", () => {
    render(<MemberHome tenant={tenant} roleLabel="Member" />);
    expect(screen.getByText("You sign in with your company account")).toBeTruthy();
    expect(screen.queryByText("Protect your access")).toBeNull();
  });

  it("warns about a single passkey with no recovery codes, and is calm with two", () => {
    shared.passkeys = 1;
    const { unmount } = render(<MemberHome tenant={tenant} roleLabel="Member" />);
    expect(screen.getByText("Protect your access")).toBeTruthy();
    unmount();
    shared.passkeys = 2;
    render(<MemberHome tenant={tenant} roleLabel="Member" />);
    expect(screen.getByText("Your sign-in is protected")).toBeTruthy();
  });

  it("shows how many alerts are open", () => {
    shared.open = 3;
    render(<MemberHome tenant={tenant} roleLabel="Member" />);
    expect(screen.getByText("3 open")).toBeTruthy();
  });
});
