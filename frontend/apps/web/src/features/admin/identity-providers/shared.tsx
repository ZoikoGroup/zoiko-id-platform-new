"use client";

/** Building blocks shared by the identity-provider, verification and credential admin pages. */

import { ApiError } from "@zoiko/api-client";
import type { UseQueryResult } from "@tanstack/react-query";
import {
  Banner,
  Button,
  Checkbox,
  Dialog,
  FieldGroup,
  RiskBadge,
  SensitiveActionDialog,
  type CommitResult,
  type Reversibility,
} from "@zoiko/ui";
import { Fingerprint, Lock } from "lucide-react";
import type { FormEvent, ReactNode } from "react";

import { ApiErrorView, QueryView } from "@/lib/cell";

export const ALGORITHMS = ["ES256", "Ed25519", "RS256", "PS256"] as const;
export type Alg = (typeof ALGORITHMS)[number];

/* ------------------------------------------------------------------------------------------ errors */

export function isScopeError(e: unknown): e is ApiError {
  return e instanceof ApiError && e.code === "SCOPE_INSUFFICIENT";
}

/** SCOPE_INSUFFICIENT means the app's access token lacks an OAuth scope (not a missing grant). */
export function AdminErrorView({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  if (isScopeError(error)) {
    const scope = error.message.replace(/ is required\.$/, "");
    return (
      <Banner tone="warn" icon={Lock} title="Missing OAuth scope" live="polite">
        This app&apos;s access token does not carry the <span className="font-mono">{scope}</span> scope, so the cell refused the call
        before checking your authority. Sign out and sign in again; if it persists, the app&apos;s client registration must be granted
        that scope.
        {error.correlationId ? <span className="mt-1 block text-xs">Support reference: <span className="font-mono">{error.correlationId}</span></span> : null}
      </Banner>
    );
  }
  return <ApiErrorView error={error} onRetry={onRetry} />;
}

/** QueryView that explains SCOPE_INSUFFICIENT instead of showing a generic error. */
export function AdminQueryView<T>({ query, children }: { query: UseQueryResult<T>; children: (data: T) => ReactNode }) {
  if (query.isError && isScopeError(query.error)) return <AdminErrorView error={query.error} />;
  return <QueryView query={query}>{children}</QueryView>;
}

/** The SensitiveActionDialog renders `ErrorState`, which expects string details. */
function errorShape(e: unknown) {
  if (e instanceof ApiError) {
    const message = e.isApprovalRequired
      ? "A second administrator must approve this exact change first. Create an approval request under Authority → Approvals and have it approved."
      : e.code === "PROTECTED_ACTION_DENIED"
        ? "Your authority grants do not cover this action in this tenant."
        : e.message;
    return {
      code: e.code,
      message,
      correlationId: e.correlationId,
      details: e.details.map((d) => (d.field ? `${d.field}: ${d.issue}` : d.issue)),
    };
  }
  return { message: e instanceof Error ? e.message : "Unexpected error." };
}

/* ------------------------------------------------------------------------------------------ R3 */

export function StepUpNote({ approval }: { approval?: ReactNode }) {
  return (
    <Banner tone="info" icon={Fingerprint} title="Critical action (R3)">
      You will be asked to confirm with your passkey before the cell applies it.
      {approval ? <> {approval}</> : null}
    </Banner>
  );
}

/** Destructive or security-relevant change, reviewed through the sensitive-action flow. The passkey
 * step-up is requested automatically by the runtime when the cell answers STEP_UP_REQUIRED. */
export function R3ActionDialog({
  isOpen,
  onClose,
  title,
  intent,
  scope,
  reversibility,
  commitLabel,
  dependents,
  risk = "R3",
  run,
}: {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  intent: ReactNode;
  scope?: [string, ReactNode][];
  reversibility: Reversibility;
  commitLabel: string;
  dependents?: { label: string; effect: string }[];
  risk?: "R2" | "R3";
  run: () => Promise<{ outcome: string; objectId: string; revision?: number }>;
}) {
  async function onCommit(): Promise<CommitResult> {
    try {
      const r = await run();
      return { kind: "done", ...r };
    } catch (e) {
      return { kind: "error", error: errorShape(e) };
    }
  }
  return (
    <SensitiveActionDialog
      isOpen={isOpen}
      onOpenChange={(o) => !o && onClose()}
      title={title}
      intent={
        <div className="space-y-2">
          <div>{intent}</div>
          {risk === "R3" ? <p>When you commit, the cell asks for a fresh passkey check if your current sign-in is not recent enough.</p> : null}
        </div>
      }
      scope={scope}
      risk={risk}
      evidenceClass={risk === "R3" ? "E3" : "E2"}
      reversibility={reversibility}
      dependents={dependents}
      commitLabel={commitLabel}
      onCommit={onCommit}
    />
  );
}

/* ------------------------------------------------------------------------------------------ forms */

/** A create/edit form in a dialog. Submitting runs `onSubmit`; local validation errors and cell errors
 * are both shown above the footer. */
export function FormDialog({
  isOpen,
  onClose,
  title,
  description,
  submitLabel,
  pending,
  error,
  onSubmit,
  children,
  size = "lg",
  risk,
}: {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  description?: ReactNode;
  submitLabel: string;
  pending: boolean;
  error: unknown;
  onSubmit: () => void;
  children: ReactNode;
  size?: "sm" | "md" | "lg" | "xl";
  risk?: "R1" | "R2" | "R3";
}) {
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!pending) onSubmit();
  };
  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={(o) => !o && !pending && onClose()}
      isDismissable={!pending}
      title={title}
      description={description}
      size={size}
      footer={
        <>
          <Button variant="secondary" isDisabled={pending} onPress={onClose}>
            Cancel
          </Button>
          <Button pending={pending} onPress={() => onSubmit()}>
            {submitLabel}
          </Button>
        </>
      }
    >
      <form onSubmit={submit} noValidate className="space-y-5">
        {risk ? (
          <div className="flex items-center gap-2 text-xs text-ink-muted">
            <RiskBadge risk={risk} /> {risk === "R3" ? "Needs a fresh passkey check." : null}
          </div>
        ) : null}
        {children}
        {error ? <AdminErrorView error={error} /> : null}
        <button type="submit" hidden aria-hidden tabIndex={-1} />
      </form>
    </Dialog>
  );
}

/** Multi-select of signing algorithms, as checkboxes. */
export function AlgorithmPicker({
  legend,
  value,
  onChange,
  description,
}: {
  legend: string;
  value: Alg[];
  onChange: (v: Alg[]) => void;
  description?: ReactNode;
}) {
  return (
    <FieldGroup legend={legend} description={description}>
      <div className="flex flex-wrap gap-x-5 gap-y-2">
        {ALGORITHMS.map((a) => (
          <Checkbox
            key={a}
            isSelected={value.includes(a)}
            onChange={(on) => onChange(on ? [...value, a] : value.filter((x) => x !== a))}
          >
            <span className="font-mono">{a}</span>
          </Checkbox>
        ))}
      </div>
    </FieldGroup>
  );
}

/* ------------------------------------------------------------------------------------------ values */

/** `datetime-local` input value `days` from now, in local time. Call from event handlers or lazy
 * state initializers only (it reads the clock). */
export function localDateTimeFromNow(days: number): string {
  const d = new Date(Date.now() + days * 86_400_000);
  d.setSeconds(0, 0);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** Converts a `datetime-local` value to an ISO instant; throws a readable error when empty/invalid. */
export function isoFromLocal(v: string, field: string): string {
  const d = new Date(v);
  if (!v || Number.isNaN(d.getTime())) throw new Error(`${field} must be a valid date and time.`);
  return d.toISOString();
}

/** Required non-empty text; throws a readable error otherwise. */
export function required(v: string, field: string): string {
  const s = v.trim();
  if (!s) throw new Error(`${field} is required.`);
  return s;
}

/** Optional text: trimmed value or null. */
export function optional(v: string): string | null {
  const s = v.trim();
  return s ? s : null;
}

/** PEM certificates (any number of BEGIN/END blocks), or one base64 DER certificate per line. */
export function certificatesInput(v: string): string[] {
  const pem = v.match(/-----BEGIN CERTIFICATE-----[\s\S]+?-----END CERTIFICATE-----/g);
  if (pem) return pem.map((c) => c.trim());
  return v
    .split(/\n/)
    .map((s) => s.trim())
    .filter(Boolean);
}

export const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function uuidInput(v: string, field: string): string {
  const s = required(v, field);
  if (!UUID_RE.test(s)) throw new Error(`${field} must be a UUID.`);
  return s;
}

/** Comma-separated values for compact table cells. */
export function joined(v: string[] | null | undefined): ReactNode {
  if (!v?.length) return <span className="text-ink-subtle">—</span>;
  return <span className="text-xs">{v.join(", ")}</span>;
}
