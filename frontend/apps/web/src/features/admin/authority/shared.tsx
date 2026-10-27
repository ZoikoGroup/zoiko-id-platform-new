"use client";

import type { AuthorityGrant, PrincipalItem, PrincipalType } from "@zoiko/api-client";
import { Banner, Button, Select, TextField, humanize, type Option } from "@zoiko/ui";
import { AlertTriangle } from "lucide-react";
import { useState, type ReactNode } from "react";

import { useCellQuery } from "@/lib/cell";
import { useRuntime } from "@/lib/runtime";

/* ------------------------------------------------------------------------------------------------
 * Small helpers shared by the authority, mandate and decision admin pages.
 * ---------------------------------------------------------------------------------------------- */

export const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
/** Non-negative decimal string; amounts never travel as binary floats. */
export const DECIMAL_RE = /^\d+(\.\d+)?$/;
export const CURRENCY_RE = /^[A-Z]{3}$/;

export const RISK_OPTIONS: Option[] = [
  { id: "0", label: "R0 · Low", description: "Public or low-sensitivity actions." },
  { id: "1", label: "R1 · Moderate", description: "Normal workforce access and ordinary writes." },
  { id: "2", label: "R2 · High", description: "Sensitive data, external writes, privileged workflows." },
  { id: "3", label: "R3 · Critical", description: "Delegation, payments, IdP/MFA changes, bulk export." },
];

/** The signed-in principal (the grantor / approver / mandate creator). */
export function useMe(): string | undefined {
  const { session } = useRuntime();
  return session?.authenticated ? session.subject.sub : undefined;
}

/** `Date` → value for `<input type="datetime-local">` in the user's time zone. */
export function toLocalInput(d: Date): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** datetime-local value → ISO 8601 UTC, or null if empty / invalid. */
export function localToIso(v: string): string | null {
  if (!v) return null;
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

export function defaultWindow(days = 30): { from: string; to: string } {
  const now = new Date();
  return { from: toLocalInput(now), to: toLocalInput(new Date(now.getTime() + days * 86_400_000)) };
}

/** Optional non-negative integer field: "" → undefined; invalid → NaN (caller reports). */
export function intInput(v: string): number | undefined {
  const t = v.trim();
  if (!t) return undefined;
  return /^\d+$/.test(t) ? Number(t) : Number.NaN;
}

export function shortId(id: string): string {
  return id.length > 13 ? `${id.slice(0, 8)}…${id.slice(-4)}` : id;
}

/** A list of scopes where `null` means "any". */
export function ScopeList({ values, any = "Any" }: { values: string[] | null | undefined; any?: string }) {
  if (values === null || values === undefined) return <span className="text-ink-muted italic">{any}</span>;
  if (values.length === 0) return <span className="text-ink-subtle">—</span>;
  return (
    <ul className="flex flex-wrap gap-1">
      {values.map((v) => (
        <li key={v}>
          <code className="rounded bg-sunken px-1 py-px font-mono text-xs text-ink">{v}</code>
        </li>
      ))}
    </ul>
  );
}

/** Client-side validation message shown inside a form. */
export function FormProblem({ children }: { children: ReactNode }) {
  if (!children) return null;
  return (
    <Banner tone="danger" icon={AlertTriangle} title="Check the form" live="polite">
      {children}
    </Banner>
  );
}

/**
 * Keeps the Idempotency-Key stable while the same payload is retried, and rotates it as soon as
 * the payload changes (re-using a key with a different body is refused as IDEMPOTENCY_KEY_REUSED).
 */
export function useIdempotency(newKey: () => string) {
  const [state, setState] = useState<{ key: string; payload: string } | null>(null);
  return (payload: unknown): string => {
    const json = JSON.stringify(payload);
    if (state && state.payload === json) return state.key;
    const key = newKey();
    setState({ key, payload: json });
    return key;
  };
}

/* ------------------------------------------------------------------------------------------------
 * Principal picker: a list from /admin/principals, with a free-text ID fallback (the list needs an
 * admin grant; without it, or for principals outside the list, the ID is typed).
 * ---------------------------------------------------------------------------------------------- */

const MANUAL = "__manual__";

function principalOption(p: PrincipalItem): Option {
  return {
    id: p.principal_id,
    label: `${humanize(p.principal_type)} · ${shortId(p.principal_id)}`,
    description: `${p.principal_id}${p.status !== "ACTIVE" ? ` (${humanize(p.status)})` : ""}`,
  };
}

export function PrincipalField({
  label,
  value,
  onChange,
  preferTypes,
  description,
  isRequired,
  extraOptions = [],
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  /** Types listed first (e.g. AI_AGENT/SERVICE/WORKLOAD for executors). */
  preferTypes?: PrincipalType[];
  description?: ReactNode;
  isRequired?: boolean;
  extraOptions?: Option[];
}) {
  const principals = useCellQuery(["admin-picker", "principals"], (api) => api.principals.list({ limit: 200 }));
  const [manual, setManual] = useState(false);
  const items = [...(principals.data?.items ?? [])].sort((a, b) => {
    const rank = (p: PrincipalItem) => (preferTypes?.includes(p.principal_type) ? 0 : 1);
    return rank(a) - rank(b) || a.principal_type.localeCompare(b.principal_type);
  });
  // An extra option wins over the same principal in the list (the signed-in person is in both): keys must be unique.
  const extraIds = new Set(extraOptions.map((o) => o.id));
  const options = [...extraOptions, ...items.filter((p) => !extraIds.has(p.principal_id)).map(principalOption)];
  const known = options.some((o) => o.id === value);
  const useText = manual || principals.isError || (principals.isSuccess && options.length === 0) || (value !== "" && !known);

  if (useText) {
    return (
      <div className="space-y-1">
        <TextField
          label={label}
          value={value}
          onChange={(v) => onChange(v.trim())}
          isRequired={isRequired}
          mono
          placeholder="Principal ID (UUID)"
          description={
            <>
              {description}
              {principals.isError ? " The principal list isn't available to you, so enter the ID." : null}
            </>
          }
          errorMessage={value && !UUID_RE.test(value) && !extraOptions.some((o) => o.id === value) ? "Enter a principal UUID." : undefined}
        />
        {options.length > 0 ? (
          <Button
            variant="link"
            size="xs"
            onPress={() => {
              setManual(false);
              onChange("");
            }}
          >
            Choose from the principal list
          </Button>
        ) : null}
      </div>
    );
  }
  return (
    <Select
      label={label}
      isRequired={isRequired}
      value={value || null}
      placeholder={principals.isPending ? "Loading principals…" : "Select a principal…"}
      onChange={(v) => {
        if (v === MANUAL) {
          setManual(true);
          onChange("");
        } else onChange(v);
      }}
      options={[...options, { id: MANUAL, label: "Enter an ID…", description: "For a principal not in this list." }]}
      description={description}
    />
  );
}

/* ------------------------------------------------------------------------------------------------
 * Grant picker over /v1/authority-grants with a free-text fallback.
 * ---------------------------------------------------------------------------------------------- */

export function GrantPicker({
  label,
  value,
  onChange,
  granteeId,
  description,
  isRequired,
  allowNone,
  filter,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  /** Only grants held by this principal. */
  granteeId?: string;
  /** Keep only the grants that suit the task (e.g. those a mandate can be built on). */
  filter?: (g: AuthorityGrant) => boolean;
  description?: ReactNode;
  isRequired?: boolean;
  /** Adds a "None" choice (value ""). */
  allowNone?: string;
}) {
  const q = { status: "ACTIVE" as const, grantee_principal_id: granteeId, limit: 200 };
  const grants = useCellQuery(["authority", "grants", "picker", q], (api) => api.authority.grants(q));
  const [manual, setManual] = useState(false);
  const options: Option[] = (grants.data?.items ?? []).filter((g) => !filter || filter(g)).map((g) => ({
    id: g.authority_grant_id,
    label: `${shortId(g.authority_grant_id)} · ${g.actions === null ? "any action" : g.actions.slice(0, 2).join(", ") + (g.actions.length > 2 ? ` +${g.actions.length - 2}` : "")}`,
    description: `Grantee ${shortId(g.grantee_principal_id)} · for ${shortId(g.represented_principal_id)}${g.value_limit ? ` · up to ${g.value_limit.amount} ${g.value_limit.currency}` : ""}${g.valid_to ? ` · until ${new Date(g.valid_to).toLocaleDateString("en-US")}` : ""}`,
  }));
  const known = options.some((o) => o.id === value);
  const useText = manual || grants.isError || (value !== "" && !known && grants.isSuccess);

  if (useText) {
    return (
      <div className="space-y-1">
        <TextField
          label={label}
          value={value}
          onChange={(v) => onChange(v.trim())}
          isRequired={isRequired}
          mono
          placeholder="Authority grant ID (UUID)"
          description={
            <>
              {description}
              {grants.isError ? " The grant list isn't available to you, so enter the ID." : null}
            </>
          }
          errorMessage={value && !UUID_RE.test(value) ? "Enter a grant UUID." : undefined}
        />
        {options.length > 0 ? (
          <Button
            variant="link"
            size="xs"
            onPress={() => {
              setManual(false);
              onChange("");
            }}
          >
            Choose from active grants
          </Button>
        ) : null}
      </div>
    );
  }
  const NONE = "__none__";
  return (
    <Select
      label={label}
      isRequired={isRequired}
      value={value || (allowNone && !isRequired ? NONE : null)}
      placeholder={grants.isPending ? "Loading grants…" : options.length ? "Select a grant…" : "No active grants found"}
      onChange={(v) => {
        if (v === MANUAL) {
          setManual(true);
          onChange("");
        } else onChange(v === NONE ? "" : v);
      }}
      options={[
        ...(allowNone ? [{ id: NONE, label: allowNone }] : []),
        ...options,
        { id: MANUAL, label: "Enter a grant ID…", description: "For a grant not in this list." },
      ]}
      description={description}
    />
  );
}
