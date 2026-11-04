"use client";

import type { ApprovalForm } from "@zoiko/api-client";
import { Banner, Button, Dialog, Select, TextField } from "@zoiko/ui";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { useCellMutation, useCellQuery } from "@/lib/cell";

import { LEVEL_WORDS, buildRequest, formProblems, memberChoices, requestHref, type FormValues } from "./logic";
import { Refusal } from "./shared";

/** WF-013C-02: ask for a governed change. The form collects what the request needs and says how many independent people will have to approve it.
 * It decides nothing: the server classifies the action, checks that this person may ask, validates the details and chooses the approvers. */
export function NewRequestDialog({ orgId, isOpen, onOpenChange }: { orgId: string; isOpen: boolean; onOpenChange: (o: boolean) => void }) {
  const router = useRouter();
  const forms = useCellQuery(["org-approvals", orgId, "forms"], (api) => api.org.requestForms(orgId), { enabled: isOpen });
  const members = useCellQuery(["tenant", "members"], (api) => api.tenant.members(), { enabled: isOpen });
  const [actionId, setActionId] = useState("");
  const [values, setValues] = useState<FormValues>({});
  const [reason, setReason] = useState("");
  const [shown, setShown] = useState(false);

  const form: ApprovalForm | undefined = forms.data?.forms.find((f) => f.action_id === actionId);
  const problems = form ? formProblems(form, values, reason) : [];
  const create = useCellMutation((api) => api.org.createRequest(orgId, buildRequest(form as ApprovalForm, values, reason)), {
    invalidate: [["org-approvals"]],
    success: "Request sent for approval",
    onSuccess: (made) => {
      onOpenChange(false);
      router.push(requestHref(orgId, made.case_id));
    },
  });

  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={(o) => {
        onOpenChange(o);
        if (!o) {
          setActionId("");
          setValues({});
          setReason("");
          setShown(false);
          create.reset();
        }
      }}
      title="New request"
      description="Sensitive and critical changes are asked for here. Nothing changes until the people who must approve have done so and the change is applied."
      footer={
        <>
          <Button variant="secondary" className="min-h-11" onPress={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            className="min-h-11"
            isDisabled={!form}
            pending={create.isPending}
            onPress={() => {
              setShown(true);
              if (form && problems.length === 0) create.mutate(undefined);
            }}
          >
            Send for approval
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Select
          label="What do you want to change?"
          value={actionId}
          onChange={(v) => {
            setActionId(v);
            setValues({});
            setShown(false);
            create.reset();
          }}
          options={[{ id: "", label: "Choose a change" }, ...(forms.data?.forms ?? []).map((f) => ({ id: f.action_id, label: f.label }))]}
        />
        {form ? (
          <>
            <p className="text-sm text-ink-muted">{form.summary}</p>
            <p className="text-sm font-medium text-ink">{LEVEL_WORDS[form.level]}</p>
            {form.fields.map((f) =>
              f.kind === "member" || f.kind === "owner" || f.kind === "select" ? (
                <Select
                  key={f.key}
                  label={f.label}
                  value={values[f.key] ?? ""}
                  onChange={(v) => setValues({ ...values, [f.key]: v })}
                  options={[
                    { id: "", label: "Choose" },
                    ...(f.kind === "select" ? f.options : memberChoices(members.data?.items ?? [], f.kind)),
                  ]}
                />
              ) : (
                <TextField
                  key={f.key}
                  label={f.label}
                  value={values[f.key] ?? ""}
                  onChange={(v) => setValues({ ...values, [f.key]: v })}
                  multiline={f.kind === "textarea"}
                  rows={f.kind === "textarea" ? 3 : undefined}
                  description={f.help ?? undefined}
                  isRequired={f.required}
                />
              ),
            )}
            <TextField label="Why are you asking?" value={reason} onChange={setReason} isRequired />
            {shown && problems.length > 0 ? (
              <Banner tone="warn" title="Check the request" live="polite">
                <ul className="list-disc pl-5">
                  {problems.map((p) => (
                    <li key={p}>{p}</li>
                  ))}
                </ul>
              </Banner>
            ) : null}
            {create.error ? <Refusal error={create.error} title="The request was not sent" /> : null}
          </>
        ) : null}
      </div>
    </Dialog>
  );
}
