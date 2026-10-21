"use client";

import type { SignalAccepted, SignalIn, SubjectType } from "@zoiko/api-client";
import { Banner, Button, Card, CopyId, KeyValues, RevisionChip, Select, StatusBadge, TextField } from "@zoiko/ui";
import { FlaskConical, Send } from "lucide-react";
import { useState, type FormEvent } from "react";

import { ApiErrorView, newIdempotencyKey, useCellMutation } from "@/lib/cell";

import { CANONICAL_EVENTS, isUuid, SUBJECT_TYPES } from "./shared";

export function PublishSignalSection() {
  const [eventType, setEventType] = useState<string>("risk.elevated");
  const [type, setType] = useState<SubjectType>("PRINCIPAL");
  const [subjectId, setSubjectId] = useState("");
  const [sourceEventId, setSourceEventId] = useState(() => newIdempotencyKey());
  const [effectiveAt, setEffectiveAt] = useState("");
  const [invalid, setInvalid] = useState<string | undefined>();
  const [result, setResult] = useState<SignalAccepted | null>(null);

  const publish = useCellMutation((api, body: SignalIn) => api.signals.publish(body), {
    success: (r) => `Signal ${r.status.toLowerCase()}`,
    onSuccess: (r) => {
      setResult(r);
      setSourceEventId(newIdempotencyKey());
    },
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!isUuid(subjectId)) return setInvalid("Enter the subject's UUID.");
    setInvalid(undefined);
    setResult(null);
    publish.mutate({
      event_type: eventType,
      subject_type: type,
      subject_id: subjectId.trim(),
      source_event_id: sourceEventId.trim(),
      effective_at: effectiveAt ? new Date(effectiveAt).toISOString() : null,
    });
  };

  return (
    <div className="space-y-5">
      <Banner tone="warn" icon={FlaskConical} title="For testing signal rules">
        Publishing runs the real rules: a signal can revoke sessions or credentials of the subject. It needs a token with the{" "}
        <code className="font-mono">signals:publish</code> scope.
      </Banner>
      <Card title="Publish a canonical signal" description="Acts as a trusted internal source. Re-using a source event ID is de-duplicated.">
        <form onSubmit={submit} className="grid gap-4 sm:grid-cols-2">
          <Select
            label="Event type"
            isRequired
            options={CANONICAL_EVENTS.map((e) => ({ id: e, label: e }))}
            value={eventType}
            onChange={setEventType}
          />
          <Select label="Subject type" isRequired options={SUBJECT_TYPES} value={type} onChange={(v) => setType(v as SubjectType)} />
          <TextField label="Subject ID" isRequired value={subjectId} onChange={setSubjectId} mono errorMessage={invalid} className="sm:col-span-2" />
          <TextField
            label="Source event ID"
            isRequired
            value={sourceEventId}
            onChange={setSourceEventId}
            mono
            description="Idempotency key for this event at its source; a fresh one is generated after each publish."
          />
          <TextField
            label="Effective at (optional)"
            type="datetime-local"
            value={effectiveAt}
            onChange={setEffectiveAt}
            description="Your local time; defaults to now."
          />
          {publish.error ? (
            <div className="sm:col-span-2">
              <ApiErrorView error={publish.error} />
            </div>
          ) : null}
          <div className="flex justify-end sm:col-span-2">
            <Button type="submit" pending={publish.isPending} isDisabled={!subjectId.trim() || !sourceEventId.trim()} icon={<Send aria-hidden className="size-4" />}>
              Publish signal
            </Button>
          </div>
        </form>
      </Card>
      {result ? (
        <Card title="Result">
          <KeyValues
            columns={4}
            items={[
              ["Outcome", <StatusBadge key="o" state={result.status} kind="Signal" />],
              ["Resulting state", <StatusBadge key="s" state={result.state} kind="Security state" />],
              ["Revision", <RevisionChip key="r" kind="Security" value={result.revision} />],
              ["Signal", result.signal_id ? <CopyId key="id" value={result.signal_id} label="signal ID" /> : null],
            ]}
          />
        </Card>
      ) : null}
    </div>
  );
}
