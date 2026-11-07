/** The Agents pickers: a person is chosen by name, tools are ticked, and a missing grant is explained before the form is filled in. */
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  queries: {} as Record<string, { data?: unknown; isError?: boolean; isSuccess?: boolean; isPending?: boolean }>,
  me: "me-1" as string | undefined,
}));

vi.mock("@/lib/cell", () => ({
  // The key's second-to-last word picks the canned answer, so each test says what the cell returned.
  useCellQuery: (key: string[]) => {
    const name = key.includes("members") ? "members" : key.includes("principals") ? "principals" : key.includes("grants") ? "grants" : "other";
    return state.queries[name] ?? { data: undefined, isPending: true };
  },
}));
vi.mock("next/link", () => ({ default: ({ href, children }: { href: string; children: ReactNode }) => <a href={href}>{children}</a> }));
vi.mock("../authority/shared", () => ({
  useMe: () => state.me,
  shortId: (s: string) => s.slice(0, 8),
  PrincipalField: ({ label }: { label: string }) => <div>fallback id field: {label}</div>,
}));
vi.mock("@zoiko/ui", () => ({
  Banner: ({ title, children }: { title: string; children: ReactNode }) => (
    <div role="alert">
      <strong>{title}</strong>
      {children}
    </div>
  ),
  Checkbox: ({ children, isSelected, onChange }: { children: ReactNode; isSelected?: boolean; onChange?: (on: boolean) => void }) => (
    <label>
      <input type="checkbox" checked={Boolean(isSelected)} onChange={(e) => onChange?.(e.target.checked)} />
      {children}
    </label>
  ),
  Select: ({ label, options, value, onChange }: { label: string; options: { id: string; label: string; description?: string }[]; value?: string | null; onChange?: (v: string) => void }) => (
    <label>
      {label}
      <select aria-label={label} value={value ?? ""} onChange={(e) => onChange?.(e.target.value)}>
        <option value="">—</option>
        {options.map((o) => (
          <option key={o.id} value={o.id}>
            {o.label}|{o.description ?? ""}
          </option>
        ))}
      </select>
    </label>
  ),
  StatusBadge: ({ state: s }: { state: string }) => <span>{s}</span>,
  humanize: (s: string) => s.toLowerCase(),
}));

import { AuthorityNotice, PersonSelect, ServerSelect, ToolChecklist } from "./pickers";

beforeEach(() => {
  state.queries = {};
  state.me = "me-1";
});
afterEach(cleanup);

const member = (id: string, label: string, role: string, status = "ACTIVE") => ({ principal_id: id, role, status, person: { principal_id: id, label } });

describe("PersonSelect", () => {
  it("lists active people by name with their role, and marks the signed-in person", () => {
    state.queries.members = { isSuccess: true, data: { items: [member("me-1", "Anil Kumar (M-0001)", "OWNER"), member("p2", "Ravi (M-0002)", "ADMIN"), member("p3", "Invited One", "MEMBER", "INVITED")] } };
    render(<PersonSelect label="Sponsor" value="" onChange={() => undefined} />);
    const options = [...screen.getByLabelText("Sponsor").querySelectorAll("option")].map((o) => o.textContent);
    expect(options).toContain("Anil Kumar (M-0001)|owner · you");
    expect(options).toContain("Ravi (M-0002)|admin");
    expect(options.join()).not.toContain("Invited One");
  });

  it("keeps a sponsor who is no longer a member visible instead of dropping them", () => {
    state.queries.members = { isSuccess: true, data: { items: [member("me-1", "Anil", "OWNER")] } };
    render(<PersonSelect label="Sponsor" value="gone-0000-1111" onChange={() => undefined} />);
    expect([...screen.getByLabelText("Sponsor").querySelectorAll("option")].map((o) => o.textContent)).toContain("gone-000|Not a current member");
  });

  it("falls back to the plain ID field when the member list is not available", () => {
    state.queries.members = { isError: true };
    render(<PersonSelect label="Sponsor" value="" onChange={() => undefined} />);
    expect(screen.getByText("fallback id field: Sponsor")).toBeTruthy();
  });
});

describe("ServerSelect", () => {
  it("offers only active applications, services and workloads", () => {
    const p = (id: string, principal_type: string, status = "ACTIVE") => ({ principal_id: id, principal_type, status });
    state.queries.principals = { isSuccess: true, data: { items: [p("app-0001", "APPLICATION"), p("human-01", "HUMAN"), p("svc-0002", "SERVICE", "SUSPENDED"), p("wl-00003", "WORKLOAD")] } };
    render(<ServerSelect value="" onChange={() => undefined} />);
    const texts = [...screen.getByLabelText("Hosting server").querySelectorAll("option")].map((o) => o.textContent).join("\n");
    expect(texts).toContain("app-0001");
    expect(texts).toContain("wl-00003");
    expect(texts).not.toContain("human-01");
    expect(texts).not.toContain("svc-0002");
  });
});

describe("ToolChecklist", () => {
  const tool = (tool_id: string, status: string) => ({ tool_id, status, canonical_action: `${tool_id}.act`, version: "1" }) as never;

  function Harness() {
    const [v, setV] = useState<string[]>([]);
    return (
      <>
        <ToolChecklist tools={[tool("a.read", "ACTIVE"), tool("b.write", "DRAFT"), tool("c.old", "RETIRED")]} value={v} onChange={setV} emptyHint="none yet" />
        <output data-testid="v">{v.join(",")}</output>
      </>
    );
  }

  it("lets a person tick tools, marks drafts, and hides retired ones", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    expect(screen.queryByText("c.old")).toBeNull();
    expect(screen.getByText("DRAFT")).toBeTruthy();
    await user.click(screen.getByLabelText(/a\.read/));
    await user.click(screen.getByLabelText(/b\.write/));
    expect(screen.getByTestId("v").textContent).toBe("a.read,b.write");
    await user.click(screen.getByLabelText(/a\.read/));
    expect(screen.getByTestId("v").textContent).toBe("b.write");
  });

  it("says what to do first when there are no tools", () => {
    render(<ToolChecklist tools={[]} value={[]} onChange={() => undefined} emptyHint="Register one under Tools first." />);
    expect(screen.getByText("Register one under Tools first.")).toBeTruthy();
  });
});

describe("AuthorityNotice", () => {
  const grant = (actions: string[] | null) => ({ actions });

  it("explains a missing grant before the form is filled in, and points to where to fix it", () => {
    state.queries.grants = { data: { items: [grant(["machine.agent.read", "mandate.read"])] } };
    render(<AuthorityNotice needs={["machine.tool.manage"]} what="register a tool" />);
    expect(screen.getByRole("alert").textContent).toContain("register a tool");
    expect(screen.getByRole("alert").textContent).toContain("machine.tool.manage");
    expect(screen.getByRole("link", { name: /Authority/ }).getAttribute("href")).toBe("/admin/authority");
  });

  it("says nothing when a grant already covers it, including an unlimited or wildcard grant", () => {
    state.queries.grants = { data: { items: [grant(["machine.tool.manage"])] } };
    const { unmount } = render(<AuthorityNotice needs={["machine.tool.manage"]} what="x" />);
    expect(screen.queryByRole("alert")).toBeNull();
    unmount();
    state.queries.grants = { data: { items: [grant(null)] } };
    const second = render(<AuthorityNotice needs={["machine.tool.manage"]} what="x" />);
    expect(screen.queryByRole("alert")).toBeNull();
    second.unmount();
    state.queries.grants = { data: { items: [grant(["machine.*"])] } };
    render(<AuthorityNotice needs={["machine.tool.manage", "machine.agent.activate"]} what="x" />);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows nothing while the grants are still loading", () => {
    state.queries.grants = { data: undefined, isPending: true };
    render(<AuthorityNotice needs={["machine.tool.manage"]} what="x" />);
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
