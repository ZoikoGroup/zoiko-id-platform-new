/** The Directory sync section: what is listed, the actions, and what "Connect Auth0" sends (the secret goes once, in the request, and is never listed). */
import type { DirectorySync, ProvisioningSourceItem } from "@zoiko/api-client";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const shared = vi.hoisted(() => ({
  syncs: [] as unknown[],
  mutate: vi.fn(),
  calls: [] as { fn: unknown }[],
}));

vi.mock("@/lib/cell", () => ({
  useCellQuery: () => ({ data: { items: shared.syncs }, isPending: false }),
  useCellMutation: (fn: unknown) => ({ mutate: (v: unknown) => shared.mutate(fn, v), isPending: false, error: null, reset: () => undefined }),
  QueryView: ({ query, children }: { query: { data: unknown }; children: (d: unknown) => ReactNode }) => <>{children(query.data)}</>,
  ApiErrorView: () => <div>generic error</div>,
}));

vi.mock("@zoiko/ui", () => ({
  Banner: ({ title, children }: { title: string; children?: ReactNode }) => (
    <div>
      <b>{title}</b>
      {children}
    </div>
  ),
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
  Select: ({ label, value, onChange, options }: { label: string; value: string | null; onChange: (v: string) => void; options: { id: string; label: string }[] }) => (
    <label>
      {label}
      <select aria-label={label} value={value ?? ""} onChange={(e) => onChange(e.target.value)}>
        <option value="" />
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
      <input value={value} onChange={(e) => onChange(e.target.value)} />
      {errorMessage ? <span>{errorMessage}</span> : null}
    </label>
  ),
}));

vi.mock("./create-source-dialog", () => ({ SOURCES_KEY: ["s"], SourceError: () => <div>source error</div> }));

import { DirectorySyncSection } from "./directory-sync";

const sources = [{ source_id: "src-1", client_id: "hr-1", display_name: "HR", status: "ACTIVE" }] as ProvisioningSourceItem[];
const sync = (over: Partial<DirectorySync> = {}): DirectorySync => ({
  sync_id: "sync-1",
  source_id: "src-1",
  provider: "AUTH0",
  display_name: "Acme Auth0",
  settings: { domain: "acme.us.auth0.com", client_id: "test-client-id-0001" },
  status: "ACTIVE",
  interval_seconds: 3600,
  next_run_at: "2026-11-05T13:00:00Z",
  last_run_at: "2026-11-05T12:00:00Z",
  last_outcome: "OK",
  last_error: null,
  last_counts: { created: 3, updated: 0, unchanged: 12, deactivated: 1, failed: 0 },
  consecutive_failures: 0,
  ...over,
});

afterEach(() => {
  cleanup();
  shared.syncs = [];
  shared.mutate.mockReset();
});

describe("DirectorySyncSection", () => {
  it("shows an empty state before anything is connected", () => {
    render(<DirectorySyncSection sources={sources} />);
    expect(screen.getByText("No directory connected")).toBeTruthy();
  });

  it("lists a connected directory with its last run in words, and no secret", () => {
    shared.syncs = [sync()];
    render(<DirectorySyncSection sources={sources} />);
    expect(screen.getByText("Acme Auth0")).toBeTruthy();
    expect(screen.getByText("acme.us.auth0.com")).toBeTruthy();
    expect(screen.getByText(/3 created, 12 unchanged, 1 deactivated/)).toBeTruthy();
    expect(screen.queryByText(/secret/i, { selector: "span" })).toBeNull();
  });

  it("explains a failed run in words instead of a code", () => {
    shared.syncs = [sync({ last_outcome: "FAILED", last_error: "CREDENTIAL_TOO_BROAD", last_counts: {} })];
    render(<DirectorySyncSection sources={sources} />);
    expect(screen.getByText(/remove every permission except read:users/)).toBeTruthy();
    expect(screen.queryByText("CREDENTIAL_TOO_BROAD")).toBeNull();
  });

  it("offers Resume (not Run now) for a paused sync, and Run now for an active one", async () => {
    shared.syncs = [sync({ status: "PAUSED" })];
    const { unmount } = render(<DirectorySyncSection sources={sources} />);
    expect(screen.getByRole("button", { name: "Resume" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Run now" })).toBeNull();
    unmount();
    shared.syncs = [sync()];
    render(<DirectorySyncSection sources={sources} />);
    await userEvent.setup().click(screen.getByRole("button", { name: "Run now" }));
    expect(shared.mutate).toHaveBeenCalledWith(expect.anything(), "sync-1");
  });

  it("connects Auth0 with the domain, client id and secret, only once they are valid", async () => {
    render(<DirectorySyncSection sources={sources} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Connect Auth0" }));
    const connect = () => screen.getAllByRole("button", { name: "Connect" })[0] as HTMLButtonElement;
    expect(connect().disabled).toBe(true);
    fireEvent.change(screen.getByLabelText(/^Auth0 domain/), { target: { value: "evil.example.com" } });
    expect(screen.getByText(/ending in .auth0.com/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText(/^Auth0 domain/), { target: { value: "Acme.US.auth0.com" } });
    fireEvent.change(screen.getByLabelText("Client ID"), { target: { value: "test-client-id-0001" } });
    fireEvent.change(screen.getByLabelText("Client secret"), { target: { value: "s3cret-value" } });
    expect(connect().disabled).toBe(false);
    await user.click(connect());
    expect(shared.mutate).toHaveBeenCalledWith(expect.anything(), {
      source_id: "src-1",
      provider: "AUTH0",
      display_name: "Auth0",
      settings: { domain: "acme.us.auth0.com", client_id: "test-client-id-0001" },
      secret: "s3cret-value",
      interval_seconds: 3600,
      approval_request_id: null,
    });
  });

  it("asks for a provisioning source first when none is active", async () => {
    render(<DirectorySyncSection sources={[]} />);
    await userEvent.setup().click(screen.getByRole("button", { name: "Connect Auth0" }));
    expect(screen.getByText("Register a provisioning source first")).toBeTruthy();
  });
});
