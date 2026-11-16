"use client";

import type { Person } from "@zoiko/api-client";
import { CopyId } from "@zoiko/ui";

import { useCellQuery } from "@/lib/cell";
import { UserText } from "@/lib/user-text";

/** A person as colleagues read them: "Name (M-0007)", with the account id kept one click away for support and audit. */
export function PersonLabel({ person, fallbackId }: { person?: Person | null; fallbackId?: string }) {
  if (!person) return fallbackId ? <CopyId value={fallbackId} label="principal ID" /> : <span className="text-sm text-ink-subtle">Unknown</span>;
  return (
    <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-0.5">
      <span className="text-sm font-medium text-ink"><UserText>{person.label}</UserText></span>
      <CopyId value={person.principal_id} label="principal ID" />
    </span>
  );
}

/** Look up the people of this company by account id, so lists that only carry ids can still say "Name (M-0007)". An id not found (or a list this person may not read) falls back to the id. */
export function usePeople(): (id: string) => Person | undefined {
  const members = useCellQuery(["tenant", "members", "people-lookup"], (api) => api.tenant.members());
  const byId = new Map((members.data?.items ?? []).map((m) => [m.principal_id, m.person]));
  return (id) => byId.get(id);
}
