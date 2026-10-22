"use client";

import { ApiError, type AuthenticatorView } from "@zoiko/api-client";
import { useQueryClient } from "@tanstack/react-query";
import {
  Badge,
  Banner,
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
  TextField,
  humanize,
  useToast,
  type Column,
} from "@zoiko/ui";
import { Fingerprint, Plus } from "lucide-react";
import { useState, useSyncExternalStore } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";
import { useRuntime } from "@/lib/runtime";
import { browserSupportsWebAuthn, createPasskey, passkeyErrorMessage } from "@/lib/webauthn";

const KEY = ["me", "authenticators"];
/** Authenticator types the cell counts as strong factors (never removable when last). */
const STRONG = new Set(["PASSKEY", "SECURITY_KEY"]);
const noSubscribe = () => () => {};

function isActiveStrong(a: AuthenticatorView) {
  return a.status === "ACTIVE" && STRONG.has(a.authenticator_type);
}

/** Registration failures that mean the cell cookie session is gone on this device. */
function addErrorMessage(e: unknown): string {
  if (e instanceof ApiError && (e.code === "SESSION_REQUIRED" || e.status === 401)) {
    return "Your sign-in on this device has expired. Sign out, sign in again with a passkey, then add the new one.";
  }
  return passkeyErrorMessage(e);
}

export function PasskeysPage() {
  const { flow } = useRuntime();
  const qc = useQueryClient();
  const toast = useToast();
  const authenticators = useCellQuery(KEY, (api) => api.me.authenticators());
  const supported = useSyncExternalStore(noSubscribe, browserSupportsWebAuthn, () => true);

  const [adding, setAdding] = useState(false);
  const [label, setLabel] = useState("");
  const [addPending, setAddPending] = useState(false);
  const [addFailure, setAddFailure] = useState<string | null>(null);
  const [target, setTarget] = useState<AuthenticatorView | null>(null);

  const revoke = useCellMutation((api, id: string) => api.me.revokeAuthenticator(id), {
    invalidate: [KEY],
    success: "Passkey removed",
    onSuccess: () => setTarget(null),
  });

  function closeAdd() {
    if (addPending) return;
    setAdding(false);
    setLabel("");
    setAddFailure(null);
  }

  async function addPasskey() {
    setAddPending(true);
    setAddFailure(null);
    try {
      // The cell's own cookie session (set at passkey sign-in) authorizes this ceremony.
      const options = await flow.registrationOptions({});
      const credential = await createPasskey(options);
      await flow.registrationVerify(credential, label.trim() || undefined);
      await qc.invalidateQueries({ queryKey: KEY });
      toast.notify({ tone: "ok", title: "Passkey added" });
      setAdding(false);
      setLabel("");
    } catch (e) {
      setAddFailure(addErrorMessage(e));
    } finally {
      setAddPending(false);
    }
  }

  const activeStrong = authenticators.data?.items.filter(isActiveStrong).length ?? 0;

  const columns: Column<AuthenticatorView>[] = [
    {
      key: "name",
      header: "Passkey",
      pinned: true,
      render: (a) => (
        <span className="flex flex-col">
          <span className="font-medium text-ink">{a.label || humanize(a.authenticator_type)}</span>
          <CopyId value={a.authenticator_id} label="authenticator" />
        </span>
      ),
      sortValue: (a) => a.label ?? a.authenticator_type,
    },
    { key: "type", header: "Type", render: (a) => humanize(a.authenticator_type), sortValue: (a) => a.authenticator_type },
    {
      key: "sync",
      header: "Storage",
      render: (a) =>
        a.backup_eligible ? (
          <Badge tone="info" label="Synced passkey: backed up by your password manager or platform account">
            Synced
          </Badge>
        ) : (
          <Badge tone="neutral" label="Device-bound passkey: stays on one device or security key">
            Device-bound
          </Badge>
        ),
    },
    { key: "status", header: "Status", render: (a) => <StatusBadge state={a.status} kind="Passkey" />, sortValue: (a) => a.status },
    { key: "added", header: "Added", render: (a) => <DateTime value={a.registered_at} />, sortValue: (a) => a.registered_at ?? "" },
    { key: "used", header: "Last used", render: (a) => <RelativeTime value={a.last_used_at} />, sortValue: (a) => a.last_used_at ?? "" },
    {
      key: "actions",
      header: "Actions",
      align: "end",
      render: (a) => {
        if (a.status !== "ACTIVE" && a.status !== "SUSPENDED") return null;
        const last = isActiveStrong(a) && activeStrong <= 1;
        return (
          <span className="inline-flex flex-col items-end gap-1">
            <Button
              size="xs"
              variant="danger-outline"
              isDisabled={last}
              aria-describedby={last ? "last-passkey-note" : undefined}
              onPress={() => setTarget(a)}
            >
              Remove
            </Button>
            {last ? <span className="text-2xs text-ink-subtle">Your only passkey</span> : null}
          </span>
        );
      },
    },
  ];

  return (
    <>
      <PageHeader
        title="Passkeys"
        description="Passkeys sign you in with your device's screen lock or a security key. They can't be phished or reused on other sites."
        actions={
          <Button icon={<Plus aria-hidden className="size-4" />} isDisabled={!supported} onPress={() => setAdding(true)}>
            Add a passkey
          </Button>
        }
      />

      {!supported ? (
        <Banner tone="warn" title="This browser can't create passkeys" className="mb-4">
          Use a current version of Chrome, Edge, Safari or Firefox to add a passkey.
        </Banner>
      ) : null}

      <Card
        padded={false}
        footer={
          <p id="last-passkey-note" className="text-xs text-ink-muted">
            You can&apos;t remove your only active passkey: you would have no way to sign in. Add another one first, or use account
            recovery if it is lost. Removing a passkey signs you out everywhere and needs a fresh passkey check.
          </p>
        }
      >
        <QueryView query={authenticators}>
          {(data) => (
            <DataTable
              caption="Your passkeys"
              columns={columns}
              rows={data.items}
              rowKey={(a) => a.authenticator_id}
              empty={<EmptyState icon={Fingerprint} title="No passkeys">Add a passkey to sign in without a password.</EmptyState>}
            />
          )}
        </QueryView>
      </Card>

      <Dialog
        isOpen={adding}
        onOpenChange={(o) => !o && closeAdd()}
        title="Add a passkey"
        description="Create a passkey on this device, or insert a security key."
        size="sm"
        footer={
          <>
            <Button variant="secondary" isDisabled={addPending} onPress={closeAdd}>
              Cancel
            </Button>
            <Button pending={addPending} icon={<Fingerprint aria-hidden className="size-4" />} onPress={() => void addPasskey()}>
              Create passkey
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <TextField
            label="Name (optional)"
            value={label}
            onChange={setLabel}
            placeholder="e.g. Work laptop"
            description="Helps you tell your passkeys apart. Up to 64 characters."
            isDisabled={addPending}
          />
          {addFailure ? (
            <Banner tone="danger" live="assertive" title="Couldn't add the passkey">
              {addFailure}
            </Banner>
          ) : null}
        </div>
      </Dialog>

      <Dialog
        isOpen={target !== null}
        onOpenChange={(o) => !o && !revoke.isPending && setTarget(null)}
        title="Remove this passkey?"
        size="sm"
        role="alertdialog"
        footer={
          <>
            <Button variant="secondary" isDisabled={revoke.isPending} onPress={() => setTarget(null)}>
              Cancel
            </Button>
            <Button variant="danger" pending={revoke.isPending} onPress={() => target && revoke.mutate(target.authenticator_id)}>
              Remove passkey
            </Button>
          </>
        }
      >
        <div className="space-y-3 text-sm text-ink-muted">
          <p>
            <strong className="text-ink">{target?.label || (target ? humanize(target.authenticator_type) : "")}</strong> will stop
            working immediately. You may be asked to confirm with a passkey first.
          </p>
          <p>For your security, every session for your account then ends, including this one, and you sign in again.</p>
          {revoke.error ? <ApiErrorView error={revoke.error} /> : null}
        </div>
      </Dialog>
    </>
  );
}
