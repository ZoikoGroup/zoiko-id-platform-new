"use client";

import type { AuthorityGrant, ContinuityMode, GrantCreate, GrantType } from "@zoiko/api-client";
import { Banner, Button, Checkbox, Dialog, FieldGroup, Select, Switch, TextField, humanize, type Option } from "@zoiko/ui";
import { Info } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { ApiErrorView, listInput, newIdempotencyKey, useCellMutation, useCellQuery } from "@/lib/cell";

import { ACTION_CHOICES, ChoiceList, RESOURCE_CHOICES } from "./choice-list";
import { reviewRequired, scopeBreadth } from "./scope-review";

import {
  CURRENCY_RE,
  DECIMAL_RE,
  FormProblem,
  GrantPicker,
  PrincipalField,
  RISK_OPTIONS,
  UUID_RE,
  defaultWindow,
  intInput,
  localToIso,
  useIdempotency,
  useMe,
} from "./shared";

const GRANT_TYPES: GrantType[] = ["ROLE_ISSUED", "PERSONAL", "ORGANIZATION_DIRECT", "SERVICE_DELEGATION", "AGENT_DELEGATION", "EXTERNAL_DELEGATION"];
const CONTINUITY: { id: ContinuityMode; description: string }[] = [
  { id: "PERSON_BOUND", description: "Ends if the grantor leaves or loses authority." },
  { id: "ROLE_CONTINUOUS", description: "Survives a change of the person holding the grantor's role." },
  { id: "ORGANIZATION_CONTINUOUS", description: "Held by the organization rather than a person." },
  { id: "CONTRACT_BOUND", description: "Tied to a contract's term." },
];
const CURRENCIES: Option[] = ["GBP", "USD", "EUR"].map((c) => ({ id: c, label: c }));
const ASSURANCE: Option[] = [0, 1, 2, 3].map((n) => ({ id: String(n), label: n === 0 ? "No minimum" : `AAL-Z${n} or higher` }));

/** Optional scope list: empty → null ("any"), which the cell reads as unrestricted. */
const optList = (v: string) => {
  const l = listInput(v);
  return l.length ? l : null;
};

export function IssueGrantDialog({ onClose, onIssued }: { onClose: () => void; onIssued: (g: AuthorityGrant) => void }) {
  const me = useMe();
  const [win] = useState(() => defaultWindow(90));
  const [f, setF] = useState({
    grantee: "",
    represented: "",
    grantType: "ROLE_ISSUED" as GrantType,
    continuity: "PERSON_BOUND" as ContinuityMode,
    parent: "",
    root: "",
    actions: "",
    resources: "",
    purposes: "",
    jurisdictions: "",
    tools: "",
    dataCategories: "",
    amount: "",
    currency: "",
    count: "",
    dataBytes: "",
    minAssurance: "0",
    from: win.from,
    to: win.to,
    delegate: false,
    depth: "1",
    risk: "3",
  });
  const set = <K extends keyof typeof f>(k: K) => (v: (typeof f)[K]) => setF((s) => ({ ...s, [k]: v }));
  const [problem, setProblem] = useState<string | null>(null);
  const [reviewed, setReviewed] = useState(false);
  // How wide the grant is, as typed so far: a wide grant needs an explicit review before it can be issued (UX-07).
  const breadth = scopeBreadth({
    actions: listInput(f.actions),
    resources: listInput(f.resources),
    hasValueLimit: f.amount.trim() !== "",
    hasCountLimit: f.count.trim() !== "",
    delegate: f.delegate,
    depth: Number(f.depth) || 0,
  });
  const keyFor = useIdempotency(newIdempotencyKey);

  // The company the person already acts for, and the register assertion that roots their authority. The company is an
  // organization, not a person, so it is not in the principal list and cannot be guessed: offer it, and fill both in.
  const mine = useCellQuery(["authority", "grants", "mine-rooted", me], (api) =>
    api.authority.grants({ status: "ACTIVE", grantee_principal_id: me, limit: 50 }),
  );
  const rooted = mine.data?.items.find((g) => g.authority_root_assertion_id);
  const company = rooted?.represented_principal_id ?? null;
  const companyRoot = rooted?.authority_root_assertion_id ?? null;
  // Shown and sent unless the person typed something else; a grant derived from another grant needs neither.
  const represented = f.represented || (f.parent ? "" : (company ?? ""));
  const root = f.root || (f.parent ? "" : (companyRoot ?? ""));

  // The message that stops a submit sits at the foot of a long form: bring it into view.
  const problemRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (problem) problemRef.current?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [problem]);

  const create = useCellMutation((api, v: { body: GrantCreate; key: string }) => api.authority.createGrant(v.body, v.key), {
    invalidate: [["authority", "grants"]],
    success: "Grant issued",
    onSuccess: (g) => onIssued(g),
  });

  function build(): GrantCreate | string {
    if (!UUID_RE.test(f.grantee)) return "Choose the grantee principal.";
    if (!UUID_RE.test(represented)) return "Choose the principal the grantee acts on behalf of.";
    const actions = listInput(f.actions);
    const resources = listInput(f.resources);
    if (!actions.length) return "List at least one action.";
    if (!resources.length) return "List at least one resource.";
    const from = localToIso(f.from);
    const to = localToIso(f.to);
    if (!from || !to) return "Enter a valid start and end time.";
    if (new Date(to) <= new Date(from)) return "The end of the window must be after its start.";
    if (f.parent && !UUID_RE.test(f.parent)) return "The parent grant must be a grant UUID.";
    if (!f.parent && !UUID_RE.test(root.trim()))
      return "A top-level grant needs the authority root assertion (it is filled in for you when you hold one). Or choose “Derive from my grant”.";
    let value_limit: GrantCreate["value_limit"] = null;
    if (f.amount.trim() || f.currency.trim()) {
      if (!DECIMAL_RE.test(f.amount.trim())) return "The value limit must be a non-negative decimal, e.g. 2500.00.";
      if (!CURRENCY_RE.test(f.currency.trim())) return "The currency must be a three-letter ISO code, e.g. USD.";
      value_limit = { amount: f.amount.trim(), currency: f.currency.trim() };
    }
    const count_limit = intInput(f.count);
    const data_limit_bytes = intInput(f.dataBytes);
    if (Number.isNaN(count_limit)) return "The count limit must be a whole number.";
    if (Number.isNaN(data_limit_bytes)) return "The data limit must be a whole number of bytes.";
    const depth = intInput(f.depth) ?? 0;
    if (f.delegate && (Number.isNaN(depth) || depth < 1 || depth > 16)) return "Delegation depth must be between 1 and 16.";
    const needsReview = reviewRequired(breadth, reviewed);
    if (needsReview) return needsReview;
    return {
      grantee_principal_id: f.grantee,
      represented_principal_id: represented,
      grant_type: f.grantType,
      continuity_mode: f.continuity,
      parent_grant_id: f.parent || null,
      authority_root_assertion_id: root.trim() || null,
      actions,
      resources,
      purposes: optList(f.purposes),
      jurisdictions: optList(f.jurisdictions),
      tools: optList(f.tools),
      data_categories: optList(f.dataCategories),
      value_limit,
      count_limit: count_limit ?? null,
      data_limit_bytes: data_limit_bytes ?? null,
      min_assurance: Number(f.minAssurance),
      valid_from: from,
      valid_to: to,
      delegation: { allowed: f.delegate, max_depth: f.delegate ? depth : 0 },
      risk_class: Number(f.risk),
    };
  }

  function submit(e?: FormEvent) {
    e?.preventDefault();
    const body = build();
    if (typeof body === "string") {
      setProblem(body);
      return;
    }
    setProblem(null);
    create.mutate({ body, key: keyFor(body) });
  }

  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && !create.isPending && onClose()}
      title="Issue authority grant"
      description="Give a principal authority to act on behalf of another, within limits."
      size="xl"
      footer={
        <>
          <Button variant="secondary" isDisabled={create.isPending} onPress={onClose}>
            Cancel
          </Button>
          <Button pending={create.isPending} onPress={() => submit()}>
            Issue grant
          </Button>
        </>
      }
    >
      <form onSubmit={submit} className="space-y-5" aria-label="Issue authority grant">
        <Banner tone="info" icon={Info} title="You are the grantor">
          You can only give what you hold yourself. For company spending the grant is for the company and is checked against your own authority.
        </Banner>

        <div className="grid gap-4 sm:grid-cols-2">
          <PrincipalField label="Grantee" isRequired value={f.grantee} onChange={set("grantee")} description="Who receives the authority." />
          <div className="grid grid-cols-2 gap-4">
            <TextField label="Value limit" value={f.amount} onChange={set("amount")} inputMode="decimal" mono placeholder="10000.00" description="The most they may approve." />
            <Select
              label="Currency"
              value={f.currency || null}
              placeholder="Choose…"
              onChange={set("currency")}
              options={CURRENCIES}
            />
          </div>
          <ChoiceList
            label="Actions"
            description="Tick what they may do."
            choices={ACTION_CHOICES}
            value={f.actions}
            onChange={set("actions")}
            otherLabel="Another action name"
            otherPlaceholder="exact.action.name"
          />
          <ChoiceList
            label="Resources"
            description="Tick what it applies to."
            choices={RESOURCE_CHOICES}
            value={f.resources}
            onChange={set("resources")}
            otherLabel="Another resource"
            otherPlaceholder="type:id"
          />
        </div>

        <details className="rounded-card border border-border">
          <summary className="cursor-pointer px-4 py-3 text-sm font-medium text-ink">
            More options <span className="font-normal text-ink-subtle">(already set to safe values; most people skip this)</span>
          </summary>
          <div className="space-y-6 border-t border-border p-4">
            <FieldGroup legend="Who it is for">
              <div className="grid gap-4 sm:grid-cols-2">
                <PrincipalField
                  label="On behalf of"
                  isRequired
                  value={represented}
                  onChange={set("represented")}
                  preferTypes={["ORGANIZATION", "ORG_UNIT", "HUMAN"]}
                  extraOptions={[
                    ...(company ? [{ id: company, label: "The company", description: company }] : []),
                    ...(me ? [{ id: me, label: "Me (signed-in user)", description: me }] : []),
                  ]}
                  description="Filled in as the company. For company spending, keep it."
                />
                <TextField
                  label="Authority root assertion"
                  value={root}
                  onChange={set("root")}
                  mono
                  isRequired={!f.parent}
                  description="Filled in for you from your own grant. Leave it unless you derive from another grant."
                />
                <GrantPicker
                  label="Derive from my grant"
                  value={f.parent}
                  onChange={set("parent")}
                  granteeId={me}
                  allowNone="None (top-level grant)"
                  description="Only when passing on part of a grant you hold."
                />
                <Select
                  label="Grant type"
                  value={f.grantType}
                  onChange={(v) => set("grantType")(v as GrantType)}
                  options={GRANT_TYPES.map((t) => ({ id: t, label: humanize(t) }))}
                />
                <Select
                  label="Continuity"
                  value={f.continuity}
                  onChange={(v) => set("continuity")(v as ContinuityMode)}
                  options={CONTINUITY.map((c) => ({ id: c.id, label: humanize(c.id), description: c.description }))}
                />
              </div>
            </FieldGroup>

            <FieldGroup legend="Narrow it further" description="Leave empty for no restriction.">
              <div className="grid gap-4 sm:grid-cols-2">
                <TextField label="Purposes" multiline rows={2} mono value={f.purposes} onChange={set("purposes")} />
                <TextField label="Jurisdictions" multiline rows={2} mono value={f.jurisdictions} onChange={set("jurisdictions")} />
                <TextField label="Tools" multiline rows={2} mono value={f.tools} onChange={set("tools")} />
                <TextField label="Data categories" multiline rows={2} mono value={f.dataCategories} onChange={set("dataCategories")} />
                <TextField label="Count limit" value={f.count} onChange={set("count")} inputMode="numeric" mono />
                <TextField label="Data limit (bytes)" value={f.dataBytes} onChange={set("dataBytes")} inputMode="numeric" mono />
                <Select label="Minimum assurance" value={f.minAssurance} onChange={set("minAssurance")} options={ASSURANCE} />
              </div>
            </FieldGroup>

            <FieldGroup legend="Window and delegation">
              <div className="grid gap-4 sm:grid-cols-2">
                <TextField label="Valid from" type="datetime-local" isRequired value={f.from} onChange={set("from")} />
                <TextField label="Valid until" type="datetime-local" isRequired value={f.to} onChange={set("to")} />
                <div className="space-y-2">
                  <Switch isSelected={f.delegate} onChange={set("delegate")}>
                    Grantee may delegate further
                  </Switch>
                  {f.delegate ? (
                    <TextField label="Maximum delegation depth" value={f.depth} onChange={set("depth")} inputMode="numeric" mono description="1–16 further links." />
                  ) : null}
                </div>
                <Select
                  label="Risk class"
                  value={f.risk}
                  onChange={set("risk")}
                  options={RISK_OPTIONS}
                  description="The highest risk class the grant must be usable at."
                />
              </div>
            </FieldGroup>
          </div>
        </details>

        {breadth.length ? (
          <Banner tone="warn" title="Review before issuing">
            <ul className="mt-1 list-disc space-y-0.5 ps-5 text-sm">
              {breadth.map((b) => (
                <li key={b.code}>{b.message}</li>
              ))}
            </ul>
            <div className="mt-3">
              <Checkbox isSelected={reviewed} onChange={setReviewed}>
                I have reviewed this scope and it is as wide as it needs to be
              </Checkbox>
            </div>
          </Banner>
        ) : null}

        <div ref={problemRef}>
          <FormProblem>{problem}</FormProblem>
        </div>
        {create.error ? <ApiErrorView error={create.error} title="The grant was not issued" /> : null}
      </form>
    </Dialog>
  );
}
