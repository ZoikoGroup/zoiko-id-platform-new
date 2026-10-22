"use client";

import type { RelianceDecision, RelianceIn, ReviewIn, ReviewOutcome } from "@zoiko/api-client";
import { Banner, Button, Card, CopyId, KeyValues, RadioCards, RiskBadge, Select, StatusBadge, TextField } from "@zoiko/ui";
import { Gavel, Scale } from "lucide-react";
import { useState, type FormEvent } from "react";

import { listInput, useCellMutation } from "@/lib/cell";

import { AdminErrorView, required, uuidInput } from "../identity-providers/shared";

type Decision = ReviewIn["decision"];
type Level = RelianceIn["required_level"];

/** Manual review: the backend has no list of open cases, so the reviewer enters the case ID. */
export function ReviewDecisionPanel() {
  const [caseId, setCaseId] = useState("");
  const [decision, setDecision] = useState<Decision>("APPROVE");
  const [reasons, setReasons] = useState("");
  const [localError, setLocalError] = useState<unknown>(null);
  const [result, setResult] = useState<ReviewOutcome | null>(null);

  const decide = useCellMutation((api, v: { caseId: string; body: ReviewIn }) => api.verification.decideReview(v.caseId, v.body), {
    success: (r) => `Review decision recorded: ${r.outcome}`,
    onSuccess: (r) => setResult(r),
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    setLocalError(null);
    setResult(null);
    decide.reset();
    try {
      const id = uuidInput(caseId, "Case ID");
      const codes = listInput(reasons);
      if (!codes.length) throw new Error("Add at least one reason code.");
      if (codes.length > 10) throw new Error("At most 10 reason codes.");
      decide.mutate({ caseId: id, body: { decision, reason_codes: codes } });
    } catch (err) {
      setLocalError(err);
    }
  }

  return (
    <Card
      title={
        <span className="flex items-center gap-2">
          <Gavel aria-hidden className="size-4 text-ink-muted" /> Manual review decision
        </span>
      }
      description="Decide an open review case. You cannot decide a case about yourself, and profiles may need more than one approving reviewer."
      actions={<RiskBadge risk="R2" compact />}
    >
      <form onSubmit={submit} noValidate className="space-y-4">
        <TextField label="Review case ID" isRequired mono value={caseId} onChange={setCaseId} placeholder="00000000-0000-0000-0000-000000000000" />
        <RadioCards
          label="Decision"
          columns={2}
          value={decision}
          onChange={(v) => setDecision(v as Decision)}
          options={[
            { id: "APPROVE", label: "Approve", description: "Counts toward the approvals the profile requires." },
            { id: "REJECT", label: "Reject", description: "Closes the case; the verification fails." },
            { id: "REQUEST_MORE_EVIDENCE", label: "Request more evidence", description: "The subject is asked for more." },
            { id: "ESCALATE", label: "Escalate", description: "Closes this case as escalated." },
          ]}
        />
        <TextField
          label="Reason codes"
          isRequired
          mono
          value={reasons}
          onChange={setReasons}
          placeholder="DOCUMENT_MATCHES, FACE_MATCH_CONFIRMED"
          description="Comma separated, 1–10. Recorded in the evidence trail."
        />
        {localError ?? decide.error ? <AdminErrorView error={localError ?? decide.error} /> : null}
        {result ? (
          <Banner tone="ok" title="Decision recorded" live="polite">
            Case <CopyId value={result.case_id} label="review case" /> is now <StatusBadge state={result.outcome} kind="Review" />.
          </Banner>
        ) : null}
        <div className="flex justify-end">
          <Button type="submit" pending={decide.isPending}>
            Record decision
          </Button>
        </div>
      </form>
    </Card>
  );
}

/** Ask whether existing verification can be relied on for a purpose, without re-verifying. */
export function RelianceDecisionPanel() {
  const [principalId, setPrincipalId] = useState("");
  const [purpose, setPurpose] = useState("");
  const [level, setLevel] = useState<Level>("IAL-Z2");
  const [claims, setClaims] = useState("");
  const [jurisdiction, setJurisdiction] = useState("");
  const [localError, setLocalError] = useState<unknown>(null);
  const [result, setResult] = useState<RelianceDecision | null>(null);

  const rely = useCellMutation((api, body: RelianceIn) => api.verification.rely(body), {
    onSuccess: (r) => setResult(r),
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    setLocalError(null);
    setResult(null);
    rely.reset();
    try {
      const c = listInput(claims);
      if (!c.length) throw new Error("Add at least one claim.");
      if (c.length > 20) throw new Error("At most 20 claims.");
      const j = jurisdiction.trim().toUpperCase();
      if (j && !/^[A-Z]{2}$/.test(j)) throw new Error("Jurisdiction must be a two-letter country code.");
      rely.mutate({
        subject_principal_id: uuidInput(principalId, "Subject principal ID"),
        purpose: required(purpose, "Purpose"),
        required_level: level,
        claims: c,
        jurisdiction: j || null,
      });
    } catch (err) {
      setLocalError(err);
    }
  }

  return (
    <Card
      title={
        <span className="flex items-center gap-2">
          <Scale aria-hidden className="size-4 text-ink-muted" /> Reliance decision
        </span>
      }
      description="Check whether a principal's existing, fresh verification meets a level for a purpose. The decision is recorded; nothing is re-verified."
      actions={<RiskBadge risk="R2" compact />}
    >
      <form onSubmit={submit} noValidate className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <TextField label="Subject principal ID" isRequired mono value={principalId} onChange={setPrincipalId} className="sm:col-span-2" />
          <TextField label="Purpose" isRequired mono value={purpose} onChange={setPurpose} placeholder="account_opening" />
          <Select
            label="Required level"
            isRequired
            value={level}
            onChange={(v) => setLevel(v as Level)}
            options={[
              { id: "IAL-Z1", label: "IAL-Z1" },
              { id: "IAL-Z2", label: "IAL-Z2" },
              { id: "IAL-Z3", label: "IAL-Z3" },
            ]}
          />
          <TextField label="Claims" isRequired mono value={claims} onChange={setClaims} placeholder="given_name, birthdate" description="Comma separated." />
          <TextField label="Jurisdiction (optional)" mono value={jurisdiction} onChange={setJurisdiction} placeholder="GB" />
        </div>
        <p className="text-xs text-ink-muted">
          Needs the <span className="font-mono">verification:execute</span> OAuth scope on this app&apos;s token.
        </p>
        {localError ?? rely.error ? <AdminErrorView error={localError ?? rely.error} /> : null}
        {result ? (
          <div role="status" className="space-y-3 rounded-lg border border-border bg-canvas/60 p-4">
            <div className="flex flex-wrap items-center gap-2 text-sm font-semibold text-ink">
              Outcome <StatusBadge state={result.outcome} kind="Reliance" />
            </div>
            <KeyValues
              items={[
                ["Decision", <CopyId key="d" value={result.reliance_decision_id} label="reliance decision" />],
                ["Reason codes", result.reason_codes.length ? <span className="font-mono text-xs">{result.reason_codes.join(", ")}</span> : null],
                [
                  "Assertions relied on",
                  result.assertion_ids.length ? (
                    <span className="flex flex-col gap-1">
                      {result.assertion_ids.map((id) => (
                        <CopyId key={id} value={id} label="assertion" />
                      ))}
                    </span>
                  ) : null,
                ],
              ]}
            />
          </div>
        ) : null}
        <div className="flex justify-end">
          <Button type="submit" pending={rely.isPending}>
            Decide reliance
          </Button>
        </div>
      </form>
    </Card>
  );
}
