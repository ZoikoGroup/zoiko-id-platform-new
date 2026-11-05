/** The Bulk exports tab: what is listed, who can download, and exactly what "Create and download" sends. */
import type { BulkExport } from "@zoiko/api-client";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const shared = vi.hoisted(() => ({ items: [] as unknown[], mutate: vi.fn() }));

vi.mock("@/lib/cell", () => ({
  useCellQuery: () => ({ data: { items: shared.items }, isPending: false }),
  useCellMutation: (fn: unknown) => ({ mutate: (v: unknown) => shared.mutate(String(fn), v), isPending: false, error: null, reset: () => undefined }),
  QueryView: ({ query, children }: { query: { data: unknown }; children: (d: unknown) => ReactNode }) => <>{children(query.data)}</>,
  ApiErrorView: () => <div>generic error</div>,
}));

vi.mock("./receipts", () => ({ EXPORT_MODES: [{ id: "CUSTOMER_VERIFY", label: "Customer verification" }] }));

vi.mock("@zoiko/ui", () => ({
  Banner: ({ title }: { title: string }) => <div>{title}</div>,
  Button: ({ children, onPress, isDisabled }: { children: ReactNode; onPress?: () => void; isDisabled?: boolean }) => (
    <button onClick={onPress} disabled={isDisabled}>
      {children}
    </button>
  ),
  Card: ({ title, actions, children }: { title?: string; actions?: ReactNode; children: ReactNode }) => (
    <section>
      <h3>{title}</h3>
      {actions}
      {children}
    </section>
  ),
  CopyId: ({ value }: { value: string }) => <span>{value}</span>,
  DataTable: ({ columns, rows, empty }: { columns: { key: string; render?: (r: never) => ReactNode }[]; rows: never[]; empty: ReactNode }) =>
    rows.length ? (
      <div>
        {rows.map((r, i) => (
          <div key={i}>{columns.map((c) => (c.render ? <span key={c.key}>{c.render(r)}</span> : null))}</div>
        ))}
      </div>
    ) : (
      empty
    ),
  DateTime: ({ value }: { value: string }) => <span>{value}</span>,
  Dialog: ({ isOpen, title, children, footer }: { isOpen: boolean; title: string; children: ReactNode; footer: ReactNode }) =>
    isOpen ? (
      <div role="dialog">
        <h2>{title}</h2>
        {children}
        {footer}
      </div>
    ) : null,
  EmptyState: ({ title }: { title: string }) => <div>{title}</div>,
  RiskBadge: () => null,
  Select: ({ label, value, onChange, options }: { label: string; value: string; onChange: (v: string) => void; options: { id: string; label: string }[] }) => (
    <label>
      {label}
      <select aria-label={label} value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map((o) => (
          <option key={o.id} value={o.id}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  ),
  StatusBadge: ({ state }: { state: string }) => <span>{state}</span>,
  TextField: ({ label, value, onChange, errorMessage }: { label: string; value: string; onChange: (v: string) => void; errorMessage?: string }) => (
    <label>
      {label}
      <textarea value={value} onChange={(e) => onChange(e.target.value)} />
      {errorMessage ? <span>{errorMessage}</span> : null}
    </label>
  ),
}));

import { BulkExportsSection } from "./bulk-exports";

const exp = (over: Partial<BulkExport> = {}): BulkExport => ({
  export_id: "e1",
  requested_by: "h1",
  purpose: "Auditor request",
  mode: "AUDITOR",
  receipt_count: 12,
  package_hash: "sha256:abc",
  status: "AVAILABLE",
  downloads: 1,
  created_at: "2026-11-05T12:00:00Z",
  expires_at: "2099-01-01T00:00:00Z",
  erased_at: null,
  ...over,
});
const A = "0b1c2d3e-4f50-4162-8374-95a6b7c8d9e0";

afterEach(() => {
  cleanup();
  shared.items = [];
  shared.mutate.mockReset();
});

describe("BulkExportsSection", () => {
  it("shows an empty state, then the exports with a download only for one that is still available", () => {
    const { unmount } = render(<BulkExportsSection />);
    expect(screen.getByText("No bulk exports yet")).toBeTruthy();
    unmount();
    shared.items = [exp(), exp({ export_id: "e2", status: "EXPIRED", purpose: "Old one" })];
    render(<BulkExportsSection />);
    expect(screen.getByText("Auditor request")).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "Download" })).toHaveLength(1); // the expired one has been erased
  });

  it("creates only when the receipts, purpose and case are valid, and says it asks for a fresh sign-in", async () => {
    render(<BulkExportsSection />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "New bulk export" }));
    expect(screen.getByText("This asks you to sign in again")).toBeTruthy();
    const create = () => screen.getByRole("button", { name: "Create and download" }) as HTMLButtonElement;
    expect(create().disabled).toBe(true);
    fireEvent.change(screen.getByLabelText(/Receipt IDs/), { target: { value: `${A}\nnot-an-id` } });
    expect(screen.getByText(/Not a receipt ID: not-an-id/)).toBeTruthy();
    expect(create().disabled).toBe(true);
    fireEvent.change(screen.getByLabelText(/Receipt IDs/), { target: { value: A } });
    fireEvent.change(screen.getByLabelText("Purpose"), { target: { value: "Customer audit request" } });
    fireEvent.change(screen.getByLabelText("Case reference"), { target: { value: "CASE-1" } });
    expect(create().disabled).toBe(false);
    await user.click(create());
    expect(shared.mutate).toHaveBeenCalled();
  });
});
