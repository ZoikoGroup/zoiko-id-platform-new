"use client";

import type { SubjectSessionStatus } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, Checkbox, PageHeader } from "@zoiko/ui";
import { CheckCircle2, Hand, ScanFace } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

/** The person's own verification session (ZID-DTV-WF-04 B01 notice and consent, B02 alternative route; ZID-PRI-004 section 07; CR-108).
 * - B01: what is checked and why, where it is processed, the exact notice; consent is asked ONLY when the session's lawful basis is consent, and
 *   the box is never pre-ticked. Acknowledging a notice is not consent, and the two are recorded separately.
 * - B02: other ways to verify, with no reason asked: nobody has to disclose a disability or medical condition, and choosing another way never
 *   counts against the person.
 * Nothing here shows or keeps an image, a template or a score. */

const KEY = (ref: string) => ["account", "verification-session", ref];

/** Each server blocker in the words the person sees. */
export const BLOCKER_TEXT: Record<string, string> = {
  NOTICE_NOT_ACKNOWLEDGED: "Read and acknowledge the privacy notice below.",
  PRIVACY_CONSENT_REQUIRED: "This check relies on your consent. You can give it below, or choose another way.",
  SESSION_CANCELED: "You stopped this session. Nothing more will be collected.",
  NON_BIOMETRIC_ALTERNATIVE_CHOSEN: "You chose another way to verify. The camera will not be used.",
  PRIVACY_PROCESSING_RESTRICTED: "You asked us to restrict this processing, so the camera will not be used.",
  PRIVACY_ACTIVITY_NOT_ACTIVE: "This check is not available right now.",
  PRIVACY_PURPOSE_NOT_ACTIVE: "This check is not available right now.",
};

/** Where the assisted route stands, for the person (CR-110). */
export const ASSISTED_TEXT: Record<string, string> = {
  WAITING: "A person will help you verify another way. You will be contacted; nothing else is needed from you now.",
  IN_PROGRESS: "A person is helping you verify another way.",
  DONE: "A person completed your verification another way.",
  NOT_COMPLETED: "A person could not complete it this time. You can try again, or choose another way.",
};

function blockerText(code: string): string {
  return BLOCKER_TEXT[code] ?? "This check cannot use the camera right now.";
}

function language(): string {
  return typeof navigator !== "undefined" && /^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?$/.test(navigator.language) ? navigator.language : "en";
}

function NoticeCard({ s, refreshKey }: { s: SubjectSessionStatus; refreshKey: string[] }) {
  const [agree, setAgree] = useState(false);
  const ack = useCellMutation((api) => api.privacy.v2.acknowledgeNotice(s.session_ref, s.notice!.notice_version, language()), {
    invalidate: [refreshKey],
    success: "Notice acknowledged",
  });
  const consent = useCellMutation((api) => api.privacy.v2.sessionConsent(s.session_ref, s.notice!.notice_version), {
    invalidate: [refreshKey],
    success: "Consent recorded. You can withdraw it at any time from Account › Privacy.",
  });
  const needsConsent = s.blockers.includes("PRIVACY_CONSENT_REQUIRED");
  return (
    <Card title="What we check and why" description="Only the checks this session needs. You can stop at any time.">
      <dl className="grid gap-3 text-sm sm:grid-cols-2">
        <div>
          <dt className="font-medium">Purpose</dt>
          <dd className="font-mono text-xs">{s.purpose_code}</dd>
        </div>
        <div>
          <dt className="font-medium">Where it is processed</dt>
          <dd>{s.region ?? "As stated in the notice"}</dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="font-medium">What may be checked</dt>
          <dd>Your document, that you are present (liveness), and that your face matches the document, only where each is allowed. Nothing is stored as a photo of you unless the notice says so, and only for as long as it says.</dd>
        </div>
      </dl>
      {s.notice ? (
        <div className="mt-4 space-y-3 border-t border-border pt-4">
          <p className="text-sm">
            Privacy notice <span className="font-mono text-xs">{s.notice.notice_version}</span>
            {s.notice.content_uri ? (
              <>
                {" "}
                ·{" "}
                <a className="underline" href={s.notice.content_uri} target="_blank" rel="noreferrer">
                  Read the notice
                </a>
              </>
            ) : null}
          </p>
          {s.notice.acknowledged ? (
            <Badge tone="ok">Notice acknowledged</Badge>
          ) : (
            <Button size="sm" variant="secondary" pending={ack.isPending} onPress={() => ack.mutate(undefined)}>
              I have read the notice
            </Button>
          )}
          {ack.error ? <ApiErrorView error={ack.error} /> : null}
          {needsConsent ? (
            <div className="space-y-2 rounded-[var(--radius-control)] border border-border p-3">
              <Checkbox isSelected={agree} onChange={setAgree}>
                I agree to the biometric checks described in this notice for this purpose
              </Checkbox>
              <p className="text-xs text-ink-muted">Optional. If you do not agree, choose another way below; it never counts against you.</p>
              <Button size="sm" isDisabled={!agree || !s.notice.acknowledged} pending={consent.isPending} onPress={() => consent.mutate(undefined)}>
                Agree and continue
              </Button>
              {consent.error ? <ApiErrorView error={consent.error} /> : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </Card>
  );
}

function AlternativesCard({ s, refreshKey }: { s: SubjectSessionStatus; refreshKey: string[] }) {
  const choose = useCellMutation((api, mode: string) => api.privacy.v2.chooseRoute(s.session_ref, mode), {
    invalidate: [refreshKey],
    success: (r) => (r.alternative_mode ? "Another way chosen: the camera will not be used" : "Back to the camera route"),
  });
  return (
    <Card title="Other ways to verify" description="If the camera, face matching or a chip scan will not work for you, or you prefer not to use them.">
      <p className="mb-3 text-sm text-ink-muted">{s.statement} You do not need to tell us why.</p>
      <ul className="space-y-2" aria-label="Other ways to verify">
        {s.alternatives.map((a) => {
          const chosen = s.alternative_mode === a.id;
          return (
            <li key={a.id} className="flex flex-wrap items-center justify-between gap-2 rounded-[var(--radius-control)] border border-border px-3 py-2 text-sm">
              <span className="flex items-center gap-2">
                <Hand aria-hidden className="size-4 text-ink-muted" />
                {a.label}
              </span>
              {chosen ? (
                <Badge tone="ok">Chosen</Badge>
              ) : (
                <Button size="xs" variant="secondary" isDisabled={s.state !== "OPEN"} pending={choose.isPending} onPress={() => choose.mutate(a.id)} aria-label={`Choose: ${a.label}`}>
                  Choose
                </Button>
              )}
            </li>
          );
        })}
      </ul>
      {s.alternative_mode && s.state === "OPEN" ? (
        <Button className="mt-3" size="sm" variant="secondary" pending={choose.isPending} onPress={() => choose.mutate("NONE")}>
          Use the camera after all
        </Button>
      ) : null}
      {choose.error ? <ApiErrorView error={choose.error} /> : null}
    </Card>
  );
}

export function VerificationSessionView({ sessionRef }: { sessionRef: string }) {
  const key = KEY(sessionRef);
  const q = useCellQuery(key, (api) => api.privacy.v2.session(sessionRef));
  const cancel = useCellMutation((api) => api.privacy.v2.cancelSession(sessionRef), { invalidate: [key], success: "Session stopped. Nothing more will be collected." });
  return (
    <>
      <PageHeader title="Verify your identity" description="Before anything is captured, see what is checked, why, and the other ways you can choose." />
      <QueryView query={q}>
        {(s) => (
          <div className="space-y-5">
            {s.capture_enabled ? (
              <Banner tone="ok" title="Ready" icon={CheckCircle2}>
                {s.route === "CAMERA" ? "You can continue to the camera step." : "You can continue on the route you chose."}
              </Banner>
            ) : (
              <Banner tone="info" title={s.route === "ALTERNATIVE" ? "Another way chosen" : "Before the camera can be used"}>
                <ul className="list-disc pl-5">
                  {[...new Set(s.blockers.map(blockerText))].map((t) => (
                    <li key={t}>{t}</li>
                  ))}
                </ul>
              </Banner>
            )}
            {s.assisted_state ? (
              <Banner tone={s.assisted_state === "NOT_COMPLETED" ? "warn" : "info"} title="Another way, with a person">
                {ASSISTED_TEXT[s.assisted_state]}
              </Banner>
            ) : null}
            <NoticeCard s={s} refreshKey={key} />
            <AlternativesCard s={s} refreshKey={key} />
            {s.state === "OPEN" ? (
              <div className="flex flex-wrap gap-2">
                <Button variant="secondary" icon={<ScanFace aria-hidden className="size-4" />} pending={cancel.isPending} onPress={() => cancel.mutate(undefined)}>
                  Stop this session
                </Button>
              </div>
            ) : null}
            {cancel.error ? <ApiErrorView error={cancel.error} /> : null}
          </div>
        )}
      </QueryView>
    </>
  );
}
