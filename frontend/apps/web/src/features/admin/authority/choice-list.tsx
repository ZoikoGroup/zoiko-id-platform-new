"use client";

import { Checkbox, TextField } from "@zoiko/ui";

import { listInput } from "@/lib/cell";

export type Choice = { id: string; label: string };
export type ChoiceGroup = { id: string; label: string; description?: string; choices: Choice[] };

/** What a company can most often hand out. Each is a name the platform already knows, so nothing is typed from memory.
 * These four are the spending actions: the mandate dialog uses exactly this list (a mandate narrows a spending grant), so it is
 * kept as it is and the grant dialog builds on it below. */
export const ACTION_CHOICES: Choice[] = [
  { id: "payment.release", label: "Release a payment" },
  { id: "mandate.create", label: "Give someone a spending limit (mandate)" },
  { id: "purchase.create", label: "Create a purchase" },
  { id: "invoice.approve", label: "Approve an invoice" },
];

export const RESOURCE_CHOICES: Choice[] = [
  { id: "payment:*", label: "Any payment" },
  { id: "mandate:*", label: "Any spending limit (mandate)" },
  { id: "purchase:*", label: "Any purchase" },
  { id: "invoice:*", label: "Any invoice" },
];

/**
 * Everything the grant dialog can hand out, by feature area, so a person ticks areas instead of typing action names. Each name is one
 * the cell's routes check today (`action=` and `resource=` in the API modules), and each area's resources are the types those routes
 * name, so a grant that ticks an area covers what that area's screens do.
 *
 * Not listed on purpose: company administration (members, owners, residency, identity providers). Those come with the Owner and Admin
 * roles and are not delegated one by one; anything else can still be typed under "Something else".
 */
export const ACTION_GROUPS: ChoiceGroup[] = [
  {
    id: "payments",
    label: "Payments and invoices",
    description: "Move or commit company money.",
    choices: [
      { id: "payment.release", label: "Release a payment" },
      { id: "purchase.create", label: "Create a purchase" },
      { id: "invoice.approve", label: "Approve an invoice" },
    ],
  },
  {
    id: "mandates",
    label: "Spending limits (mandates)",
    description: "Hand part of a grant to an agent or service with a budget.",
    choices: [
      { id: "mandate.create", label: "Give someone a spending limit (mandate)" },
      { id: "mandate.manage", label: "Activate, pause or revoke mandates" },
      { id: "mandate.read", label: "See mandates and balances" },
    ],
  },
  {
    id: "agents",
    label: "Agents and tools",
    description: "Register AI agents, the blueprints they are built from and the tools they may use.",
    choices: [
      { id: "machine.agent.read", label: "See agents, blueprints and tools" },
      { id: "machine.agent.manage", label: "Register agents and attest their checks" },
      { id: "machine.agent.activate", label: "Activate an agent" },
      { id: "machine.blueprint.manage", label: "Publish, approve or revoke blueprint releases" },
      { id: "machine.build.register", label: "Register a software build (what a build pipeline holds)" },
      { id: "machine.build.manage", label: "Approve or revoke software builds" },
      { id: "machine.tool.manage", label: "Register, activate or suspend tools" },
    ],
  },
  {
    id: "workloads",
    label: "Workload identity",
    description: "Bind a running workload's identity to an agent and quarantine an environment.",
    choices: [{ id: "machine.workload.manage", label: "Register, revoke or quarantine workload bindings" }],
  },
  {
    id: "evidence",
    label: "Evidence and audit",
    description: "Read, verify and export the tamper-evident record.",
    choices: [
      { id: "evidence.read", label: "Read receipts and audit records" },
      { id: "evidence.verify", label: "Verify receipts and the ledger" },
      { id: "evidence.annotate", label: "Annotate a receipt" },
      { id: "evidence.export", label: "Export a signed evidence package" },
    ],
  },
  {
    id: "credentials",
    label: "Credentials",
    description: "Issue and manage verifiable credentials.",
    choices: [
      { id: "credential.read", label: "See credentials and issuers" },
      { id: "credential.issue", label: "Issue a credential" },
      { id: "credential.status.change", label: "Suspend, revoke or reinstate a credential" },
    ],
  },
  {
    id: "privacy",
    label: "Privacy and data rights",
    description: "Handle data-subject requests, retention and the record of processing.",
    choices: [
      { id: "privacy.read", label: "See privacy records, purposes and retention" },
      { id: "privacy.rights.manage", label: "Handle data-subject requests" },
      { id: "privacy.rights.fulfill", label: "Fulfil a data-subject request" },
      { id: "privacy.retention.manage", label: "Change retention rules" },
      { id: "privacy.processing_activity.manage", label: "Record processing activities and purposes" },
      { id: "privacy.role_assignment.manage", label: "Assign controller or processor roles" },
    ],
  },
  {
    id: "verification",
    label: "Verification and screening",
    description: "Run identity and company checks and their reviews.",
    choices: [
      { id: "verification.profile.manage", label: "Publish verification profiles" },
      { id: "verification.adapter.manage", label: "Manage verification provider trust" },
      { id: "verification.review.decide", label: "Decide a manual verification review" },
      { id: "verification.screening.review", label: "Review a screening case" },
      { id: "verification.screening.read_restricted", label: "Read restricted screening detail" },
      { id: "verification.assertion.revoke", label: "Revoke a verification assertion" },
      { id: "verification.idv.review", label: "Review an identity proofing case on an alternative route" },
      { id: "organization.kyb.review", label: "Review a company (KYB) case" },
      { id: "organization.kyb.escalation.decide", label: "Decide a company case the reviewers disagreed on" },
      { id: "organization.kyb.assess", label: "Assess a company's verification grade again" },
      { id: "organization.kyb.appeal.open", label: "Open an appeal against a company decision" },
      { id: "organization.kyb.appeal.decide", label: "Decide an appeal against a company decision" },
      { id: "organization.kyb.export", label: "Export the signed company verification summary" },
      { id: "organization.kyb.mandate.manage", label: "Record, countersign or revoke a representative's mandate" },
    ],
  },
];

export const RESOURCE_GROUPS: ChoiceGroup[] = [
  {
    id: "payments",
    label: "Payments and invoices",
    choices: [
      { id: "payment:*", label: "Any payment" },
      { id: "purchase:*", label: "Any purchase" },
      { id: "invoice:*", label: "Any invoice" },
    ],
  },
  { id: "mandates", label: "Spending limits (mandates)", choices: [{ id: "mandate:*", label: "Any spending limit (mandate)" }] },
  {
    id: "agents",
    label: "Agents and tools",
    choices: [
      { id: "agent:*", label: "Any agent" },
      { id: "blueprint:*", label: "Any blueprint release" },
      { id: "build:*", label: "Any software build" },
      { id: "tool:*", label: "Any tool" },
    ],
  },
  {
    id: "workloads",
    label: "Workload identity",
    choices: [
      { id: "workload_binding:*", label: "Any workload binding" },
      { id: "workload_environment:*", label: "Any workload environment" },
    ],
  },
  {
    id: "evidence",
    label: "Evidence and audit",
    choices: [
      { id: "decision_receipt:*", label: "Any decision receipt" },
      { id: "evidence_case:*", label: "Any evidence case" },
      { id: "evidence_export:*", label: "Any evidence export" },
    ],
  },
  {
    id: "credentials",
    label: "Credentials",
    choices: [
      { id: "credential_issuer:*", label: "Any credential issuer" },
      { id: "credential_configuration:*", label: "Any credential type" },
    ],
  },
  {
    id: "privacy",
    label: "Privacy and data rights",
    choices: [
      { id: "rights_request:*", label: "Any data-subject request" },
      { id: "data_map:*", label: "Any data map" },
      { id: "restriction:*", label: "Any processing restriction" },
      { id: "processing_activity:*", label: "Any processing activity" },
      { id: "purpose:*", label: "Any purpose" },
      { id: "retention_policy:*", label: "Any retention policy" },
      { id: "processor:*", label: "Any processor" },
      { id: "privacy_notice:*", label: "Any privacy notice" },
      { id: "processing_role_assignment:*", label: "Any controller or processor role" },
    ],
  },
  {
    id: "verification",
    label: "Verification and screening",
    choices: [
      { id: "verification_profile:*", label: "Any verification profile" },
      { id: "verification_adapter:*", label: "Any verification provider" },
      { id: "verification_review_case:*", label: "Any manual review case" },
      { id: "screening_case:*", label: "Any screening case" },
      { id: "verification_assertion:*", label: "Any verification assertion" },
      { id: "kyb_case:*", label: "Any company (KYB) case" },
    ],
  },
];

const idsOf = (groups: ChoiceGroup[], area: string) => groups.find((g) => g.id === area)?.choices.map((c) => c.id) ?? [];
const withIds = (list: string[], ids: string[], on: boolean) => (on ? [...new Set([...list, ...ids])] : list.filter((x) => !ids.includes(x)));

/**
 * The quick way to cover a feature: tick an area and both its actions and its resources are ticked below (and cleared again when
 * unticked). The two lists stay editable for anything finer. Entries typed under "Something else" are never touched.
 */
export function AreaPicker({
  actions,
  resources,
  onActions,
  onResources,
}: {
  actions: string;
  resources: string;
  onActions: (v: string) => void;
  onResources: (v: string) => void;
}) {
  const a = listInput(actions);
  const r = listInput(resources);
  const count = (area: string) => {
    const ai = idsOf(ACTION_GROUPS, area);
    const ri = idsOf(RESOURCE_GROUPS, area);
    return { n: ai.filter((i) => a.includes(i)).length + ri.filter((i) => r.includes(i)).length, total: ai.length + ri.length };
  };
  const set = (areas: string[], on: boolean) => {
    const ai = areas.flatMap((x) => idsOf(ACTION_GROUPS, x));
    const ri = areas.flatMap((x) => idsOf(RESOURCE_GROUPS, x));
    onActions(withIds(a, ai, on).join("\n"));
    onResources(withIds(r, ri, on).join("\n"));
  };
  const areas = ACTION_GROUPS.map((g) => g.id);
  const totals = areas.map(count);
  const every = totals.every((t) => t.n === t.total);
  const any = totals.some((t) => t.n > 0);

  return (
    <fieldset className="space-y-2 sm:col-span-2">
      <legend className="text-sm font-medium text-ink">What it covers</legend>
      <p className="text-xs text-ink-subtle">
        Tick a feature area to cover all of it (its actions and resources are ticked below). Fine-tune below, or leave it as it is.
      </p>
      <div className="space-y-2 rounded-card border border-border p-3">
        <Checkbox isSelected={every} isIndeterminate={any && !every} onChange={(on) => set(areas, on)}>
          <span className="font-medium">All areas</span>
        </Checkbox>
        <div className="grid gap-2 ps-7 sm:grid-cols-2">
          {ACTION_GROUPS.map((g, i) => {
            const t = totals[i]!;
            return (
              <div key={g.id}>
                <Checkbox isSelected={t.n === t.total} isIndeterminate={t.n > 0 && t.n < t.total} onChange={(on) => set([g.id], on)}>
                  {g.label}
                </Checkbox>
                {g.description ? <p className="ps-7 text-xs text-ink-subtle">{g.description}</p> : null}
              </div>
            );
          })}
        </div>
      </div>
    </fieldset>
  );
}

/**
 * Pick from a list instead of typing exact names. The value stays the text the form already sends (one entry per line),
 * so the ticked boxes and the "something else" box are two views of the same list; entries that are not in the list are
 * kept in the box so nothing is lost.
 *
 * Give it `groups` to show the choices by feature area, each with a box that ticks or clears the whole area. `choices` alone keeps
 * the plain list the mandate dialog uses.
 */
export function ChoiceList({
  label,
  description,
  choices,
  groups,
  value,
  onChange,
  otherLabel,
  otherPlaceholder,
}: {
  label: string;
  description: string;
  choices?: Choice[];
  groups?: ChoiceGroup[];
  value: string;
  onChange: (v: string) => void;
  otherLabel: string;
  otherPlaceholder: string;
}) {
  const sections: ChoiceGroup[] = groups ?? [{ id: "all", label: "", choices: choices ?? [] }];
  const all = sections.flatMap((s) => s.choices);
  const chosen = listInput(value);
  const known = new Set(all.map((c) => c.id));
  const custom = chosen.filter((v) => !known.has(v));

  /** The new list: every known choice that stays ticked (in list order), then whatever was typed under "Something else". */
  const write = (ticked: (id: string) => boolean) => onChange([...all.map((c) => c.id).filter(ticked), ...custom].join("\n"));
  const toggle = (id: string, on: boolean) => write((i) => (i === id ? on : chosen.includes(i)));
  const toggleGroup = (g: ChoiceGroup, on: boolean) => {
    const ids = new Set(g.choices.map((c) => c.id));
    write((i) => (ids.has(i) ? on : chosen.includes(i)));
  };

  return (
    <fieldset className="space-y-2">
      <legend className="text-sm font-medium text-ink">
        {label} <span aria-hidden className="text-danger-fg">*</span>
      </legend>
      <p className="text-xs text-ink-subtle">{description}</p>
      <div className="space-y-3 rounded-card border border-border p-3">
        {sections.map((s) => {
          const ticked = s.choices.filter((c) => chosen.includes(c.id)).length;
          return (
            <div key={s.id} role={groups ? "group" : undefined} aria-label={groups ? s.label : undefined} className="space-y-2">
              {groups ? (
                <div className="space-y-0.5 border-b border-border pb-1.5">
                  <Checkbox
                    isSelected={ticked === s.choices.length && ticked > 0}
                    isIndeterminate={ticked > 0 && ticked < s.choices.length}
                    onChange={(on) => toggleGroup(s, on)}
                  >
                    <span className="font-medium">{s.label}</span>{" "}
                    <span className="text-xs text-ink-subtle">
                      ({ticked} of {s.choices.length})
                    </span>
                  </Checkbox>
                  {s.description ? <p className="ps-7 text-xs text-ink-subtle">{s.description}</p> : null}
                </div>
              ) : null}
              <div className={groups ? "space-y-2 ps-7" : "space-y-2"}>
                {s.choices.map((c) => (
                  <Checkbox key={c.id} isSelected={chosen.includes(c.id)} onChange={(on) => toggle(c.id, on)}>
                    {c.label} <code className="ms-1 rounded bg-sunken px-1 font-mono text-xs text-ink-muted">{c.id}</code>
                  </Checkbox>
                ))}
              </div>
            </div>
          );
        })}
        <details open={custom.length > 0}>
          <summary className="cursor-pointer pt-1 text-xs font-medium text-brand-700">Something else</summary>
          <div className="pt-2">
            <TextField
              label={otherLabel}
              value={custom.join(", ")}
              onChange={(t) => onChange([...all.map((c) => c.id).filter((i) => chosen.includes(i)), ...listInput(t)].join("\n"))}
              mono
              placeholder={otherPlaceholder}
            />
          </div>
        </details>
      </div>
    </fieldset>
  );
}
