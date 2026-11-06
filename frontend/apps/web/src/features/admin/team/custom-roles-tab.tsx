"use client";

import type { CustomRole } from "@zoiko/api-client";
import { Banner, Button, Card, DataTable, Dialog, EmptyState, Select, StatusBadge, TextField, type Column } from "@zoiko/ui";
import { Plus, ShieldCheck } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";
import { PersonLabel } from "./person-label";

const KEY = ["admin", "team", "custom-roles"];

export function toggle(list: string[], id: string): string[] {
  return list.includes(id) ? list.filter((x) => x !== id) : [...list, id];
}

function CreateDialog({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [base, setBase] = useState<"OPERATOR" | "VIEWER">("OPERATOR");
  const [kind, setKind] = useState<"NARROW" | "EXTEND">("NARROW");
  const [picked, setPicked] = useState<string[]>([]);
  const actions = useCellQuery(["admin", "team", "custom-role-actions", base, kind], (api) => api.tenant.customRoleActions(base, kind));
  const create = useCellMutation((api) => api.tenant.createCustomRole({ name: name.trim(), description: description.trim(), base_role: base, kind, actions: picked }), {
    invalidate: [KEY],
    success: "Custom role created",
    onSuccess: () => close(),
  });
  function close() {
    setName("");
    setDescription("");
    setPicked([]);
    create.reset();
    onClose();
  }
  const valid = name.trim().length >= 2 && picked.length > 0;
  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={(o) => !o && !create.isPending && close()}
      title="New custom role"
      description="Keep part of what Operator or Viewer can do, or add reads and requests they lack. Approvals and administration can never be added."
      size="lg"
      footer={
        <>
          <Button variant="secondary" isDisabled={create.isPending} onPress={close}>
            Cancel
          </Button>
          <Button pending={create.isPending} isDisabled={!valid} onPress={() => create.mutate(undefined)}>
            Create role
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <TextField label="Name" isRequired value={name} onChange={setName} placeholder="Profile reader" />
        <TextField label="Description" value={description} onChange={setDescription} />
        <Select
          label="What it does"
          value={kind}
          onChange={(v) => {
            setKind(v as "NARROW" | "EXTEND");
            setPicked([]);
          }}
          options={[
            { id: "NARROW", label: "Narrow", description: "Keeps the person to part of their role." },
            { id: "EXTEND", label: "Extend", description: "Adds reads and requests the role lacks. Requests still need an independent approval." },
          ]}
        />
        <Select
          label="Built on"
          value={base}
          onChange={(v) => {
            setBase(v as "OPERATOR" | "VIEWER");
            setPicked([]);
          }}
          options={[
            { id: "OPERATOR", label: "Operator" },
            { id: "VIEWER", label: "Viewer" },
          ]}
        />
        <QueryView query={actions}>
          {(data) => (
            <fieldset className="max-h-64 space-y-1 overflow-auto rounded-md border border-border p-3">
              <legend className="px-1 text-xs text-ink-subtle">{kind === "EXTEND" ? "What it adds" : "What it keeps"} ({picked.length} of {data.actions.length})</legend>
              {data.actions.map((a) => (
                <label key={a.action_id} className="flex items-start gap-2 text-sm text-ink">
                  <input type="checkbox" className="mt-1" checked={picked.includes(a.action_id)} onChange={() => setPicked((p) => toggle(p, a.action_id))} />
                  <span>{a.feature}</span>
                </label>
              ))}
            </fieldset>
          )}
        </QueryView>
        {create.error ? <ApiErrorView error={create.error} /> : null}
      </div>
    </Dialog>
  );
}

function MembersDialog({ role, onClose }: { role: CustomRole | null; onClose: () => void }) {
  const [person, setPerson] = useState("");
  const id = role?.role_id ?? "";
  const members = useCellQuery(["admin", "team", "custom-role-members", id], (api) => api.tenant.customRoleMembers(id), { enabled: !!role });
  const people = useCellQuery(["admin", "team", "members-for-roles"], (api) => api.tenant.members(), { enabled: !!role });
  const refresh = [["admin", "team", "custom-role-members", id], KEY];
  const give = useCellMutation((api) => api.tenant.giveCustomRole(id, person.trim()), { invalidate: refresh, success: "Role given", onSuccess: () => setPerson("") });
  const take = useCellMutation((api, p: string) => api.tenant.takeCustomRole(id, p), { invalidate: refresh, success: "Role taken away" });
  const eligible = (people.data?.items ?? []).filter((m) => m.status === "ACTIVE" && m.role === role?.base_role && !(members.data?.items ?? []).some((x) => x.principal_id === m.principal_id));
  return (
    <Dialog isOpen={!!role} onOpenChange={(o) => !o && onClose()} title={role ? `Who holds “${role.name}”` : ""} description={`Only people who hold the ${role?.base_role.toLowerCase()} role can be given it.`} footer={<Button variant="secondary" onPress={onClose}>Close</Button>}>
      <div className="space-y-4">
        <div className="flex items-end gap-2">
          <div className="flex-1">
            <Select label="Give it to" value={person} onChange={setPerson} options={[{ id: "", label: eligible.length ? "Choose a person" : "No one eligible" }, ...eligible.map((m) => ({ id: m.principal_id, label: m.person?.label ?? m.principal_id }))]} />
          </div>
          <Button pending={give.isPending} isDisabled={!person} onPress={() => give.mutate(undefined)}>
            Give
          </Button>
        </div>
        {give.error ? <ApiErrorView error={give.error} /> : null}
        <QueryView query={members}>
          {(d) =>
            d.items.length ? (
              <ul className="space-y-2">
                {d.items.map((m) => (
                  <li key={m.principal_id} className="flex items-center justify-between gap-2">
                    <PersonLabel person={(people.data?.items ?? []).find((x) => x.principal_id === m.principal_id)?.person} fallbackId={m.principal_id} />
                    <Button size="xs" variant="secondary" isDisabled={take.isPending} onPress={() => take.mutate(m.principal_id)}>
                      Take away
                    </Button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-ink-muted">No one holds this role yet.</p>
            )
          }
        </QueryView>
      </div>
    </Dialog>
  );
}

export function CustomRolesTab() {
  const list = useCellQuery(KEY, (api) => api.tenant.customRoles());
  const [creating, setCreating] = useState(false);
  const [managing, setManaging] = useState<CustomRole | null>(null);
  const retire = useCellMutation((api, id: string) => api.tenant.retireCustomRole(id), { invalidate: [KEY], success: "Role retired" });

  const columns: Column<CustomRole>[] = [
    { key: "name", header: "Role", pinned: true, render: (r) => <span className="font-medium">{r.name}</span>, sortValue: (r) => r.name },
    { key: "base", header: "Built on", render: (r) => `${r.base_role.charAt(0)}${r.base_role.slice(1).toLowerCase()} · ${r.kind === "EXTEND" ? "adds" : "narrows"}` },
    { key: "actions", header: "Actions", align: "end", render: (r) => `${r.actions.length} ${r.kind === "EXTEND" ? "added" : "kept"}`, sortValue: (r) => r.actions.length },
    { key: "members", header: "People", align: "end", render: (r) => r.members ?? 0 },
    { key: "status", header: "Status", render: (r) => <StatusBadge state={r.status} kind="Custom role" /> },
    {
      key: "do",
      header: "Actions",
      align: "end",
      render: (r) =>
        r.status === "ACTIVE" ? (
          <span className="flex justify-end gap-2">
            <Button size="xs" variant="secondary" onPress={() => setManaging(r)}>
              People
            </Button>
            <Button size="xs" variant="secondary" isDisabled={retire.isPending} onPress={() => retire.mutate(r.role_id)}>
              Retire
            </Button>
          </span>
        ) : null,
    },
  ];

  return (
    <>
      <Banner tone="info" title="Custom roles never grant approval or administration">
        A person with a custom role is still an Operator or Viewer first. A narrowing role keeps them to the actions you chose; an extending role adds reads and requests the role lacks, and requests still need an independent approval. Administrators and approvers cannot be given one.
      </Banner>
      <Card
        title="Custom roles"
        description="Your own names for part of what Operator or Viewer can do. Part of the custom organization roles plan feature."
        actions={
          <Button icon={<Plus aria-hidden className="size-4" />} onPress={() => setCreating(true)}>
            New custom role
          </Button>
        }
        padded={false}
      >
        <QueryView query={list}>
          {(data) => (
            <DataTable
              caption="Custom roles"
              columns={columns}
              rows={data.items}
              rowKey={(r) => r.role_id}
              empty={
                <EmptyState icon={ShieldCheck} title="No custom roles yet">
                  Create one when Operator or Viewer allows more than a team needs.
                </EmptyState>
              }
            />
          )}
        </QueryView>
        {retire.error ? (
          <div className="p-4">
            <ApiErrorView error={retire.error} />
          </div>
        ) : null}
      </Card>
      <CreateDialog isOpen={creating} onClose={() => setCreating(false)} />
      <MembersDialog role={managing} onClose={() => setManaging(null)} />
    </>
  );
}
