"use client";

import type { GovDelegate, GovSlot } from "@zoiko/api-client";
import { Banner, Button, Card, DataTable, EmptyState, PageHeader, Select } from "@zoiko/ui";
import { UserCheck } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { shortId, useMe } from "../../authority/shared";
import { PersonSelect } from "../pickers";
import { GOV_KEY, govKey, slotLabel } from "./logic";
import { GovernanceNav } from "./shell";

// From the approval matrix (machine/domain/governance.py required_slots and the restore rule).
const SLOT_HELP: Record<GovSlot, string> = {
  SECURITY: "Signs publishing a policy, wider scope, a restore after an emergency stop, turning AI on, and caps above the company baseline.",
  AUTHORITY: "Signs publishing a policy, wider scope and a restore after an emergency stop.",
  PRIVACY: "Signs wider scope that changes the data class, and turning AI on.",
  FINANCE: "Signs budget and quota increases, and turning AI on.",
};

/** Who may sign agent-governance requests in each slot (CR-091 AG2-D02). Nobody gives a slot to themselves; the server checks it too. */
export function SignersPage() {
  const me = useMe();
  const delegates = useCellQuery(govKey("delegates"), (api) => api.agentGovernance.delegates());
  const [person, setPerson] = useState("");
  const [slot, setSlot] = useState<GovSlot | "">("");
  const grant = useCellMutation<void, { created: boolean }>(
    (api) => api.agentGovernance.grantSlot({ principal_id: person, slot: slot as GovSlot }),
    {
      invalidate: [GOV_KEY],
      success: (r) => (r.created ? `${slotLabel(slot)} slot given` : "That person already holds this slot"),
      onSuccess: () => setPerson(""),
    },
  );
  const revoke = useCellMutation<GovDelegate, { revoked: boolean }>(
    (api, d) => api.agentGovernance.revokeSlot({ principal_id: d.principal_id, slot: d.slot }),
    { invalidate: [GOV_KEY], success: () => "Slot taken back" },
  );
  const self = Boolean(person) && person === me;

  return (
    <>
      <PageHeader
        icon={UserCheck}
        title="Approval signers"
        description="Who may sign agent changes as Security, Authority, Privacy or Finance. A request waits until every slot it needs is signed."
        breadcrumbs={[{ label: "Agents & tools", href: "/admin/agents" }, { label: "Governance", href: "/admin/agents/governance" }, { label: "Signers" }]}
      />
      <GovernanceNav current="signers" />
      <QueryView query={delegates}>
        {(d) => (
          <div className="space-y-4">
            <Card title="Give a slot" description="The person must be an active member. Nobody can give a slot to themselves.">
              <form
                className="grid grid-cols-1 gap-3 md:grid-cols-3 md:items-end"
                onSubmit={(e) => {
                  e.preventDefault();
                  grant.mutate();
                }}
              >
                <PersonSelect label="Person" value={person} onChange={setPerson} isRequired />
                <Select
                  label="Slot"
                  isRequired
                  value={slot || null}
                  placeholder="Choose a slot…"
                  onChange={(v) => setSlot(v as GovSlot)}
                  options={d.slots.map((s) => ({ id: s, label: slotLabel(s), description: SLOT_HELP[s] }))}
                  description={slot ? SLOT_HELP[slot] : undefined}
                />
                <Button type="submit" isDisabled={!person || !slot || self} pending={grant.isPending}>
                  Give slot
                </Button>
              </form>
              {self ? (
                <Banner tone="warn" title="Choose someone else">
                  Nobody gives an approval slot to themselves. Ask another administrator to give it to you.
                </Banner>
              ) : null}
              {grant.error ? <ApiErrorView error={grant.error} /> : null}
            </Card>
            <Card padded={false} title={`${d.items.length} current ${d.items.length === 1 ? "signer" : "signers"}`}>
              <DataTable
                caption="Approval signers"
                rows={d.items}
                rowKey={(r) => `${r.slot}:${r.principal_id}`}
                empty={
                  <EmptyState icon={UserCheck} title="No signers yet">
                    Until someone holds a slot, requests that need it stay pending.
                  </EmptyState>
                }
                columns={[
                  { key: "slot", header: "Slot", pinned: true, render: (r) => slotLabel(r.slot) },
                  { key: "who", header: "Person", render: (r) => <span className="font-mono text-xs">{shortId(r.principal_id)}{r.principal_id === me ? " (you)" : ""}</span> },
                  { key: "by", header: "Given by", render: (r) => <span className="font-mono text-xs">{shortId(r.granted_by)}</span> },
                  { key: "at", header: "Since (UTC)", render: (r) => r.granted_at.replace("T", " ").slice(0, 16) },
                  {
                    key: "act",
                    header: "",
                    render: (r) => (
                      <Button
                        size="sm"
                        variant="secondary"
                        pending={revoke.isPending && revoke.variables === r}
                        onPress={() => revoke.mutate(r)}
                      >
                        Take back
                      </Button>
                    ),
                  },
                ]}
              />
              {revoke.error ? <div className="p-4"><ApiErrorView error={revoke.error} /></div> : null}
            </Card>
          </div>
        )}
      </QueryView>
    </>
  );
}
