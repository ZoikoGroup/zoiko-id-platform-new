"use client";

import { Badge, ButtonLink, Card, LoadingRows } from "@zoiko/ui";
import { Check, Lock } from "lucide-react";

import { MY_IDV_KEY } from "@/features/account/idv/logic";
import { useTenant } from "@/features/onboarding/shared";
import { useCellQuery } from "@/lib/cell";

import { companyVerified, identityApproved, journey, nextStep, type JourneyState } from "./journey-logic";

const BADGE: Record<JourneyState, { tone: "ok" | "info" | "neutral"; text: string }> = {
  done: { tone: "ok", text: "Done" },
  current: { tone: "info", text: "Next" },
  locked: { tone: "neutral", text: "Waiting" },
};

/** The three steps from sign-up to onboarding, with where each stands and one button for the step to do now. It reads what the server decided; it decides nothing. */
export function VerificationJourney() {
  const idv = useCellQuery(MY_IDV_KEY, (api) => api.idv.cases());
  const tenant = useTenant();

  if ((idv.isPending && idv.fetchStatus !== "idle") || (tenant.isPending && tenant.fetchStatus !== "idle")) {
    return (
      <Card title="Get your company verified">
        <LoadingRows rows={3} />
      </Card>
    );
  }

  const steps = journey({
    identityDone: identityApproved(idv.data?.items ?? []),
    companyVerified: companyVerified(tenant.data?.status),
  });
  const next = nextStep(steps);

  return (
    <Card title="Get your company verified" description="Three steps, in this order. Each one opens when the one before it is done.">
      <ol className="space-y-3" aria-label="Verification steps">
        {steps.map((s, i) => (
          <li
            key={s.id}
            aria-current={next?.id === s.id ? "step" : undefined}
            className={`flex flex-wrap items-center gap-3 rounded-card border p-3 ${next?.id === s.id ? "border-brand-500 bg-brand-50/40" : "border-border"}`}
          >
            <span
              aria-hidden
              className={`flex size-7 shrink-0 items-center justify-center rounded-full text-sm font-semibold ${s.state === "done" ? "bg-ok-bg text-ok-fg" : "bg-sunken text-ink-muted"}`}
            >
              {s.state === "done" ? <Check className="size-4" /> : s.state === "locked" ? <Lock className="size-3.5" /> : i + 1}
            </span>
            <div className="min-w-0 flex-1">
              <p className="font-medium text-ink">
                {s.title} <Badge size="xs" tone={BADGE[s.state].tone}>{BADGE[s.state].text}</Badge>
              </p>
              <p className="text-sm text-ink-muted">{s.summary}</p>
            </div>
            {s.href ? (
              <ButtonLink href={s.href} size="sm" variant={next?.id === s.id ? "primary" : "secondary"}>
                {s.action}
              </ButtonLink>
            ) : null}
          </li>
        ))}
      </ol>
    </Card>
  );
}
