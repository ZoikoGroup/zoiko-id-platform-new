"use client";

import { ApiError, type OrgClaimAppealKind, type OrgClaimStatus } from "@zoiko/api-client";
import { Banner, Button, ButtonLink, Card, Select, TextField } from "@zoiko/ui";
import { CheckCircle2, MessageSquareWarning } from "lucide-react";
import { useState } from "react";

import { useCellMutation } from "@/lib/cell";

import { APPEAL_LABELS, MAX_STATEMENT, appealKinds, appealMessage, statementProblem } from "./org-claim";

/** WF-13B to WF-13C: a VERIFIED claim (all four checks complete) is turned into the organization, named by the register, with the claimant as its owner. Nothing
 * but a VERIFIED outcome offers this, and the server refuses it otherwise. */
export function EstablishCard({ claimId, status }: { claimId: string; status: OrgClaimStatus }) {
  const establish = useCellMutation((api) => api.orgClaims.establish(claimId), {
    invalidate: [["org-claims"], ["tenant"], ["onboarding"]],
    success: "Your organization is established.",
  });
  if (status.outcome !== "VERIFIED") return null;
  return (
    <Card title="Your organization is verified" description="All four checks are complete. Create the organization from the official register record.">
      {establish.data ? (
        <div className="space-y-3">
          <Banner tone="ok" icon={CheckCircle2} title={`${establish.data.legal_name ?? "Your organization"} is established`}>
            You are now its owner. Finish the setup next.
          </Banner>
          <ButtonLink className="min-h-11" href="/organizations/current/setup">
            Continue to organization setup
          </ButtonLink>
        </div>
      ) : (
        <div className="space-y-3">
          <Button className="min-h-11" isDisabled={establish.isPending} onPress={() => establish.mutate(undefined as never)}>
            Create the organization
          </Button>
          {establish.isError ? (
            <p role="alert" className="text-sm text-danger-fg">
              {establish.error instanceof ApiError ? establish.error.message : "It could not be created. Try again."}
            </p>
          ) : null}
        </div>
      )}
    </Card>
  );
}

/** What the customer can do next, as the screening named it (ZID-ED-04 section 04: missing evidence is an explicit task). The
 * tasks are words and counts; nothing here ever names a person or says what a watchlist held. */
export function TasksCard({ claimId, status }: { claimId: string; status: OrgClaimStatus }) {
  const tasks = status.screening?.tasks ?? [];
  if (tasks.length === 0) return null;
  return (
    <Card title="What we need from you" description="Complete these so the checks can move on.">
      <ul className="space-y-3" aria-label="Tasks">
        {tasks.map((t) => (
          <li key={t.code} className="text-sm text-ink">
            {t.text}
          </li>
        ))}
      </ul>
      <div className="mt-4">
        <ButtonLink className="min-h-11" href={`/admin/organization/claim/${encodeURIComponent(claimId)}/authority`}>
          Update owners and controllers
        </ButtonLink>
      </div>
    </Card>
  );
}

/** ZID-ED-04 section 06, decision fairness: a person can appeal a rejection or ask for a correction and a different person looks
 * again. What is written goes to a reviewer only. */
export function AppealCard({ claimId, status }: { claimId: string; status: OrgClaimStatus }) {
  const kinds = appealKinds(status.screening?.status);
  const [kind, setKind] = useState<OrgClaimAppealKind>(kinds[0] ?? "CORRECTION");
  const [statement, setStatement] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [sent, setSent] = useState(false);
  const send = useCellMutation((api, v: { kind: OrgClaimAppealKind; statement: string }) => api.orgClaims.appeal(claimId, v), {
    invalidate: [["org-claims", claimId]],
    onSuccess: () => {
      setSent(true);
      setStatement("");
    },
  });
  if (kinds.length === 0) return null;
  const chosen = kinds.includes(kind) ? kind : kinds[0]!;
  const server = send.error instanceof ApiError ? appealMessage(send.error.code) : send.error ? appealMessage(undefined) : null;
  const shown = problem ?? server;

  return (
    <Card title="Think we got something wrong?" description="Ask for a correction, or appeal a decision. A person reads what you write and a different person decides.">
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          const p = statementProblem(statement);
          setProblem(p);
          setSent(false);
          if (!p) send.mutate({ kind: chosen, statement: statement.trim() });
        }}
      >
        {kinds.length > 1 ? (
          <Select label="What would you like to do?" value={chosen} onChange={(v) => setKind(v as OrgClaimAppealKind)} options={kinds.map((k) => ({ id: k, label: APPEAL_LABELS[k].label }))} />
        ) : (
          <p className="text-sm font-medium text-ink">{APPEAL_LABELS[chosen].label}</p>
        )}
        <p className="text-sm text-ink-muted">{APPEAL_LABELS[chosen].help}</p>
        <TextField
          label="What should we know?"
          multiline
          rows={5}
          value={statement}
          onChange={(v) => {
            setStatement(v);
            setProblem(null);
            setSent(false);
          }}
          description={`Written for the reviewer only. Up to ${MAX_STATEMENT} characters.`}
          isRequired
        />
        <Button type="submit" className="min-h-11" pending={send.isPending} isDisabled={statement.trim().length === 0} icon={<MessageSquareWarning aria-hidden className="size-4" />}>
          Send to a reviewer
        </Button>
      </form>
      <div role="status" aria-live="polite" className="mt-3">
        {sent ? (
          <Banner tone="ok" title="Sent">
            A person will look at this. We will tell you if we need anything else.
          </Banner>
        ) : null}
        {shown ? (
          <Banner tone="warn" title="That was not sent">
            {shown}
          </Banner>
        ) : null}
      </div>
    </Card>
  );
}
