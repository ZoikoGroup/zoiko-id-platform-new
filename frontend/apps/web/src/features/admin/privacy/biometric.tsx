"use client";

import type { BiometricMethodAnswer } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, EmptyState, Select, TextField } from "@zoiko/ui";
import { CheckCircle2, ScanFace, XCircle } from "lucide-react";
import { useState } from "react";

import { QueryView, useCellQuery } from "@/lib/cell";

import { PRIVACY_KEY } from "./shared";

/** Biometric readiness (ZID-DTV-WF-04 B11, section 19; CR-107, CR-108): for one country and purpose, whether this organization's live sessions may
 * use face match and liveness, and every gate that stops them, in words. Read-only: it changes nothing and says nothing about any person. */

const COUNTRIES = ["GB", "IE", "DE", "FR", "ES", "IT", "NL", "US", "CA", "AU", "IN", "AE", "SG"].map((c) => ({ id: c, label: c }));

/** What each refusal means and who can fix it. A code not listed is shown as is. */
export const REASON_HELP: Record<string, string> = {
  ENGINE_NOT_ACCEPTED: "No face or liveness engine is accepted in this deployment. Zoiko's engine owners must record an independent evaluation first.",
  ENGINE_GATE_INVALID: "An engine is switched on but its evaluation record is incomplete. Zoiko's engine owners must complete it.",
  METHOD_OFF: "Zoiko has not approved this method for this engine version, country and purpose.",
  METHOD_LAB_ONLY: "Approved for laboratory testing only, never for live sessions.",
  METHOD_PAUSED: "Paused by Zoiko (the kill switch). It resumes only after a new approval.",
  METHOD_NOT_IN_ROLLOUT: "This person is outside the current gradual rollout.",
  DPIA_NOT_READY: "Your data protection impact assessment for this method is not approved and ready. See the blockers below and the DPIA tab.",
  PRIVACY_OVERLAY_MISSING: "No approved privacy overlay exists for this country.",
  BIOMETRIC_NOT_PERMITTED_IN_COUNTRY: "This country's overlay does not permit biometric processing.",
  BREACH_CAPTURE_PAUSED: "An open breach case paused biometric capture here. Release it on the Breaches tab once it is safe.",
  TENANT_REQUIRED: "Sign in to an organization to check its methods.",
  EVALUATION_NOT_READY: "This engine version has no current independent evaluation in your model inventory, or it is not the one in service. See the blockers below.",
};

function help(reason: string): string {
  if (reason.startsWith("RETENTION_NOT_SET:")) return `No retention period is set for ${reason.slice(18)}. Publish a retention policy for that object type on the Retention tab.`;
  if (reason.startsWith("USE_CASE_NOT_CERTIFIED:")) return `This Zoiko product's use (${reason.slice(23)}) has no signed integration acceptance yet.`;
  if (reason.startsWith("USE_CASE_NOT_YET_EFFECTIVE:")) return `This Zoiko product's use (${reason.slice(27)}) is signed but not yet in effect.`;
  if (reason.startsWith("OVERLAY_")) return "This country's privacy overlay is not signed by both the DPO and Legal, or has changed since.";
  if (reason.startsWith("ENGINE_")) return REASON_HELP.ENGINE_GATE_INVALID!;
  return REASON_HELP[reason] ?? reason;
}

function Gate({ ok, label, children }: { ok: boolean; label: string; children?: React.ReactNode }) {
  const Icon = ok ? CheckCircle2 : XCircle;
  return (
    <li className="flex items-start gap-2 py-1.5">
      <Icon aria-hidden className={`mt-0.5 size-4 shrink-0 ${ok ? "text-ok-fg" : "text-danger-fg"}`} />
      <span className="text-sm">
        <span className="font-medium">{label}</span>
        <span className="sr-only">{ok ? ": passes" : ": refuses"}</span>
        {children ? <span className="block text-ink-muted">{children}</span> : null}
      </span>
    </li>
  );
}

export function MethodCard({ answer }: { answer: BiometricMethodAnswer }) {
  const has = (prefix: string) => answer.reasons.some((r) => r === prefix || r.startsWith(prefix));
  const engine = !answer.reasons.some((r) => r.startsWith("ENGINE_") || r === "TENANT_REQUIRED");
  return (
    <Card
      title={answer.method === "FACE_MATCH" ? "Face match" : "Liveness"}
      actions={<Badge tone={answer.available ? "ok" : "danger"}>{answer.available ? "Available" : "Not available"}</Badge>}
    >
      <ul aria-label={`${answer.method === "FACE_MATCH" ? "Face match" : "Liveness"} gates`} className="divide-y divide-border">
        <Gate ok={engine} label="Accepted engine">
          {!engine ? help(answer.reasons.find((r) => r.startsWith("ENGINE_") || r === "TENANT_REQUIRED")!) : null}
        </Gate>
        <Gate ok={engine && !has("METHOD_")} label={`Zoiko approval (${answer.state.replaceAll("_", " ").toLowerCase()})`}>
          {answer.reasons.filter((r) => r.startsWith("METHOD_")).map(help).join(" ") ||
            (answer.scope_ref
              ? `Rests on ${answer.scope_ref}.${answer.exposure_percent < 100 ? ` Gradual rollout: runs for ${answer.exposure_percent}% of people.` : ""}`
              : null)}
        </Gate>
        <Gate ok={engine && !has("DPIA_")} label="Your DPIA">
          {answer.dpia_blockers.length ? (
            <span className="mt-1 flex flex-wrap gap-1">
              {answer.dpia_blockers.map((b) => (
                <Badge key={b} tone="warn">
                  {b}
                </Badge>
              ))}
            </span>
          ) : null}
        </Gate>
        <Gate ok={!has("PRIVACY_OVERLAY") && !has("OVERLAY_") && !has("BIOMETRIC_NOT_PERMITTED")} label="Country privacy overlay">
          {answer.reasons.filter((r) => r.startsWith("OVERLAY_") || r === "PRIVACY_OVERLAY_MISSING" || r === "BIOMETRIC_NOT_PERMITTED_IN_COUNTRY").map(help)[0] ??
            (answer.consent_required ? "Consent is required: each person is asked when their session starts." : null)}
        </Gate>
        <Gate ok={!answer.breaches.length} label="No breach pause">
          {answer.breaches.length ? `${help("BREACH_CAPTURE_PAUSED")} Cases: ${answer.breaches.join(", ")}` : null}
        </Gate>
        <Gate ok={engine && !has("EVALUATION_")} label="Independent evaluation of this engine version">
          {answer.evaluation_blockers.length ? (
            <span className="mt-1 flex flex-wrap gap-1">
              {answer.evaluation_blockers.map((b) => (
                <Badge key={b} tone="warn">
                  {b}
                </Badge>
              ))}
            </span>
          ) : answer.evaluation_id ? (
            `Evaluation ${answer.evaluation_id.slice(0, 8)}…`
          ) : null}
        </Gate>
        {answer.reasons.some((r) => r.startsWith("USE_CASE_")) ? (
          <Gate ok={false} label="Zoiko product use case">
            {answer.reasons.filter((r) => r.startsWith("USE_CASE_")).map(help).join(" ")}
          </Gate>
        ) : null}
        <Gate ok={engine && !answer.missing_retention.length} label="Retention for every biometric asset class">
          {answer.missing_retention.length ? (
            <span className="mt-1 flex flex-wrap gap-1">
              {answer.missing_retention.map((c) => (
                <Badge key={c} tone="warn">
                  <span className="font-mono">{c}</span>
                </Badge>
              ))}
            </span>
          ) : null}
        </Gate>
      </ul>
    </Card>
  );
}

export function BiometricSection() {
  const [country, setCountry] = useState("GB");
  const [purpose, setPurpose] = useState("IDENTITY_VERIFICATION");
  const [asked, setAsked] = useState<{ country: string; purpose: string } | null>(null);
  const q = useCellQuery([...PRIVACY_KEY, "biometric", asked?.country, asked?.purpose], (api) => api.verification.biometricEligibility(asked!.country, asked!.purpose), {
    enabled: asked !== null,
  });
  return (
    <div className="space-y-5">
      <Card
        title="Biometric readiness"
        description="Whether your live sessions may use face match and liveness for a country and purpose. Nothing biometric runs unless every gate passes; nothing here changes a setting."
      >
        <form
          className="flex flex-wrap items-end gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            setAsked({ country, purpose: purpose.trim().toUpperCase() });
          }}
        >
          <Select label="Country" value={country} onChange={setCountry} options={COUNTRIES} />
          <TextField label="Purpose" value={purpose} onChange={setPurpose} mono />
          <Button type="submit" variant="secondary">
            Check
          </Button>
        </form>
      </Card>
      {asked ? (
        <QueryView query={q}>
          {(d) => (
            <div className="space-y-4">
              {d.available_methods.length ? (
                <Banner tone="ok" title={`Available in ${d.country}: ${d.available_methods.map((m) => (m === "FACE_MATCH" ? "face match" : "liveness")).join(", ")}`}>
                  Availability is permission to try, never a result: each capture must still pass.
                </Banner>
              ) : (
                <Banner tone="warn" title={`No biometric method is available in ${d.country} for ${d.purpose}`}>
                  Sessions there use the document or chip alone, or an assisted route.
                </Banner>
              )}
              <div className="grid gap-4 lg:grid-cols-2">
                {d.methods.map((m) => (
                  <MethodCard key={m.method} answer={m} />
                ))}
              </div>
              <p className="text-xs text-ink-subtle">
                Policy hash <span className="font-mono">{d.policy_hash.slice(0, 16)}…</span> · {d.notes.join(" ")}
              </p>
            </div>
          )}
        </QueryView>
      ) : (
        <EmptyState icon={ScanFace} title="Choose a country and purpose">
          The check reads Zoiko&apos;s approvals and your own DPIA, retention and breach records.
        </EmptyState>
      )}
    </div>
  );
}
