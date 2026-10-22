"use client";

import type { ProfileIn, VerificationProfile } from "@zoiko/api-client";
import {
  AssuranceBadge,
  Button,
  Card,
  Checkbox,
  CopyId,
  DataTable,
  EmptyState,
  FieldGroup,
  Select,
  StatusBadge,
  TextField,
  humanize,
  type Column,
} from "@zoiko/ui";
import { ClipboardList, Plus } from "lucide-react";
import { useState } from "react";

import { listInput, useCellMutation, useCellQuery } from "@/lib/cell";

import { AdminQueryView, FormDialog, R3ActionDialog, joined, required } from "../identity-providers/shared";

export const PROFILES_KEY = ["admin", "verification", "profiles"];
/** The account verification page caches the same list under this key. */
const ACCOUNT_PROFILES_KEY = ["verification", "profiles"];

/** verify.EVIDENCE_TYPES on the backend. */
const EVIDENCE_TYPES = [
  "GOVERNMENT_ID",
  "DIGITAL_CREDENTIAL",
  "AUTHORITATIVE_SOURCE",
  "FINANCIAL_SOURCE",
  "ADDRESS_EVIDENCE",
  "BIOMETRIC_BINDING",
  "IN_PERSON_ATTESTATION",
  "TRUSTED_IDENTITY_PROVIDER",
  "ORGANIZATION_SOURCE",
] as const;

type Level = ProfileIn["level"];
const LEVELS: { id: Level; label: string; description: string }[] = [
  { id: "IAL-Z1", label: "IAL-Z1", description: "Self-asserted or lightly checked claims." },
  { id: "IAL-Z2", label: "IAL-Z2", description: "Evidence bound to the person (binding required)." },
  { id: "IAL-Z3", label: "IAL-Z3", description: "Strongest evidence, typically in person or biometric." },
];

function jurisdictionList(v: string, field: string): string[] {
  const list = listInput(v).map((j) => j.toUpperCase());
  for (const j of list) if (!/^[A-Z]{2}$/.test(j)) throw new Error(`${field}: “${j}” is not a two-letter country code.`);
  return list;
}

function CreateProfileDialog({ onClose }: { onClose: () => void }) {
  const [name, setName] = useState("");
  const [level, setLevel] = useState<Level>("IAL-Z2");
  const [claims, setClaims] = useState("given_name, family_name, birthdate");
  const [evidence, setEvidence] = useState<string[]>(["GOVERNMENT_ID"]);
  const [jurisdictions, setJurisdictions] = useState("");
  const [binding, setBinding] = useState(true);
  const [liveness, setLiveness] = useState(false);
  const [biometric, setBiometric] = useState("");
  const [freshness, setFreshness] = useState("365");
  const [manualReview, setManualReview] = useState(false);
  const [reviewApprovals, setReviewApprovals] = useState("1");
  const [purposes, setPurposes] = useState("");
  const [localError, setLocalError] = useState<unknown>(null);

  const create = useCellMutation((api, body: ProfileIn) => api.verification.createProfile(body), {
    invalidate: [PROFILES_KEY, ACCOUNT_PROFILES_KEY],
    success: (r) => `Profile “${r.name}” v${r.version} created as a draft`,
    onSuccess: onClose,
  });

  function submit() {
    setLocalError(null);
    create.reset();
    try {
      const requiredClaims = listInput(claims);
      if (!requiredClaims.length) throw new Error("Add at least one required claim.");
      if (!evidence.length) throw new Error("Accept at least one evidence type.");
      const juris = jurisdictionList(jurisdictions, "Jurisdictions");
      if (!juris.length) throw new Error("Add at least one jurisdiction.");
      const bio = jurisdictionList(biometric, "Biometric jurisdictions");
      if (bio.some((b) => !juris.includes(b))) throw new Error("Biometric jurisdictions must be among the profile's jurisdictions.");
      if (level !== "IAL-Z1" && !binding) throw new Error("IAL-Z2 and above require person-to-evidence binding.");
      const days = Number(freshness);
      if (!Number.isInteger(days) || days < 1) throw new Error("Freshness must be a whole number of days.");
      create.mutate({
        name: required(name, "Name"),
        level,
        requirements: {
          required_claims: requiredClaims,
          accepted_evidence: evidence,
          jurisdictions: juris,
          binding_required: binding,
          liveness_required: liveness,
          biometric_jurisdictions: bio,
          freshness_days: days,
          manual_review: manualReview,
          review_approvals: Number(reviewApprovals),
          reliance_purposes: listInput(purposes),
        },
      });
    } catch (e) {
      setLocalError(e);
    }
  }

  return (
    <FormDialog
      isOpen
      onClose={onClose}
      title="New verification profile"
      description="A profile defines what evidence proves which claims at which assurance level. It is created as a draft; activating it releases it."
      submitLabel="Create draft"
      pending={create.isPending}
      error={localError ?? create.error}
      onSubmit={submit}
      risk="R3"
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <TextField
          label="Name"
          isRequired
          value={name}
          onChange={setName}
          description="Up to 64 characters. Reusing a name creates a new version."
        />
        <Select
          label="Assurance level"
          isRequired
          value={level}
          onChange={(v) => {
            setLevel(v as Level);
            if (v !== "IAL-Z1") setBinding(true);
          }}
          options={LEVELS}
        />
        <TextField
          label="Required claims"
          isRequired
          mono
          value={claims}
          onChange={setClaims}
          description="Comma separated."
          className="sm:col-span-2"
        />
        <TextField
          label="Jurisdictions"
          isRequired
          mono
          value={jurisdictions}
          onChange={setJurisdictions}
          placeholder="GB, IE"
          description="Two-letter country codes where this profile applies."
        />
        <TextField
          label="Freshness (days)"
          type="number"
          inputMode="numeric"
          value={freshness}
          onChange={setFreshness}
          description="How long a verification stays reusable."
        />
      </div>

      <FieldGroup legend="Accepted evidence" description="At least one.">
        <div className="grid gap-2 sm:grid-cols-2">
          {EVIDENCE_TYPES.map((t) => (
            <Checkbox key={t} isSelected={evidence.includes(t)} onChange={(on) => setEvidence(on ? [...evidence, t] : evidence.filter((x) => x !== t))}>
              {humanize(t)}
            </Checkbox>
          ))}
        </div>
      </FieldGroup>

      <FieldGroup legend="Binding and liveness">
        <Checkbox
          isSelected={binding}
          onChange={setBinding}
          isDisabled={level !== "IAL-Z1"}
          description={level !== "IAL-Z1" ? "Always required at IAL-Z2 and above." : "Tie the evidence to the person presenting it."}
        >
          Person-to-evidence binding required
        </Checkbox>
        <Checkbox isSelected={liveness} onChange={setLiveness}>
          Liveness check required
        </Checkbox>
        <TextField
          label="Biometric jurisdictions (optional)"
          mono
          value={biometric}
          onChange={setBiometric}
          description="Where biometric binding may be used. Must be a subset of the jurisdictions above."
        />
      </FieldGroup>

      <FieldGroup legend="Review and reuse">
        <Checkbox isSelected={manualReview} onChange={setManualReview} description="Send inconclusive results to a reviewer instead of failing them.">
          Manual review fallback
        </Checkbox>
        <div className="grid gap-4 sm:grid-cols-2">
          <Select
            label="Reviewer approvals needed"
            value={reviewApprovals}
            onChange={setReviewApprovals}
            options={[
              { id: "1", label: "1 reviewer" },
              { id: "2", label: "2 reviewers" },
              { id: "3", label: "3 reviewers" },
            ]}
          />
          <TextField
            label="Reliance purposes (optional)"
            mono
            value={purposes}
            onChange={setPurposes}
            description="Purposes for which other apps may rely on the result."
          />
        </div>
      </FieldGroup>
    </FormDialog>
  );
}

export function ProfilesSection() {
  const profiles = useCellQuery(PROFILES_KEY, (api) => api.verification.profiles());
  const [creating, setCreating] = useState(false);
  const [target, setTarget] = useState<VerificationProfile | null>(null);

  const activate = useCellMutation((api, id: string) => api.verification.activateProfile(id), {
    invalidate: [PROFILES_KEY, ACCOUNT_PROFILES_KEY],
    success: "Profile activated",
  });

  const columns: Column<VerificationProfile>[] = [
    {
      key: "name",
      header: "Profile",
      pinned: true,
      sortValue: (p) => p.name,
      render: (p) => (
        <div className="min-w-0">
          <div className="font-medium text-ink">{p.name}</div>
          <CopyId value={p.profile_id} label="profile" />
        </div>
      ),
    },
    { key: "version", header: "Version", sortValue: (p) => p.version, render: (p) => <span className="tabular">v{p.version}</span> },
    { key: "level", header: "Level", sortValue: (p) => p.level, render: (p) => <AssuranceBadge kind="Identity" level={p.level} /> },
    { key: "jurisdictions", header: "Jurisdictions", render: (p) => joined(p.jurisdictions) },
    { key: "status", header: "Status", sortValue: (p) => p.status, render: (p) => <StatusBadge state={p.status} kind="Profile" /> },
    {
      key: "actions",
      header: "Actions",
      align: "end",
      render: (p) =>
        p.status === "DRAFT" ? (
          <Button size="xs" variant="secondary" onPress={() => setTarget(p)}>
            Activate
          </Button>
        ) : null,
    },
  ];

  return (
    <>
      <Card
        padded={false}
        title="Verification profiles"
        description="What evidence proves which claims, per assurance level and jurisdiction. One active version per name."
        actions={
          <Button size="sm" icon={<Plus aria-hidden className="size-4" />} onPress={() => setCreating(true)}>
            New profile
          </Button>
        }
      >
        <AdminQueryView query={profiles}>
          {(data) => (
            <DataTable
              caption="Verification profiles"
              columns={columns}
              rows={data.items}
              rowKey={(p) => p.profile_id}
              empty={
                <EmptyState icon={ClipboardList} title="No verification profiles">
                  Create a profile before anyone can verify their identity in this tenant.
                </EmptyState>
              }
            />
          )}
        </AdminQueryView>
      </Card>

      {creating ? <CreateProfileDialog onClose={() => setCreating(false)} /> : null}
      {target ? (
        <R3ActionDialog
          isOpen
          onClose={() => setTarget(null)}
          title={`Activate ${target.name} v${target.version}`}
          intent={<p>This version becomes the active profile for its name. Any previously active version retires; new verifications use this one.</p>}
          scope={[
            ["Profile", `${target.name} v${target.version}`],
            ["Level", target.level],
            ["Jurisdictions", target.jurisdictions?.join(", ") || "—"],
          ]}
          reversibility="reversible-with-effort"
          dependents={[{ label: "Previously active version of this profile", effect: "Retired" }]}
          commitLabel="Activate profile"
          run={async () => {
            const r = await activate.mutateAsync(target.profile_id);
            return { outcome: r.status, objectId: r.profile_id };
          }}
        />
      ) : null}
    </>
  );
}
