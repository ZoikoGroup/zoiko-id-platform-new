"use client";

import type { OrgUnit, OrgUnitKind } from "@zoiko/api-client";
import { Banner, Button, Card, Dialog, EmptyState, Select, TextField } from "@zoiko/ui";
import { Network, Plus } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

const KEY = ["admin", "team", "org-units"];
const KINDS: { id: OrgUnitKind; label: string }[] = [
  { id: "GROUP", label: "Group" },
  { id: "SUBSIDIARY", label: "Subsidiary" },
  { id: "DIVISION", label: "Division" },
  { id: "DEPARTMENT", label: "Department" },
];

/** The units in tree order: each parent followed by its children, with the depth to indent by. */
export function inTreeOrder(units: OrgUnit[]): OrgUnit[] {
  const byParent = new Map<string | null, OrgUnit[]>();
  for (const u of units) byParent.set(u.parent_unit_id, [...(byParent.get(u.parent_unit_id) ?? []), u]);
  const out: OrgUnit[] = [];
  const walk = (parent: string | null) => {
    for (const u of (byParent.get(parent) ?? []).sort((a, b) => a.name.localeCompare(b.name))) {
      out.push(u);
      walk(u.unit_id);
    }
  };
  walk(null);
  return out;
}

function AddDialog({ isOpen, parent, units, onClose }: { isOpen: boolean; parent: string; units: OrgUnit[]; onClose: () => void }) {
  const [name, setName] = useState("");
  const [kind, setKind] = useState<OrgUnitKind>("SUBSIDIARY");
  const [under, setUnder] = useState(parent);
  const add = useCellMutation((api) => api.tenant.addOrgUnit({ name: name.trim(), kind, parent_unit_id: under || null }), {
    invalidate: [KEY],
    success: "Unit added",
    onSuccess: () => {
      setName("");
      onClose();
    },
  });
  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={(o) => !o && !add.isPending && onClose()}
      title="Add a unit"
      description="A group, subsidiary, division or department. A hierarchy can be six levels deep."
      footer={
        <>
          <Button variant="secondary" isDisabled={add.isPending} onPress={onClose}>
            Cancel
          </Button>
          <Button pending={add.isPending} isDisabled={name.trim().length < 2} onPress={() => add.mutate(undefined)}>
            Add unit
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <TextField label="Name" isRequired value={name} onChange={setName} placeholder="Acme UK" />
        <Select label="Kind" value={kind} onChange={(v) => setKind(v as OrgUnitKind)} options={KINDS} />
        <Select label="Under" value={under} onChange={setUnder} options={[{ id: "", label: "Top level" }, ...inTreeOrder(units).map((u) => ({ id: u.unit_id, label: `${"— ".repeat(u.depth)}${u.name}` }))]} />
        {add.error ? <ApiErrorView error={add.error} /> : null}
      </div>
    </Dialog>
  );
}

export function OrgStructureTab() {
  const q = useCellQuery(KEY, (api) => api.tenant.orgUnits());
  const [adding, setAdding] = useState<string | null>(null);
  const retire = useCellMutation((api, id: string) => api.tenant.retireOrgUnit(id), { invalidate: [KEY], success: "Unit retired" });
  return (
    <>
      <Banner tone="info" title="A record of structure">
        This records how your group is organised inside this company. It does not give anyone access by itself; roles and grants still decide that. Part of the group governance plan feature.
      </Banner>
      <Card
        title="Organization structure"
        actions={
          <Button icon={<Plus aria-hidden className="size-4" />} onPress={() => setAdding("")}>
            Add unit
          </Button>
        }
      >
        <QueryView query={q}>
          {(d) => {
            const ordered = inTreeOrder(d.items);
            return (
              <>
                {ordered.length ? (
                  <ul className="space-y-1" aria-label="Organization structure">
                    {ordered.map((u) => (
                      <li key={u.unit_id} className="flex items-center justify-between gap-3 rounded-md px-2 py-1.5 hover:bg-hover" style={{ paddingInlineStart: `${u.depth * 1.5 + 0.5}rem` }}>
                        <span className="text-sm">
                          <span className="font-medium text-ink">{u.name}</span> <span className="text-xs text-ink-subtle">{u.kind.toLowerCase()} · {u.members} {u.members === 1 ? "person" : "people"}</span>
                        </span>
                        <span className="flex gap-2">
                          {u.depth + 1 < d.max_depth ? (
                            <Button size="xs" variant="secondary" onPress={() => setAdding(u.unit_id)}>
                              Add under
                            </Button>
                          ) : null}
                          <Button size="xs" variant="secondary" isDisabled={retire.isPending} onPress={() => retire.mutate(u.unit_id)}>
                            Retire
                          </Button>
                        </span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <EmptyState icon={Network} title="No structure yet">
                    Add your group at the top, then its subsidiaries and departments.
                  </EmptyState>
                )}
                {retire.error ? <ApiErrorView error={retire.error} /> : null}
                <AddDialog key={adding ?? "closed"} isOpen={adding !== null} parent={adding ?? ""} units={d.items} onClose={() => setAdding(null)} />
              </>
            );
          }}
        </QueryView>
      </Card>
    </>
  );
}
