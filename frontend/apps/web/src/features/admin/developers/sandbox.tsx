"use client";

import type { SandboxIdentity } from "@zoiko/api-client";
import { Banner, Button, Card, CopyId, DataTable, DateTime, Dialog, EmptyState, Select, StatusBadge, TextField, type Column } from "@zoiko/ui";
import { FlaskConical, Plus } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { LIFETIMES, cleanPrefix, room } from "./sandbox-logic";
import { UserText } from "@/lib/user-text";

const KEY = ["admin", "developers", "sandbox-identities"];

function CreateDialog({ isOpen, onClose, space }: { isOpen: boolean; onClose: () => void; space: number }) {
  const [prefix, setPrefix] = useState("test");
  const [count, setCount] = useState("5");
  const [days, setDays] = useState("14");
  const n = Number(count);
  const valid = Number.isInteger(n) && n >= 1 && n <= space;
  const create = useCellMutation((api) => api.integration.createSandboxIdentities({ prefix: cleanPrefix(prefix), count: n, days: Number(days) }), {
    invalidate: [KEY],
    success: (r) => `${r.items.length} test identit${r.items.length === 1 ? "y" : "ies"} created`,
    onSuccess: () => onClose(),
  });
  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={(o) => !o && !create.isPending && onClose()}
      title="New test identities"
      description="Labelled people you can name in a decision request or give a grant, without a real person."
      footer={
        <>
          <Button variant="secondary" isDisabled={create.isPending} onPress={onClose}>
            Cancel
          </Button>
          <Button pending={create.isPending} isDisabled={!valid} onPress={() => create.mutate(undefined)}>
            Create
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <TextField label="Label prefix" value={prefix} onChange={setPrefix} description={`Labels look like ${cleanPrefix(prefix)}-01, ${cleanPrefix(prefix)}-02.`} />
        <TextField
          label="How many"
          value={count}
          onChange={setCount}
          errorMessage={valid ? undefined : `Between 1 and ${space}.`}
          description={`You can add up to ${space} more.`}
        />
        <Select label="Lasts" value={days} onChange={setDays} options={LIFETIMES} />
        {create.error ? <ApiErrorView error={create.error} /> : null}
      </div>
    </Dialog>
  );
}

export function SandboxSection() {
  const list = useCellQuery(KEY, (api) => api.integration.sandboxIdentities());
  const [creating, setCreating] = useState(false);
  const retire = useCellMutation((api, id: string) => api.integration.retireSandboxIdentity(id), { invalidate: [KEY], success: "Test identity retired" });

  const columns: Column<SandboxIdentity>[] = [
    { key: "label", header: "Label", pinned: true, render: (i) => <span className="font-medium"><UserText>{i.label}</UserText></span>, sortValue: (i) => i.label },
    { key: "id", header: "Principal ID", render: (i) => <CopyId value={i.principal_id} label="principal ID" /> },
    { key: "status", header: "Status", render: (i) => <StatusBadge state={i.status} kind="Test identity" /> },
    { key: "expires", header: "Expires", render: (i) => <DateTime value={i.expires_at} />, sortValue: (i) => i.expires_at },
    {
      key: "actions",
      header: "Actions",
      align: "end",
      render: (i) =>
        i.status === "LIVE" ? (
          <Button size="xs" variant="secondary" isDisabled={retire.isPending} onPress={() => retire.mutate(i.principal_id)}>
            Retire
          </Button>
        ) : null,
    },
  ];

  return (
    <QueryView query={list}>
      {(data) => {
        const live = data.items.filter((i) => i.status === "LIVE").length;
        const space = room(live, data.max_live);
        return (
          <>
            <Card
              title="Test identities"
              description={`${live} of ${data.max_live} live. They never sign in and are not counted as users; use a principal ID as the subject of a decision request.`}
              actions={
                <Button icon={<Plus aria-hidden className="size-4" />} isDisabled={space === 0} onPress={() => setCreating(true)}>
                  New test identities
                </Button>
              }
              padded={false}
            >
              <DataTable
                caption="Test identities"
                columns={columns}
                rows={data.items}
                rowKey={(i) => i.principal_id}
                empty={
                  <EmptyState icon={FlaskConical} title="No test identities yet">
                    Create a few to try the decision API and grants end to end before real people are connected.
                  </EmptyState>
                }
              />
              {retire.error ? (
                <div className="p-4">
                  <ApiErrorView error={retire.error} />
                </div>
              ) : null}
            </Card>
            <Banner tone="info" title="Expired identities retire on their own">
              When a test identity expires it is disabled, so a decision for it is refused like one for any disabled person.
            </Banner>
            <CreateDialog isOpen={creating} onClose={() => setCreating(false)} space={Math.max(space, 1)} />
          </>
        );
      }}
    </QueryView>
  );
}
