"use client";

import type { Incident, IncidentRole, IncidentSeverity } from "@zoiko/api-client";
import {
  Badge,
  Banner,
  Button,
  Card,
  Checkbox,
  CopyId,
  DataTable,
  DateTime,
  Dialog,
  EmptyState,
  KeyValues,
  PageHeader,
  Select,
  StatusBadge,
  Switch,
  TextField,
  type Column,
} from "@zoiko/ui";
import { Plus, Siren } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, listInput, useCellMutation, useCellQuery } from "@/lib/op";

const KEY = ["ops", "incidents"];
const SEVERITIES: { id: IncidentSeverity; label: string; description: string }[] = [
  { id: "SEV0", label: "SEV0", description: "Catastrophic security or integrity event (cross-tenant, key compromise, overspend)." },
  { id: "SEV1", label: "SEV1", description: "Major outage or security event on T0/T1 or an R3 control path." },
  { id: "SEV2", label: "SEV2", description: "Significant degradation with a workaround or limited scope." },
  { id: "SEV3", label: "SEV3", description: "Minor issue, limited customer impact." },
  { id: "SEV4", label: "SEV4", description: "Defect with no current customer impact." },
];
const ROLES: { id: IncidentRole; label: string }[] = [
  { id: "INCIDENT_COMMANDER", label: "Incident commander" },
  { id: "OPERATIONS_LEAD", label: "Operations lead" },
  { id: "COMMUNICATIONS_LEAD", label: "Communications lead" },
  { id: "SCRIBE", label: "Scribe" },
  { id: "SECURITY_LEAD", label: "Security lead" },
];
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function sevTone(s: IncidentSeverity) {
  return s === "SEV0" || s === "SEV1" ? "danger" : s === "SEV2" ? "warn" : "neutral";
}

function Declare({ onClose, onDone }: { onClose: () => void; onDone: (i: Incident) => void }) {
  const services = useCellQuery(["ops", "services"], (api) => api.ops.services());
  const [title, setTitle] = useState("");
  const [severity, setSeverity] = useState<IncidentSeverity>("SEV2");
  const [security, setSecurity] = useState(false);
  const [chosen, setChosen] = useState<string[]>([]);
  const [cells, setCells] = useState("");
  const declare = useCellMutation(
    (api) => api.ops.declareIncident({ title: title.trim(), severity, security, services: chosen, cells: listInput(cells) }),
    { invalidate: [KEY], success: "Incident declared", onSuccess: onDone },
  );
  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && !declare.isPending && onClose()}
      title="Declare an incident"
      description="Any active operator may declare at once; there is no approval step. You become incident commander unless you assign someone."
      size="lg"
      footer={
        <>
          <Button variant="secondary" onPress={onClose}>
            Cancel
          </Button>
          <Button variant="danger" pending={declare.isPending} isDisabled={title.trim().length < 4} onPress={() => declare.mutate(undefined)}>
            Declare
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <TextField label="What is happening" isRequired value={title} onChange={setTitle} description="No customer data: ids and symptoms only." />
        <Select label="Severity" value={severity} onChange={(v) => setSeverity(v as IncidentSeverity)} options={SEVERITIES} />
        <Switch isSelected={security || severity === "SEV0"} isDisabled={severity === "SEV0"} onChange={setSecurity}>
          Security incident (needs a Security Lead; preserves forensic evidence)
        </Switch>
        <fieldset className="space-y-2">
          <legend className="text-sm font-medium text-ink">Affected services</legend>
          <div className="grid gap-2 sm:grid-cols-2">
            {(services.data?.services ?? []).map((s) => (
              <Checkbox
                key={s.service_id}
                isSelected={chosen.includes(s.service_id)}
                onChange={() => setChosen((l) => (l.includes(s.service_id) ? l.filter((x) => x !== s.service_id) : [...l, s.service_id]))}
              >
                {s.name}
              </Checkbox>
            ))}
          </div>
        </fieldset>
        <TextField label="Affected cells" mono value={cells} onChange={setCells} placeholder="uk-1, eu-1" />
        {declare.error ? <ApiErrorView error={declare.error} title="Not declared" /> : null}
      </div>
    </Dialog>
  );
}

function IncidentDetail({ id }: { id: string }) {
  const key = [...KEY, id];
  const q = useCellQuery(key, (api) => api.ops.incident(id), { refetchInterval: 15_000 });
  const refresh = [KEY, key];
  const [severity, setSeverity] = useState<IncidentSeverity | "">("");
  const [role, setRole] = useState<IncidentRole>("SECURITY_LEAD");
  const [person, setPerson] = useState("");
  const [why, setWhy] = useState("");
  const [note, setNote] = useState("");
  const [kind, setKind] = useState<"ACTION" | "DECISION" | "COMMUNICATION">("ACTION");
  const [validation, setValidation] = useState("");
  const [item, setItem] = useState({ title: "", owner: "", due: "" });
  const [pir, setPir] = useState("");
  const update = useCellMutation(
    (api, body: Parameters<typeof api.ops.updateIncident>[1]) => api.ops.updateIncident(id, body),
    { invalidate: refresh, success: "Incident updated", onSuccess: () => setWhy("") },
  );
  const entry = useCellMutation((api) => api.ops.addEntry(id, { kind, note: note.trim() }), { invalidate: [key], success: "Added to timeline", onSuccess: () => setNote("") });
  const resolve = useCellMutation((api) => api.ops.resolve(id, validation.trim()), { invalidate: refresh, success: "Incident resolved" });
  const addItem = useCellMutation(
    (api) => api.ops.addActionItem(id, { title: item.title.trim(), owner: item.owner.trim() || null, due: item.due || null }),
    { invalidate: [key], success: "Action item added", onSuccess: () => setItem({ title: "", owner: "", due: "" }) },
  );
  const verify = useCellMutation((api, itemId: string) => api.ops.verifyActionItem(id, itemId), { invalidate: [key], success: "Verified" });
  const close = useCellMutation((api) => api.ops.close(id, pir.trim() || null), { invalidate: refresh, success: "Incident closed" });
  return (
    <QueryView query={q}>
      {(i) => {
        const open = i.status !== "RESOLVED" && i.status !== "CLOSED";
        return (
          <div className="space-y-4">
            <KeyValues
              columns={3}
              items={[
                ["Severity", <Badge key="s" tone={sevTone(i.severity)}>{i.severity}</Badge>],
                ["Status", <StatusBadge key="t" state={i.status} kind="Incident" size="xs" />],
                ["Security", i.security ? "Yes" : "No"],
                ["Services", i.services.join(", ") || "—"],
                ["Cells", i.cells.join(", ") || "—"],
                ["Declared", <DateTime key="d" value={i.declared_at} />],
              ]}
            />
            <Card title="Roles">
              <ul className="space-y-1 text-sm">
                {ROLES.map((r) => (
                  <li key={r.id} className="flex items-center justify-between">
                    <span className="text-ink-muted">{r.label}</span>
                    {i.roles[r.id] ? <CopyId value={i.roles[r.id] ?? ""} label="principal" /> : <span className="text-ink-subtle">unassigned</span>}
                  </li>
                ))}
              </ul>
            </Card>
            {open ? (
              <Card title="Update">
                <div className="space-y-3">
                  <TextField label="Reason (recorded in the timeline)" value={why} onChange={setWhy} />
                  <div className="flex flex-wrap items-end gap-2">
                    <Select label="Severity" value={severity || i.severity} onChange={(v) => setSeverity(v as IncidentSeverity)} options={SEVERITIES.map((s) => ({ id: s.id, label: s.label }))} />
                    <Button size="sm" variant="secondary" isDisabled={why.trim().length < 4 || !severity || severity === i.severity} pending={update.isPending} onPress={() => severity && update.mutate({ severity, reason: why.trim() })}>
                      Change severity
                    </Button>
                    {i.status !== "RECOVERY_VALIDATION" ? (
                      <Button size="sm" variant="secondary" isDisabled={why.trim().length < 4} onPress={() => update.mutate({ status: i.status === "DECLARED" ? "MITIGATING" : "RECOVERY_VALIDATION", reason: why.trim() })}>
                        {i.status === "DECLARED" ? "Start mitigating" : "Restored — validate"}
                      </Button>
                    ) : (
                      <Button size="sm" variant="secondary" isDisabled={why.trim().length < 4} onPress={() => update.mutate({ status: "MITIGATING", reason: why.trim() })}>
                        Regressed — back to mitigation
                      </Button>
                    )}
                  </div>
                  <div className="flex flex-wrap items-end gap-2">
                    <Select label="Role" value={role} onChange={(v) => setRole(v as IncidentRole)} options={ROLES} />
                    <TextField label="Person (principal ID)" mono value={person} onChange={setPerson} />
                    <Button size="sm" variant="secondary" isDisabled={!UUID.test(person.trim()) || why.trim().length < 4} onPress={() => update.mutate({ roles: { [role]: person.trim() }, reason: why.trim() })}>
                      Assign
                    </Button>
                  </div>
                  {update.error ? <ApiErrorView error={update.error} /> : null}
                  <div className="flex flex-wrap items-end gap-2 border-t border-border pt-3">
                    <Select
                      label="Add to timeline"
                      value={kind}
                      onChange={(v) => setKind(v as typeof kind)}
                      options={[
                        { id: "ACTION", label: "Action" },
                        { id: "DECISION", label: "Decision" },
                        { id: "COMMUNICATION", label: "Communication" },
                      ]}
                    />
                    <TextField label="Note" value={note} onChange={setNote} className="min-w-72 flex-1" />
                    <Button size="sm" isDisabled={note.trim().length < 2} pending={entry.isPending} onPress={() => entry.mutate(undefined)}>
                      Add
                    </Button>
                  </div>
                  <div className="space-y-2 border-t border-border pt-3">
                    <TextField label="Validation (how service and controls were proven healthy)" multiline rows={2} value={validation} onChange={setValidation} />
                    <Button size="sm" isDisabled={validation.trim().length < 10} pending={resolve.isPending} onPress={() => resolve.mutate(undefined)}>
                      Resolve
                    </Button>
                    {resolve.error ? <ApiErrorView error={resolve.error} /> : null}
                  </div>
                </div>
              </Card>
            ) : null}
            {i.status === "RESOLVED" ? (
              <Card title="Post-incident review" description={i.severity === "SEV0" || i.severity === "SEV1" ? "SEV0/SEV1: every action item needs an owner, a due date and independent verification." : "Optional for this severity."}>
                <div className="space-y-3">
                  <ul className="divide-y divide-border">
                    {(i.action_items ?? []).map((a) => (
                      <li key={a.item_id} className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm">
                        <span className="text-ink">{a.title}</span>
                        <span className="text-xs text-ink-muted">due {a.due ?? "—"}</span>
                        {a.verified_by ? (
                          <Badge tone="ok">verified</Badge>
                        ) : (
                          <Button size="xs" variant="secondary" pending={verify.isPending} onPress={() => verify.mutate(a.item_id)}>
                            Verify closed
                          </Button>
                        )}
                      </li>
                    ))}
                  </ul>
                  {verify.error ? <ApiErrorView error={verify.error} /> : null}
                  <div className="grid gap-2 sm:grid-cols-[1fr_14rem_9rem_auto] sm:items-end">
                    <TextField label="Action item" value={item.title} onChange={(v) => setItem((s) => ({ ...s, title: v }))} />
                    <TextField label="Owner (principal ID)" mono value={item.owner} onChange={(v) => setItem((s) => ({ ...s, owner: v }))} />
                    <TextField label="Due" type="date" value={item.due} onChange={(v) => setItem((s) => ({ ...s, due: v }))} />
                    <Button size="sm" variant="secondary" isDisabled={item.title.trim().length < 4} pending={addItem.isPending} onPress={() => addItem.mutate(undefined)}>
                      Add
                    </Button>
                  </div>
                  <div className="flex flex-wrap items-end gap-2 border-t border-border pt-3">
                    <TextField label="PIR reference" mono value={pir} onChange={setPir} placeholder="PIR-2026-17" />
                    <Button size="sm" pending={close.isPending} onPress={() => close.mutate(undefined)}>
                      Close incident
                    </Button>
                  </div>
                  {close.error ? <ApiErrorView error={close.error} /> : null}
                </div>
              </Card>
            ) : null}
            {i.status === "CLOSED" ? <Banner tone="ok" title="Closed">PIR {i.pir_ref ?? "not required"}.</Banner> : null}
            <Card title="Timeline" description="Append-only and timestamped.">
              <ol className="space-y-2">
                {(i.timeline ?? []).map((e) => (
                  <li key={e.seq} className="text-sm">
                    <div className="flex flex-wrap items-center gap-2">
                      <DateTime value={e.at} />
                      <Badge tone="neutral">{e.kind.toLowerCase()}</Badge>
                    </div>
                    <div className="text-ink">{e.note}</div>
                  </li>
                ))}
              </ol>
            </Card>
          </div>
        );
      }}
    </QueryView>
  );
}

/** Incident lifecycle (SRE-001 sections 15-17, 26). */
export function IncidentsPage() {
  const [openOnly, setOpenOnly] = useState(true);
  const list = useCellQuery([...KEY, openOnly], (api) => api.ops.incidents(openOnly), { refetchInterval: 15_000 });
  const [declaring, setDeclaring] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const columns: Column<Incident>[] = [
    { key: "sev", header: "Severity", render: (i) => <Badge tone={sevTone(i.severity)}>{i.severity}</Badge>, sortValue: (i) => i.severity },
    { key: "title", header: "Incident", pinned: true, render: (i) => <span className="font-medium text-ink">{i.title}</span> },
    { key: "status", header: "Status", render: (i) => <StatusBadge state={i.status} kind="Incident" size="xs" /> },
    { key: "services", header: "Services", render: (i) => i.services.join(", ") || "—" },
    { key: "at", header: "Declared", render: (i) => <DateTime value={i.declared_at} />, sortValue: (i) => i.declared_at },
    {
      key: "a",
      header: "",
      align: "end",
      render: (i) => (
        <Button size="xs" variant="secondary" onPress={() => setOpen(i.incident_id)}>
          Open
        </Button>
      ),
    },
  ];
  return (
    <>
      <PageHeader
        icon={Siren}
        title="Incidents"
        description="Declare, run and close incidents. Every change is timestamped; SEV0/SEV1 close only with a verified post-incident review."
        actions={
          <Button variant="danger" icon={<Plus aria-hidden className="size-4" />} onPress={() => setDeclaring(true)}>
            Declare incident
          </Button>
        }
      />
      <Card
        padded={false}
        actions={
          <Switch isSelected={openOnly} onChange={setOpenOnly}>
            Open only
          </Switch>
        }
      >
        <QueryView query={list}>
          {(r) =>
            r.incidents.length ? (
              <DataTable caption="Incidents" columns={columns} rows={r.incidents} rowKey={(i) => i.incident_id} />
            ) : (
              <EmptyState title={openOnly ? "No open incidents" : "No incidents"} icon={Siren} />
            )
          }
        </QueryView>
      </Card>
      {declaring ? (
        <Declare
          onClose={() => setDeclaring(false)}
          onDone={(i) => {
            setDeclaring(false);
            setOpen(i.incident_id);
          }}
        />
      ) : null}
      {open ? (
        <Dialog isOpen onOpenChange={(o) => !o && setOpen(null)} title="Incident" size="lg" footer={<Button onPress={() => setOpen(null)}>Close</Button>}>
          <IncidentDetail id={open} />
        </Dialog>
      ) : null}
    </>
  );
}
