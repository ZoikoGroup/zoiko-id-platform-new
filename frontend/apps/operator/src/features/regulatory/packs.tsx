"use client";

import type { PackApproverRole, PackSource, PolicyPack } from "@zoiko/api-client";
import { Badge, Button, Card, DataTable, Dialog, EmptyState, PageHeader, Select, StatusBadge, TextField, type Column } from "@zoiko/ui";
import { BookOpenCheck } from "lucide-react";
import { useState } from "react";

import { QueryView, useCellMutation, useCellQuery } from "@/lib/op";

import { CAPABILITY_OPTIONS, COUNTRY_OPTIONS, PURPOSE_OPTIONS, Facts, Hash, isoFromLocal, list, Mono, opt, REG, RegError, upper } from "./common";

const ROLES: PackApproverRole[] = ["legal", "grc", "security", "business_owner"];
const SOURCE_STATUS: PackSource["status"][] = ["LAW", "GUIDANCE", "PROPOSAL"];

function CreatePackDialog({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const [f, setF] = useState({
    pack_id: "",
    country: "",
    subdivision: "",
    product: "INDIVIDUAL_VERIFICATION",
    purpose: "ENGAGEMENT",
    instrument: "",
    url: "",
    sourceStatus: "LAW" as PackSource["status"],
    retrieved: "",
    effective: "",
    owner: "",
    evidence: "",
    alternates: "",
    lawful: "",
  });
  const set = (k: keyof typeof f) => (v: string) => setF((p) => ({ ...p, [k]: v }));
  const create = useCellMutation(
    (api) =>
      api.regulatory.createPack({
        pack_id: f.pack_id.trim(),
        country: upper(f.country),
        subdivision: opt(f.subdivision) ?? null,
        product: upper(f.product),
        purpose: upper(f.purpose),
        sources: [
          {
            instrument: f.instrument.trim(),
            url: f.url.trim(),
            status: f.sourceStatus,
            retrieved_at: isoFromLocal(f.retrieved),
            effective_from: isoFromLocal(f.effective),
            owner: f.owner.trim(),
          },
        ],
        rules: { evidence_minimum: list(f.evidence), alternate_routes: list(f.alternates), lawful_basis: f.lawful.trim() },
      }),
    { invalidate: [REG.packs], success: (p) => `Draft ${p.pack_id} v${p.version} opened`, onSuccess: onClose },
  );
  const ready = f.pack_id && f.country.length === 2 && f.instrument && f.url.startsWith("https://") && f.retrieved && f.effective && f.owner && list(f.evidence).length;
  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={(o) => !o && onClose()}
      title="New policy pack draft"
      description="A pack records what the law of one market requires, with its legal source. It governs nothing until four different people approve the exact version and it is published."
      size="lg"
      footer={
        <>
          <Button variant="secondary" onPress={onClose}>
            Cancel
          </Button>
          <Button onPress={() => create.mutate(undefined)} pending={create.isPending} isDisabled={!ready}>
            Open draft
          </Button>
        </>
      }
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <TextField label="Pack ID" description="lowercase, country-product-purpose" placeholder="gb-idv-engagement" value={f.pack_id} onChange={set("pack_id")} mono isRequired />
        <Select label="Country" description="the market this pack governs" options={COUNTRY_OPTIONS} value={f.country} onChange={set("country")} isRequired />
        <TextField label="Subdivision" description="optional: England, Scotland, a US state" placeholder="GB-ENG" value={f.subdivision} onChange={set("subdivision")} />
        <Select label="Product" description="what the pack governs" options={CAPABILITY_OPTIONS} value={f.product} onChange={set("product")} isRequired />
        <Select label="Purpose" options={PURPOSE_OPTIONS} value={f.purpose} onChange={set("purpose")} isRequired />
        <TextField label="Legal instrument" description="the law, regulation or guidance it comes from" placeholder="Immigration (Restrictions on Employment) Order 2007" value={f.instrument} onChange={set("instrument")} isRequired />
        <TextField label="Source URL" description="https only" placeholder="https://www.legislation.gov.uk/uksi/2007/3290" value={f.url} onChange={set("url")} type="url" isRequired />
        <Select
          label="Source status"
          value={f.sourceStatus}
          onChange={(v) => setF((p) => ({ ...p, sourceStatus: SOURCE_STATUS.find((s) => s === v) ?? "LAW" }))}
          options={SOURCE_STATUS.map((s) => ({ id: s, label: s }))}
        />
        <TextField label="Retrieved at" type="datetime-local" value={f.retrieved} onChange={set("retrieved")} isRequired />
        <TextField label="Effective from" type="datetime-local" value={f.effective} onChange={set("effective")} isRequired />
        <TextField label="Source owner" description="who publishes the source" placeholder="UK Home Office" value={f.owner} onChange={set("owner")} isRequired />
        <TextField label="Lawful basis" description="optional" placeholder="Legal obligation (UK GDPR Art. 6(1)(c))" value={f.lawful} onChange={set("lawful")} />
        <TextField label="Evidence minimum" description="codes, comma separated" placeholder="PASSPORT, SELFIE_MATCH" value={f.evidence} onChange={set("evidence")} mono isRequired />
        <TextField label="Alternate routes" description="codes, comma separated; optional" placeholder="MANUAL_REVIEW, IN_PERSON" value={f.alternates} onChange={set("alternates")} mono />
      </div>
      {create.error ? <RegError error={create.error} /> : null}
    </Dialog>
  );
}

function PackDialog({ pack, onClose }: { pack: PolicyPack | null; onClose: () => void }) {
  const [role, setRole] = useState<PackApproverRole>("legal");
  const opts = { invalidate: [REG.packs], onSuccess: onClose };
  const approve = useCellMutation((api, p: PolicyPack) => api.regulatory.approvePack(p.pack_id, p.version, role, p.pack_hash), {
    ...opts,
    success: "Approval recorded against the exact hash",
  });
  const publish = useCellMutation((api, p: PolicyPack) => api.regulatory.publishPack(p.pack_id, p.version), { ...opts, success: "Pack published" });
  const retire = useCellMutation((api, p: PolicyPack) => api.regulatory.retirePack(p.pack_id, p.version), { ...opts, success: "Pack retired" });
  const error = approve.error ?? publish.error ?? retire.error;
  return (
    <Dialog isOpen={pack !== null} onOpenChange={(o) => !o && onClose()} title={pack ? `${pack.pack_id} v${pack.version}` : ""} size="lg">
      {pack ? (
        <div className="space-y-4">
          <Facts
            items={[
              ["Status", <StatusBadge key="s" state={pack.status} kind="Pack" />],
              ["Market", `${pack.market.country}${pack.market.subdivision ? ` / ${pack.market.subdivision}` : ""} · ${pack.market.product} · ${pack.market.purpose}`],
              ["Author", <Mono key="a">{pack.author}</Mono>],
              ["Hash to sign", <Hash key="h" value={pack.pack_hash} />],
              ["Evidence minimum", pack.rules.evidence_minimum.join(", ")],
              ["Lawful basis", pack.rules.lawful_basis || "-"],
              ["Approvals", Object.keys(pack.approvals).join(", ") || "none"],
              ["Still needed", pack.missing_approvals.join(", ") || "none"],
            ]}
          />
          <ul className="space-y-1 text-sm">
            {pack.sources.map((s) => (
              <li key={s.url}>
                <Badge tone={s.status === "LAW" ? "ok" : "warn"}>{s.status}</Badge> {s.instrument} <span className="text-ink-subtle">({s.owner})</span>
              </li>
            ))}
          </ul>
          {pack.status === "DRAFT" ? (
            <div className="flex flex-wrap items-end gap-2 border-t border-border pt-4">
              <Select
                label="Approve as"
                value={role}
                onChange={(v) => setRole(ROLES.find((r) => r === v) ?? "legal")}
                options={ROLES.filter((r) => pack.missing_approvals.includes(r)).map((r) => ({ id: r, label: r }))}
              />
              <Button onPress={() => approve.mutate(pack)} pending={approve.isPending} isDisabled={!pack.missing_approvals.includes(role)}>
                Sign this hash
              </Button>
              <Button variant="secondary" onPress={() => publish.mutate(pack)} pending={publish.isPending} isDisabled={pack.missing_approvals.length > 0}>
                Publish
              </Button>
            </div>
          ) : null}
          {pack.status === "APPROVED" ? (
            <Button variant="danger" onPress={() => retire.mutate(pack)} pending={retire.isPending}>
              Retire
            </Button>
          ) : null}
          {error ? <RegError error={error} /> : null}
        </div>
      ) : null}
    </Dialog>
  );
}

export function PacksPage() {
  const [country, setCountry] = useState("");
  const q = useCellQuery([...REG.packs, country], (api) => api.regulatory.packs(opt(upper(country))));
  const [creating, setCreating] = useState(false);
  const [openKey, setOpenKey] = useState<string | null>(null);
  const keyOf = (p: PolicyPack) => `${p.pack_id}:${p.version}`;
  const open = q.data?.items.find((x) => keyOf(x) === openKey) ?? null;
  const setOpen = (x: PolicyPack | null) => setOpenKey(x ? keyOf(x) : null);
  const columns: Column<PolicyPack>[] = [
    { key: "id", header: "Pack", pinned: true, render: (p) => <Mono>{`${p.pack_id} v${p.version}`}</Mono>, sortValue: (p) => p.pack_id },
    { key: "status", header: "Status", render: (p) => <StatusBadge state={p.status} kind="Pack" /> },
    { key: "market", header: "Market", render: (p) => `${p.market.country} · ${p.market.product} · ${p.market.purpose}`, sortValue: (p) => p.market.country },
    { key: "approvals", header: "Approvals", render: (p) => `${4 - p.missing_approvals.length}/4` },
    { key: "hash", header: "Hash", render: (p) => <Hash value={p.pack_hash} /> },
    {
      key: "open",
      header: "Actions",
      align: "end",
      render: (p) => (
        <Button size="xs" onPress={() => setOpen(p)} aria-label={`Open ${p.pack_id} v${p.version}`}>
          Open
        </Button>
      ),
    },
  ];
  return (
    <>
      <PageHeader
        title="Policy packs"
        description="The legal rules for each market (WF-01). Four different approvers sign the exact pack hash; the author cannot approve."
        actions={<Button onPress={() => setCreating(true)}>New draft</Button>}
      />
      <Card padded={false} title="Packs" actions={<TextField label="Country" value={country} onChange={setCountry} placeholder="All" className="w-28" />}>
        <QueryView query={q}>
          {(d) => (
            <DataTable
              caption="Policy packs"
              columns={columns}
              rows={d.items}
              rowKey={(p) => `${p.pack_id}:${p.version}`}
              empty={
                <EmptyState icon={BookOpenCheck} title="No policy packs">
                  No market has an approved legal pack, so under enforce every regulated market is refused.
                </EmptyState>
              }
            />
          )}
        </QueryView>
      </Card>
      <CreatePackDialog isOpen={creating} onClose={() => setCreating(false)} />
      <PackDialog pack={open} onClose={() => setOpen(null)} />
    </>
  );
}
