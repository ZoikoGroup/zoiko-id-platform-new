"use client";

import type { UseQueryResult } from "@tanstack/react-query";
import { AccessUnavailable, EmptyState, LoadingRows } from "@zoiko/ui";
import { Ban, EyeOff, Globe2 } from "lucide-react";
import type { ReactNode } from "react";

import { ApiErrorView } from "@/lib/cell";

import { classify } from "./logic";

/** The feature is off by default. The cell then answers 404 for every route, and that must read as "not available", never as an empty list. */
export function NotAvailable() {
  return (
    <EmptyState icon={Ban} title="Not available">
      The Access Readiness agent is not turned on for this environment. Nothing was run and nothing has changed. An administrator can ask Zoiko to turn it on.
    </EmptyState>
  );
}

export function NotAvailableInRegion() {
  return (
    <EmptyState icon={Globe2} title="Not available in this region">
      This workspace&apos;s region is not approved for the Access Readiness agent, so it fails closed. Nothing was run and nothing has changed.
    </EmptyState>
  );
}

/** A role that may not see a result sees that it is withheld, not zero findings. */
export function Redacted({ what = "This result" }: { what?: string }) {
  return (
    <EmptyState icon={EyeOff} title="Redacted for your role">
      {what} is withheld because your role does not include it. This is not an empty result: counts, findings and evidence are hidden, not zero.
    </EmptyState>
  );
}

/** Loading, then the right state for every refusal. `forbidden` lets a screen draw its own redacted version. */
export function ReadinessQuery<T>({
  query,
  children,
  forbidden,
  loadingRows = 4,
}: {
  query: UseQueryResult<T>;
  children: (data: T) => ReactNode;
  forbidden?: ReactNode;
  loadingRows?: number;
}) {
  if (query.isPending && query.fetchStatus !== "idle") return <LoadingRows rows={loadingRows} />;
  if (query.isError) {
    const kind = classify(query.error);
    if (kind === "off") return <NotAvailable />;
    if (kind === "region") return <NotAvailableInRegion />;
    if (kind === "forbidden") return <>{forbidden ?? <Redacted />}</>;
    if (kind === "missing") return <AccessUnavailable correlationId={(query.error as { correlationId?: string }).correlationId} />;
    return <ApiErrorView error={query.error} onRetry={() => void query.refetch()} />;
  }
  if (query.data === undefined) return null;
  return <>{children(query.data)}</>;
}

/** An error from a write, with the same distinctions. */
export function WriteError({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const kind = classify(error);
  if (kind === "off") return <NotAvailable />;
  if (kind === "region") return <NotAvailableInRegion />;
  return <ApiErrorView error={error} title={kind === "forbidden" ? "Not permitted" : undefined} onRetry={onRetry} />;
}
