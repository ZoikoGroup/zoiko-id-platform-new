"use client";

import type { PrincipalItem } from "@zoiko/api-client";
import { Button, CopyId, StatusBadge, humanize } from "@zoiko/ui";
import { UserRound, X } from "lucide-react";

import { AssuranceCard } from "./assurance-card";
import { LockboxCard } from "./lockbox-card";
import { RecoveryCard } from "./recovery-card";
import { SessionsCard } from "./sessions-card";

export function PrincipalDetail({ principal, onClose }: { principal: PrincipalItem; onClose: () => void }) {
  return (
    <section aria-labelledby="principal-detail-title" className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <span className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-border bg-surface text-ink-muted">
            <UserRound aria-hidden className="size-4" />
          </span>
          <div className="min-w-0">
            <h2 id="principal-detail-title" className="text-base font-semibold text-ink">
              {humanize(principal.principal_type)} principal
            </h2>
            <div className="mt-0.5 flex flex-wrap items-center gap-2">
              <CopyId value={principal.principal_id} short={false} label="principal ID" />
              <StatusBadge state={principal.status} kind="Principal" size="xs" />
            </div>
          </div>
        </div>
        <Button size="sm" variant="ghost" icon={<X aria-hidden className="size-4" />} onPress={onClose}>
          Close
        </Button>
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <SessionsCard principal={principal} />
        <RecoveryCard principal={principal} />
      </div>
      <LockboxCard principal={principal} />
      <AssuranceCard principal={principal} />
    </section>
  );
}
