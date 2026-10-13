"use client";

import type { SessionView } from "@zoiko/api-client";
import {
  Button,
  Card,
  CopyId,
  DataTable,
  DateTime,
  Dialog,
  EmptyState,
  PageHeader,
  RelativeTime,
  StatusBadge,
  type Column,
} from "@zoiko/ui";
import { LogOut, MonitorSmartphone } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

const KEY = ["me", "sessions"];

function aalLabel(aal: SessionView["aal"]) {
  if (aal === null || aal === undefined) return "—";
  return typeof aal === "number" ? `AAL-Z${aal}` : String(aal);
}

export function SessionsPage() {
  const sessions = useCellQuery(KEY, (api) => api.me.sessions());
  const [target, setTarget] = useState<SessionView | "all" | null>(null);

  const revokeOne = useCellMutation((api, id: string) => api.me.revokeSession(id), {
    invalidate: [KEY],
    success: "Session signed out",
    onSuccess: () => setTarget(null),
  });
  const revokeAll = useCellMutation((api) => api.me.revokeAllSessions(), {
    invalidate: [KEY],
    success: (r) => `${r.revoked} session${r.revoked === 1 ? "" : "s"} signed out`,
    onSuccess: () => setTarget(null),
  });

  const columns: Column<SessionView>[] = [
    {
      key: "session",
      header: "Session",
      pinned: true,
      render: (s) => <CopyId value={s.session_id} label="session" />,
    },
    { key: "status", header: "Status", render: (s) => <StatusBadge state={s.status} kind="Session" />, sortValue: (s) => s.status },
    { key: "aal", header: "Assurance", render: (s) => <span className="font-mono text-xs">{aalLabel(s.aal)}</span> },
    { key: "auth", header: "Signed in", render: (s) => <DateTime value={s.auth_time} />, sortValue: (s) => s.auth_time ?? "" },
    { key: "seen", header: "Last active", render: (s) => <RelativeTime value={s.last_seen_at} />, sortValue: (s) => s.last_seen_at ?? "" },
    { key: "expires", header: "Expires", render: (s) => <DateTime value={s.expires_at} />, defaultHidden: true },
    {
      key: "actions",
      header: "Actions",
      align: "end",
      render: (s) =>
        s.status === "ACTIVE" || s.status === "REAUTH_REQUIRED" ? (
          <Button size="xs" variant="danger-outline" onPress={() => setTarget(s)}>
            Sign out
          </Button>
        ) : null,
    },
  ];

  const pending = revokeOne.isPending || revokeAll.isPending;
  const error = revokeOne.error ?? revokeAll.error;

  return (
    <>
      <PageHeader
        title="Sessions"
        description="Everywhere your account is signed in. Signing a session out ends it immediately, including any tokens it issued."
        actions={
          <Button variant="danger-outline" icon={<LogOut aria-hidden className="size-4" />} onPress={() => setTarget("all")}>
            Sign out everywhere
          </Button>
        }
      />
      <Card padded={false}>
        <QueryView query={sessions}>
          {(data) => (
            <DataTable
              caption="Your sessions"
              columns={columns}
              rows={data.items}
              rowKey={(s) => s.session_id}
              empty={<EmptyState icon={MonitorSmartphone} title="No sessions">There are no sessions for your account.</EmptyState>}
            />
          )}
        </QueryView>
      </Card>

      <Dialog
        isOpen={target !== null}
        onOpenChange={(o) => !o && !pending && setTarget(null)}
        title={target === "all" ? "Sign out everywhere?" : "Sign out this session?"}
        size="sm"
        role="alertdialog"
        footer={
          <>
            <Button variant="secondary" isDisabled={pending} onPress={() => setTarget(null)}>
              Cancel
            </Button>
            <Button
              variant="danger"
              pending={pending}
              onPress={() => (target === "all" ? revokeAll.mutate(undefined) : target && revokeOne.mutate(target.session_id))}
            >
              {target === "all" ? "Sign out all sessions" : "Sign out"}
            </Button>
          </>
        }
      >
        <div className="space-y-3 text-sm text-ink-muted">
          {target === "all" ? (
            <p>Every session for your account ends now, including this one. You will need your passkey to sign in again.</p>
          ) : (
            <p>
              The session ends now and its tokens stop working. If it is the session you are using, you will be signed out.
            </p>
          )}
          {error ? <ApiErrorView error={error} /> : null}
        </div>
      </Dialog>
    </>
  );
}
