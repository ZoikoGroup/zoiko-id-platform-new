"use client";

import type { SolutionDraft, SolutionIn } from "@zoiko/api-client";
import { useState } from "react";

import { standing, useTenant } from "@/features/onboarding/shared";
import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { BILLING_KEY } from "./shared";
import { EMPTY_DRAFT, buildBody, type Draft, type StepId } from "./solution-builder";
import { SolutionBuilderView } from "./solution-builder-view";

/** The B06 panel wired to the cell (ZID-COM-WF-2026-11 WF-C02). The server judges every market and builds the preview; this only carries the person's choices. */
export function SolutionBuilderSection() {
  const tenant = useTenant();
  const mayRequest = standing(tenant.data).isAdmin;
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [step, setStep] = useState<StepId>("purpose");
  const [saved, setSaved] = useState<SolutionDraft | undefined>(undefined);

  // The service list and its names come from the offers with no market named: every card is then "choose a market" and carries no judgement.
  const catalogue = useCellQuery([...BILLING_KEY, "builder", "catalogue"], (api) => api.billing.offers());
  const deps = useCellQuery([...BILLING_KEY, "builder", "dependencies"], (api) => api.billing.capabilityDependencies());

  const body = buildBody(draft);
  const wantsPreview = body !== null && (step === "coverage" || step === "review");
  const preview = useCellQuery(
    [...BILLING_KEY, "builder", "preview", JSON.stringify(body)],
    (api) => api.billing.previewQuote(body as NonNullable<typeof body>),
    { enabled: wantsPreview && (draft.subjectCountry.trim() !== "" || draft.payerCountry.trim() !== "") },
  );

  const save = useCellMutation((api, b: SolutionIn) => api.billing.saveConfiguration(b), {
    invalidate: [[...BILLING_KEY, "builder"]],
    success: "Draft saved",
    onSuccess: (d) => setSaved(d),
  });
  const request = useCellMutation((api, id: string) => api.billing.requestQuote(id), {
    success: "Sent to the deal desk",
    onSuccess: (d) => setSaved(d),
  });

  // Changing anything after a save makes the saved draft stale: it is saved again, never edited.
  const onDraft = (d: Draft) => {
    setDraft(d);
    setSaved(undefined);
  };

  return (
    <QueryView query={catalogue}>
      {(offers) => (
        <>
          <SolutionBuilderView
            catalogue={offers.cards}
            deps={deps.data}
            draft={draft}
            onDraft={onDraft}
            step={step}
            onStep={setStep}
            preview={preview.data}
            previewPending={preview.isFetching}
            previewError={preview.error ? "Coverage could not be checked right now. Try again." : null}
            saved={saved}
            onSave={() => body && save.mutate(body)}
            onRequest={() => saved && request.mutate(saved.draft_id)}
            saving={save.isPending}
            requesting={request.isPending}
            mayRequest={mayRequest}
          />
          {save.error ? <ApiErrorView error={save.error} /> : null}
          {request.error ? <ApiErrorView error={request.error} /> : null}
        </>
      )}
    </QueryView>
  );
}
