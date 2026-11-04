"use client";

import { ApiError, type CellApi } from "@zoiko/api-client";
import { useMutation, useQuery, useQueryClient, type QueryKey, type UseQueryResult } from "@tanstack/react-query";
import { AccessUnavailable, Banner, Button, Code, ErrorState, LoadingRows, TextField, useToast } from "@zoiko/ui";
import { Search, ShieldOff, Stamp } from "lucide-react";
import { useState, type FormEvent, type ReactNode } from "react";

import { useRuntime } from "./runtime";

/** Read from the cell. `fn` receives the typed API; `enabled` gates lookups until an id is entered.
 * Protected reads (R1+ admin lists) can need a fresh passkey check: those go through the same
 * step-up prompt as writes and are retried once. */
export function useCellQuery<T>(key: QueryKey, fn: (api: CellApi) => Promise<T>, opts: { enabled?: boolean; refetchInterval?: number } = {}) {
  const { api, protect } = useRuntime();
  return useQuery({ queryKey: key, queryFn: () => protect(() => fn(api)), ...opts });
}

/** Write to the cell through the step-up interceptor; invalidates `invalidate` keys on success. */
export function useCellMutation<V, T>(
  fn: (api: CellApi, vars: V) => Promise<T>,
  opts: { invalidate?: QueryKey[]; success?: string | ((r: T) => string); onSuccess?: (r: T, v: V) => void } = {},
) {
  const { api, protect } = useRuntime();
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (vars: V) => protect(() => fn(api, vars)),
    onSuccess: async (r, v) => {
      for (const k of opts.invalidate ?? []) await qc.invalidateQueries({ queryKey: k });
      if (opts.success) toast.notify({ tone: "ok", title: typeof opts.success === "function" ? opts.success(r) : opts.success });
      opts.onSuccess?.(r, v);
    },
  });
}

const CODE_HELP: Record<string, string> = {
  SHADOW_EXIT_CRITERIA_FAILED: "Your application has not sent enough access questions yet. The Shadow step shows the progress and what to do next.",
  PEP_CONTEXT_INCOMPLETE: "Some questions were missing details your application said it would always send. Add them to each question.",
  PROTECTED_ACTION_DENIED: "Your authority grants do not cover this action in this tenant.",
  APPROVAL_REQUIRED: "A second administrator must approve this exact change first.",
  STEP_UP_REQUIRED: "This action needs a fresh, high-assurance passkey check.",
  BILLING_PERMISSION_REQUIRED: "Your account does not have billing access for this. Ask an Owner of the organization to give it to you.",
  BILLING_DELEGATION_OWNER_ONLY: "Only an Owner gives or takes away billing access.",
  BILLING_GRANT_EXISTS: "That person already has this billing access with a different end date. Take it away first to change it.",
  BILLING_SELF_GRANT: "Nobody gives billing access to themselves. Ask another Owner.",
  BILLING_GRANTEE_NOT_MEMBER: "Billing access goes to an active member of the organization only.",
  BILLING_EXPIRY_PAST: "The end date is already past.",
  STATE_UNAVAILABLE: "The security state service is unavailable, so the platform refused to decide (fail closed).",
  ACTION_UNKNOWN: "This action is not in the platform's action catalogue.",
  IDEMPOTENCY_KEY_REUSED: "This request was already submitted with different details. Start again.",
  WRITER_EPOCH_STALE: "The ledger writer for this cell changed. Retry in a moment.",
  CSRF_REJECTED: "The request could not be verified. Reload the page and try again.",
  VALIDATION_FAILED: "Some fields are not valid.",
  MANDATE_SOURCE_INVALID: "The authority this limit was built on has been withdrawn or has changed, so it cannot be switched on. Create a new one from a current grant.",
  GRANT_SCOPE_WIDENING: "A limit cannot give more than the grant it comes from. Lower the amount, shorten the dates or remove an action.",
  GRANT_DELEGATION_NOT_ALLOWED: "The grant behind this limit does not allow it to be passed on.",
  GRANT_DEPTH_EXCEEDED: "This limit has already been passed on as far as the grant allows.",
  MANDATE_EXECUTOR_MISMATCH: "Only the person or agent this limit was given to can use it.",
  MANDATE_SCOPE_EXCEEDED: "This action is outside what the limit allows.",
  MANDATE_TOOL_NOT_ALLOWED: "This tool is outside what the limit allows.",
  MANDATE_BUDGET_INVALID: "The amounts do not fit together. A single payment limit cannot be higher than the total.",
  MANDATE_CURRENCY_MISMATCH: "Use one currency for every amount in the limit.",
  MANDATE_INVALID_TRANSITION: "That change is not possible from the limit's current state.",
  CONCURRENT_MODIFICATION: "Someone changed this at the same moment. Reload and try again.",
  RESERVATION_EXPIRED: "The amount that was held has timed out. Ask for it again.",
  AUTHORITY_REVOKED_DURING_EXECUTION: "The authority behind this was withdrawn while it was being used, so it was stopped.",
  SNAPSHOT_CHANGED: "This request changed after you opened it. Read it again before you decide: an earlier look never counts for a changed request.",
  NOT_AN_APPROVER: "You are not the Owner or someone the Owner gave authority for this amount.",
  OVER_SPEND_LIMIT: "This amount is above the limit the Owner gave you. The Owner or a person with a higher limit has to approve it.",
  SELF_APPROVAL: "The person who asked for this does not approve it.",
  OWNER_ONLY: "Only an Owner gives or takes away a spend limit or a procurement role.",
  SELF_DELEGATION: "Nobody gives themselves a limit. Ask another Owner.",
  REVIEW_REQUIRED: "These terms are not standard. Zoiko's contract, legal and finance review has to be recorded before any approval counts.",
  ALREADY_SIGNED: "You have already signed this request.",
  AUTHORITY_ALREADY_GIVEN: "That signature is already given. Above the top amount a second, different person has to sign.",
  NOT_PENDING: "This request is no longer waiting for a decision.",
  ORDER_NOT_SIGNED: "Only a signed order that is not yet active needs this approval.",
  ALREADY_DELEGATED: "That person already holds this. Take it away first to change it.",
  VERSION_CONFLICT: "The profile changed since you opened it. Reload it and make your change again.",
  PAYER_CHANGE_REQUIRES_REVIEW: "A different country changes the legal payer or tax country. That is a reviewed change, not an edit here: contact your account manager.",
  ACCOUNT_REQUIRED: "This organization has no commercial account yet, so there is nothing to bill. Contact your account manager.",
  NO_CHANGE: "Nothing changed.",
  ADDRESS_INVALID: "An address needs a first line, a city and a two-letter country code.",
  TAX_ID_INVALID: "Each tax identifier needs a known type (once) and 4 to 40 letters, digits or . - /.",
  CONTACT_INVALID: "An invoice contact is required, and every address must be an email address.",
  POLICY_NOT_SIGNED: "The finance policy has no authority until the Founder and the CFO sign it.",
  FX_UNPINNED: "The policy pins no exchange rate for this currency, so its size in USD cannot be worked out.",
};

/** Uniform rendering of any cell error. Never shows stacks or raw bodies. */
export function ApiErrorView({ error, title, onRetry }: { error: unknown; title?: string; onRetry?: () => void }) {
  if (error instanceof ApiError) {
    if (error.isApprovalRequired) {
      return (
        <Banner tone="info" icon={Stamp} title="Approval required" live="polite">
          {CODE_HELP.APPROVAL_REQUIRED} Create an approval request under <strong>Authority → Approvals</strong>, have it approved, then
          resubmit with its approval request ID.
          {error.correlationId ? <span className="mt-1 block text-xs">Support reference: <Code>{error.correlationId}</Code></span> : null}
        </Banner>
      );
    }
    if (error.code === "PROTECTED_ACTION_DENIED") {
      return (
        <Banner tone="warn" icon={ShieldOff} title="Not permitted" live="polite">
          {CODE_HELP.PROTECTED_ACTION_DENIED}
          {error.correlationId ? <span className="mt-1 block text-xs">Support reference: <Code>{error.correlationId}</Code></span> : null}
        </Banner>
      );
    }
    const help = CODE_HELP[error.code];
    return (
      <ErrorState
        title={title ?? "The request was not completed"}
        // A dismissed passkey check can be retried: the retry asks for the passkey again.
        onRetry={error.retryable || error.isStepUpRequired ? onRetry : undefined}
        error={{
          code: error.code,
          // A bare code (the cell sometimes sends only the code as the message) reads as the plain sentence too.
          message: help && (error.message.endsWith("failed.") || /^[A-Z][A-Z_]+$/.test(error.message)) ? help : error.message,
          correlationId: error.correlationId,
          details: error.details.map((d) => (d.field ? `${d.field}: ${d.issue}` : d.issue)),
        }}
      />
    );
  }
  return <ErrorState title={title} error={{ message: error instanceof Error ? error.message : "Unexpected error." }} onRetry={onRetry} />;
}

/** Loading / error / not-found handling for a query; renders children with data. */
export function QueryView<T>({
  query,
  children,
  loadingRows = 4,
  notFound,
}: {
  query: UseQueryResult<T>;
  children: (data: T) => ReactNode;
  loadingRows?: number;
  notFound?: ReactNode;
}) {
  if (query.isPending && query.fetchStatus !== "idle") return <LoadingRows rows={loadingRows} />;
  if (query.isError) {
    const e = query.error;
    if (e instanceof ApiError && e.isNotFound) return <>{notFound ?? <AccessUnavailable correlationId={e.correlationId} />}</>;
    return <ApiErrorView error={e} onRetry={() => void query.refetch()} />;
  }
  if (query.data === undefined) return null;
  return <>{children(query.data)}</>;
}

/** Find an object by its immutable ID — for entities the cell can read but does not list. */
export function LookupForm({
  label,
  placeholder,
  initial = "",
  onLookup,
  description,
}: {
  label: string;
  placeholder?: string;
  initial?: string;
  onLookup: (id: string) => void;
  description?: ReactNode;
}) {
  const [value, setValue] = useState(initial);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const v = value.trim();
    if (v) onLookup(v);
  };
  return (
    <form onSubmit={submit} className="flex flex-wrap items-end gap-2" role="search">
      <TextField label={label} value={value} onChange={setValue} placeholder={placeholder} mono className="min-w-64 flex-1" description={description} />
      <Button type="submit" variant="secondary" icon={<Search aria-hidden className="size-4" />} className={description ? "mb-5" : undefined}>
        Look up
      </Button>
    </form>
  );
}

export function newIdempotencyKey(): string {
  return crypto.randomUUID();
}

/** Splits a comma/newline separated field into trimmed values. */
export function listInput(v: string): string[] {
  return v
    .split(/[\n,]/)
    .map((s) => s.trim())
    .filter(Boolean);
}

/** Parses an optional JSON object field; returns undefined when empty, throws a readable error if invalid. */
export function jsonInput(v: string, field: string): Record<string, unknown> | undefined {
  if (!v.trim()) return undefined;
  try {
    const parsed = JSON.parse(v) as unknown;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error();
    return parsed as Record<string, unknown>;
  } catch {
    throw new Error(`${field} must be a JSON object.`);
  }
}
