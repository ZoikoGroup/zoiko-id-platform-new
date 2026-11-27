"use client";

import { Banner, Button } from "@zoiko/ui";
import { useCallback, useEffect, useState } from "react";

import { LiveCheck } from "./live-check";
import { BAD_LINK, finishedText, OUTCOME, purposeText, safeReturn, safeSupport, themeClass, type HostedView } from "./logic";

type Step =
  | { kind: "loading" }
  | { kind: "bad" }
  | { kind: "ready"; view: HostedView }
  | { kind: "done"; outcome: string; back: string | null };

/** The test page a person reaches from a company's sandbox link (CR-151). Real capture arrives with the real engine; until then the page says plainly that this is a test and lets the
 * tester choose what the check should answer. It never shows the company's own reference for the person. */
export function HostedVerification({ token }: { token: string | null }) {
  const [step, setStep] = useState<Step>(token ? { kind: "loading" } : { kind: "bad" });
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const call = useCallback(
    async (body: unknown): Promise<{ ok: boolean; data?: unknown }> => {
      const res = await fetch(`/api/verify-hosted/${token}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        cache: "no-store",
        body: JSON.stringify(body),
      });
      if (!res.ok) return { ok: false };
      const json = (await res.json()) as { data?: unknown };
      return { ok: true, data: json.data ?? json };
    },
    [token],
  );

  useEffect(() => {
    if (!token) return;
    let live = true;
    void (async () => {
      try {
        const opened = await call({ action: "open" });
        if (live) setStep(opened.ok ? { kind: "ready", view: opened.data as HostedView } : { kind: "bad" });
      } catch {
        if (live) setStep({ kind: "bad" });
      }
    })();
    return () => {
      live = false;
    };
  }, [token, call]);

  async function choose(outcome: string) {
    setBusy(true);
    setProblem(null);
    try {
      const r = await call({ action: "simulate", outcome });
      if (r.ok) setStep({ kind: "done", outcome, back: safeReturn((r.data as { return_origin?: string }).return_origin) });
      else setProblem("That could not be sent. The link may have ended. Ask for a new one.");
    } catch {
      setProblem("That could not be sent right now. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  if (step.kind === "loading") {
    return (
      <p role="status" className="text-ink-muted">
        Opening your verification…
      </p>
    );
  }
  if (step.kind === "bad") {
    return (
      <section className="space-y-3">
        <h1 className="text-2xl font-bold tracking-tight text-ink">This link cannot be used</h1>
        <p role="status" className="text-ink-muted">
          {BAD_LINK}
        </p>
      </section>
    );
  }
  if (step.kind === "done") {
    return (
      <section className="space-y-4">
        <h1 className="text-2xl font-bold tracking-tight text-ink">Thank you</h1>
        <p role="status" className="text-ink-muted">
          {finishedText(step.outcome)}
        </p>
        {step.back ? (
          <a className="inline-block text-brand-600 underline" href={step.back} rel="noreferrer noopener">
            Back to the company
          </a>
        ) : null}
      </section>
    );
  }

  const v = step.view;
  if (v.live) {
    return (
      <section className="space-y-5">
        <div aria-hidden className={`h-1.5 w-16 rounded-full ${themeClass(v.theme).bar}`} />
        <LiveCheck requestedBy={v.requested_by} noticeVersion={v.notice_version ?? null} attemptsLeft={v.attempts_left ?? 0} photosKeptDays={v.photos_kept_days ?? 0} call={call} />
        {safeSupport(v.support_url) ? (
          <p className="text-sm text-ink-muted">
            Need help?{" "}
            <a className={`underline ${themeClass(v.theme).link}`} href={safeSupport(v.support_url)!} target="_blank" rel="noreferrer noopener">
              Contact {v.requested_by}
            </a>
          </p>
        ) : null}
      </section>
    );
  }
  return (
    <section className="space-y-5">
      <div aria-hidden className={`h-1.5 w-16 rounded-full ${themeClass(v.theme).bar}`} />
      <div className="space-y-2">
        <h1 className="text-2xl font-bold tracking-tight text-ink">Verify your identity</h1>
        <p className="text-ink-muted">
          <strong className="text-ink">{v.requested_by}</strong> has asked you to verify who you are {purposeText(v.purpose_code)}.
        </p>
      </div>
      {v.sandbox ? (
        <Banner tone="warn" title="This is a test">
          Nothing real is checked and no photographs or documents are taken. The person testing chooses what the answer will be.
        </Banner>
      ) : null}
      <div className="space-y-2">
        <h2 className="text-sm font-semibold text-ink">Choose the answer to send</h2>
        <ul className="space-y-2">
          {v.outcomes.map((o) => (
            <li key={o} className="flex items-center justify-between gap-3 rounded-lg border border-border-strong p-3">
              <span>
                <span className="block font-medium text-ink">{OUTCOME[o]?.label ?? o}</span>
                <span className="block text-sm text-ink-muted">{OUTCOME[o]?.help}</span>
              </span>
              <Button variant="secondary" isDisabled={busy} onPress={() => void choose(o)}>
                Send
              </Button>
            </li>
          ))}
        </ul>
        {problem ? (
          <p role="alert" className="text-sm text-danger-fg">
            {problem}
          </p>
        ) : null}
      </div>
      {safeSupport(v.support_url) ? (
        <p className="text-sm text-ink-muted">
          Need help?{" "}
          <a className={`underline ${themeClass(v.theme).link}`} href={safeSupport(v.support_url)!} target="_blank" rel="noreferrer noopener">
            Contact {v.requested_by}
          </a>
        </p>
      ) : null}
    </section>
  );
}
