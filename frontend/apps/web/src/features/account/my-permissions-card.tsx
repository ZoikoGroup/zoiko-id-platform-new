"use client";

import { Badge, Card, DateTime, EmptyState, LoadingRows } from "@zoiko/ui";
import { ShieldCheck } from "lucide-react";

import { ApiErrorView, useCellQuery } from "@/lib/cell";

import { expiryText, limitText, whatItAllows } from "./my-permissions";

/** What the signed-in person has been GIVEN permission to do, read from the grants made to them. A Member holds no company-wide right, so this is the only place they
 * can see it. Only their own permissions are ever returned. A permission is not a spending limit: limits (mandates) are set from a permission and are listed below. */
export function MyPermissions() {
  const grants = useCellQuery(["my-grants"], (api) => api.authority.myGrants());

  return (
    <Card title="What you are allowed to do" description="Permissions an owner or administrator has given you. A permission lets you act; a spending limit, if any, is under it.">
      {grants.isPending && grants.fetchStatus !== "idle" ? <LoadingRows rows={2} /> : null}
      {grants.isError ? <ApiErrorView error={grants.error} /> : null}
      {grants.data ? (
        grants.data.items.length === 0 ? (
          <EmptyState icon={ShieldCheck} title="No extra permissions">
            You can use your own account: sign in, manage your passkeys and sessions, and check your own identity. If you need to do more, ask an owner or administrator.
          </EmptyState>
        ) : (
          <ul className="space-y-3" aria-label="My permissions">
            {grants.data.items.map((g) => {
              const limit = limitText(g);
              return (
                <li key={g.authority_grant_id} className="rounded-card border border-border p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone="ok">Active</Badge>
                    <span className="text-sm text-ink-muted">{expiryText(g.valid_to)}</span>
                    {g.valid_to ? (
                      <span className="text-xs text-ink-subtle">
                        (<DateTime value={g.valid_to} />)
                      </span>
                    ) : null}
                  </div>
                  <ul className="mt-2 flex flex-wrap gap-2" aria-label="Allowed actions">
                    {whatItAllows(g).map((w) => (
                      <li key={w}>
                        <Badge tone="info">{w}</Badge>
                      </li>
                    ))}
                  </ul>
                  {limit ? <p className="mt-2 text-sm text-ink-muted">{limit}</p> : null}
                  {g.delegation_allowed ? <p className="mt-1 text-xs text-ink-muted">You may pass part of this on to someone or something else.</p> : null}
                </li>
              );
            })}
          </ul>
        )
      ) : null}
    </Card>
  );
}
