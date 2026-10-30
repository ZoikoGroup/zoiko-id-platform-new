"use client";

import type { OrgClaimCandidate, OrgClaimStatus } from "@zoiko/api-client";
import { Badge, Banner, Button, ButtonLink, Card, DateTime, PageHeader, Select, TextField } from "@zoiko/ui";
import { Building2, Plus, Search, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import {
  CHECKS,
  CHECK_TONE,
  CHECK_WORDS,
  type OwnerRow,
  attemptId,
  buildScreening,
  claimKey,
  emptyOwner,
  nextStep,
  outcomeCopy,
  ownerRowErrors,
  tidy,
} from "./org-claim";

/** Countries are discoverable everywhere (EDR-01); whether one can be verified is the server's answer, shown as it is given. */
const COUNTRIES = [
  { id: "GB", label: "United Kingdom" },
  { id: "US", label: "United States" },
  { id: "IE", label: "Ireland" },
  { id: "DE", label: "Germany" },
  { id: "FR", label: "France" },
];

const claimBase = (id: string) => `/admin/organization/claim/${encodeURIComponent(id)}`;

/** WF-13B-01 Find organization. The country is where the company is incorporated, not where you live. */
export function FindOrganizationPage() {
  const router = useRouter();
  const [attempt] = useState(attemptId);
  const [country, setCountry] = useState("GB");
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState<OrgClaimCandidate | null>(null);
  const search = useCellMutation((api, v: { country: string; query: string }) => api.orgClaims.search(v.country, v.query));
  const create = useCellMutation(
    (api, c: OrgClaimCandidate) =>
      api.orgClaims.create(
        { country: c.jurisdiction, registration_number: c.registration_number, legal_name: c.legal_name },
        claimKey(c.jurisdiction, c.registration_number, attempt),
      ),
    { onSuccess: (s: OrgClaimStatus) => router.push(claimBase(s.claim_id) + "/status") },
  );
  const found = search.data?.candidates ?? [];

  return (
    <div className="space-y-6">
      <PageHeader
        icon={Building2}
        title="Find your organization"
        description="Choose the company's country of incorporation and search an official source. Finding a company does not verify it."
        breadcrumbs={[{ label: "Company verification", href: "/admin/organization" }, { label: "Find your organization" }]}
      />
      <Card title="1. Search" description="Search by the exact registration number or the legal name.">
        <form
          className="grid gap-4 sm:grid-cols-[14rem_1fr_auto] sm:items-end"
          onSubmit={(e) => {
            e.preventDefault();
            setPicked(null);
            if (tidy(query).length >= 2) search.mutate({ country, query: tidy(query) });
          }}
        >
          <Select label="Country of incorporation" value={country} onChange={setCountry} options={COUNTRIES} isRequired />
          <TextField label="Registration number or legal name" value={query} onChange={setQuery} isRequired />
          <Button type="submit" pending={search.isPending} isDisabled={tidy(query).length < 2} icon={<Search aria-hidden className="size-4" />}>
            Search
          </Button>
        </form>
        {search.error ? (
          <div className="mt-4">
            <ApiErrorView error={search.error} title="The search could not be run" />
          </div>
        ) : null}
      </Card>
      {search.data ? (
        <Card
          title="2. Choose your company"
          description="The closest matches are listed first. Nothing is chosen for you, and a match is not a verification."
        >
          {found.length === 0 ? (
            <Banner tone="info" title="We couldn't confirm a matching record">
              Try another legal name or number, or continue with manual review. This does not mean the company does not exist.
            </Banner>
          ) : (
            <ul className="divide-y divide-line rounded-lg border border-line" aria-label="Matching companies">
              {found.map((c) => {
                const selected = picked?.registration_number === c.registration_number;
                return (
                  <li key={`${c.jurisdiction}-${c.registration_number}`} className="flex flex-wrap items-center gap-3 p-3">
                    <div className="min-w-0 flex-1">
                      <p className="font-medium text-ink">{c.legal_name}</p>
                      <p className="text-xs text-ink-muted">
                        <span className="font-mono">{c.registration_number}</span> · {c.jurisdiction} · {c.status || "status unknown"} · from the{" "}
                        {c.register.replace(/-/g, " ")} record
                      </p>
                    </div>
                    <Badge tone={selected ? "brand" : "neutral"}>{selected ? "Selected" : "Match"}</Badge>
                    <Button variant={selected ? "primary" : "secondary"} size="sm" onPress={() => setPicked(c)}>
                      {selected ? "Selected" : "Select"}
                    </Button>
                  </li>
                );
              })}
            </ul>
          )}
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <Button isDisabled={!picked} pending={create.isPending} onPress={() => picked && create.mutate(picked)}>
              Continue with the selected company
            </Button>
            <span className="text-xs text-ink-muted">This opens a provisional claim: it is not a verified organization.</span>
          </div>
          {create.error ? (
            <div className="mt-4">
              <ApiErrorView error={create.error} title="The claim could not be opened" />
            </div>
          ) : null}
        </Card>
      ) : null}
    </div>
  );
}

function OwnerEditor({ owner, index, onChange, onRemove }: { owner: OwnerRow; index: number; onChange: (o: OwnerRow) => void; onRemove: () => void }) {
  const errors = owner.name || owner.sharesPct || owner.votesPct ? ownerRowErrors(owner) : [];
  return (
    <fieldset className="space-y-3 rounded-lg border border-line p-3">
      <legend className="px-1 text-sm font-medium text-ink">Owner or controller {index + 1}</legend>
      <div className="grid gap-3 sm:grid-cols-2">
        <TextField label="Full legal name" value={owner.name} onChange={(name) => onChange({ ...owner, name })} isRequired />
        <TextField label="Birth year (optional)" value={owner.birthYear} onChange={(birthYear) => onChange({ ...owner, birthYear })} inputMode="numeric" />
        <TextField label="Share of the company, %" value={owner.sharesPct} onChange={(sharesPct) => onChange({ ...owner, sharesPct })} inputMode="decimal" />
        <TextField label="Share of the votes, %" value={owner.votesPct} onChange={(votesPct) => onChange({ ...owner, votesPct })} inputMode="decimal" />
      </div>
      <label className="flex items-center gap-2 text-sm text-ink">
        <input type="checkbox" checked={owner.boardControl} onChange={(e) => onChange({ ...owner, boardControl: e.target.checked })} />
        Can appoint or remove most of the board
      </label>
      {errors.length > 0 ? (
        <ul className="space-y-1 text-xs font-medium text-danger-fg" role="alert">
          {errors.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      ) : null}
      <Button variant="secondary" size="sm" onPress={onRemove} icon={<Trash2 aria-hidden className="size-4" />}>
        Remove
      </Button>
    </fieldset>
  );
}

/** WF-13B-02 Establish representative authority. What you declare here is a claim for review, never proof. */
export function AuthorityPage({ claimId }: { claimId: string }) {
  const router = useRouter();
  const status = useCellQuery(["org-claims", claimId], (api) => api.orgClaims.status(claimId));
  const [name, setName] = useState("");
  const [owners, setOwners] = useState<OwnerRow[]>([emptyOwner()]);
  const submit = useCellMutation((api) => api.orgClaims.screening(claimId, buildScreening(name, owners)), {
    invalidate: [["org-claims", claimId]],
    onSuccess: () => router.push(claimBase(claimId) + "/status"),
  });
  const ownersOk = owners.every((o) => ownerRowErrors(o).length === 0);
  const ok = tidy(name).length > 1 && ownersOk && owners.length > 0;

  return (
    <div className="space-y-6">
      <PageHeader
        icon={Building2}
        title="Your authority and the company's owners"
        description="Tell us who you are acting as and who owns or controls the company. We check this; your word alone does not verify anything."
        breadcrumbs={[{ label: "Company verification", href: "/admin/organization" }, { label: "Authority" }]}
      />
      <QueryView query={status}>
        {(s) => (
          <>
            <Banner tone="info" title="Company on this claim">
              <span className="font-mono">{s.registration_number}</span> ({s.jurisdiction}). Your authority is established from the register
              and the authority held for you, never from this form.
            </Banner>
            <form
              className="space-y-6"
              onSubmit={(e) => {
                e.preventDefault();
                if (ok) submit.mutate(undefined);
              }}
            >
              <Card title="You" description="Your full legal name, as on your identity document.">
                <TextField label="Your full legal name" value={name} onChange={setName} isRequired />
              </Card>
              <Card title="Owners and controllers" description="Anyone who owns or controls the company in the way your country's rules count.">
                <div className="space-y-4">
                  {owners.map((o, i) => (
                    <OwnerEditor
                      key={i}
                      owner={o}
                      index={i}
                      onChange={(next) => setOwners(owners.map((x, j) => (j === i ? next : x)))}
                      onRemove={() => setOwners(owners.length > 1 ? owners.filter((_, j) => j !== i) : [emptyOwner()])}
                    />
                  ))}
                  <Button variant="secondary" size="sm" onPress={() => setOwners([...owners, emptyOwner()])} icon={<Plus aria-hidden className="size-4" />}>
                    Add another owner or controller
                  </Button>
                </div>
              </Card>
              <Banner tone="info" title="What happens next">
                We screen the people you list against the sanctions lists the country requires and review the ownership. You will see only the
                outcome and the next step, never the lists. Holdings you declare are checked, not accepted.
              </Banner>
              <Button type="submit" pending={submit.isPending} isDisabled={!ok}>
                Submit for checking
              </Button>
              {submit.error ? <ApiErrorView error={submit.error} title="The details could not be submitted" /> : null}
            </form>
          </>
        )}
      </QueryView>
    </div>
  );
}

/** WF-13B-03 Review and track. Each check has its own state, so "company found" can sit beside "authority pending". */
export function VerificationStatusPage({ claimId }: { claimId: string }) {
  const status = useCellQuery(["org-claims", claimId], (api) => api.orgClaims.status(claimId));
  const refresh = useCellMutation((api) => api.orgClaims.refresh(claimId), { invalidate: [["org-claims", claimId]] });

  return (
    <div className="space-y-6">
      <PageHeader
        icon={Building2}
        title="Verification status"
        description="Four checks, each decided on its own evidence."
        breadcrumbs={[{ label: "Company verification", href: "/admin/organization" }, { label: "Status" }]}
      />
      <QueryView query={status}>
        {(s) => {
          const out = outcomeCopy(s.outcome);
          const step = nextStep(s);
          return (
            <div className="space-y-6">
              <Banner tone={out.tone} title={out.title}>
                {out.body}
              </Banner>
              <Card title="Checks">
                <ul className="divide-y divide-line" aria-label="Verification checks">
                  {CHECKS.map((c) => (
                    <li key={c.key} className="flex flex-wrap items-start gap-3 py-3">
                      <div className="min-w-0 flex-1">
                        <p className="font-medium text-ink">{c.label}</p>
                        <p className="text-xs text-ink-muted">{c.help}</p>
                      </div>
                      <Badge tone={CHECK_TONE[s.checks[c.key]]}>{CHECK_WORDS[s.checks[c.key]]}</Badge>
                    </li>
                  ))}
                </ul>
              </Card>
              {s.company.expires_at ? (
                <p className="text-sm text-ink-muted">
                  The company check stands until <DateTime value={s.company.expires_at} />, then it is refreshed.
                </p>
              ) : null}
              {s.screening ? <p className="text-sm text-ink-muted">{s.screening.next_action}</p> : null}
              <div className="flex flex-wrap gap-3">
                {step === "authority" ? <ButtonLink href={claimBase(s.claim_id) + "/authority"}>Add your authority and owners</ButtonLink> : null}
                <Button variant="secondary" pending={refresh.isPending} onPress={() => refresh.mutate(undefined)}>
                  Check again
                </Button>
              </div>
              {refresh.error ? <ApiErrorView error={refresh.error} title="The check could not be refreshed" /> : null}
            </div>
          );
        }}
      </QueryView>
    </div>
  );
}
