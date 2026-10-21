"use client";

import type { PrincipalItem } from "@zoiko/api-client";
import { Button, Card, Dialog, RiskBadge } from "@zoiko/ui";
import { LogOut } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, useCellMutation } from "@/lib/cell";

export function SessionsCard({ principal }: { principal: PrincipalItem }) {
  const [confirming, setConfirming] = useState(false);
  const revoke = useCellMutation((api, id: string) => api.sessionsAdmin.revokeAllForPrincipal(id), {
    success: (r) => `${r.revoked} session${r.revoked === 1 ? "" : "s"} signed out`,
    onSuccess: () => setConfirming(false),
  });
  const close = () => {
    if (revoke.isPending) return;
    setConfirming(false);
    revoke.reset();
  };

  return (
    <Card title="Sessions" description="End every session this principal holds, for example after a lost device or suspected takeover.">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-ink-muted">
          Tokens issued to those sessions stop working at once. The principal can sign in again with a valid passkey.
        </p>
        <Button variant="danger-outline" icon={<LogOut aria-hidden className="size-4" />} onPress={() => setConfirming(true)}>
          Revoke all sessions
        </Button>
      </div>

      <Dialog
        isOpen={confirming}
        onOpenChange={(o) => !o && close()}
        title="Revoke all sessions?"
        size="sm"
        role="alertdialog"
        footer={
          <>
            <Button variant="secondary" isDisabled={revoke.isPending} onPress={close}>
              Cancel
            </Button>
            <Button variant="danger" pending={revoke.isPending} onPress={() => revoke.mutate(principal.principal_id)}>
              Revoke all sessions
            </Button>
          </>
        }
      >
        <div className="space-y-3 text-sm text-ink-muted">
          <RiskBadge risk="R2" />
          <p>
            Every session of <code className="font-mono text-xs">{principal.principal_id}</code> ends now. This is recorded as a protected
            administrator action.
          </p>
          {revoke.error ? <ApiErrorView error={revoke.error} /> : null}
        </div>
      </Dialog>
    </Card>
  );
}
