"use client";

import type { UseQueryResult } from "@tanstack/react-query";
import { AccessUnavailable, EmptyState, LoadingRows } from "@zoiko/ui";
import { Ban, EyeOff } from "lucide-react";
import type { ReactNode } from "react";

import { ApiErrorView } from "@/lib/cell";

import { classify } from "./logic";

/** The product line ships switched off. The cell then refuses every call that starts or changes a case, and that must read as "not available". */
export function NotAvailable() {
  return (
    <EmptyState icon={Ban} title="Not available">
      Source of funds checks are not switched on for this environment. Nothing was asked, decided or stored. The owners switch the service on after they record its
      legal basis.
    </EmptyState>
  );
}

/** A role that may not read the answers sees that they are withheld, not that there are none. */
export function Redacted() {
  return (
    <EmptyState icon={EyeOff} title="Withheld for your role">
      Your role does not include reading source of funds answers. This is not an empty result: the answers exist and are hidden from you.
    </EmptyState>
  );
}

/** Loading, then the right state for every refusal. */
export function SofQuery<T>({
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
    if (kind === "forbidden") return <>{forbidden ?? <Redacted />}</>;
    if (kind === "missing") return <AccessUnavailable correlationId={(query.error as { correlationId?: string }).correlationId} />;
    return <ApiErrorView error={query.error} onRetry={() => void query.refetch()} />;
  }
  if (query.data === undefined) return null;
  return <>{children(query.data)}</>;
}

export function WriteError({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const kind = classify(error);
  if (kind === "off") return <NotAvailable />;
  return <ApiErrorView error={error} title={kind === "forbidden" ? "Not permitted" : undefined} onRetry={onRetry} />;
}
