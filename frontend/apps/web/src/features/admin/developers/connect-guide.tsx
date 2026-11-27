"use client";

import { Banner, Card, CopyId } from "@zoiko/ui";

const BASE = "https://YOUR-API-ADDRESS";

/** The whole integration as seven plain steps, the way Persona and Veriff explain theirs: keys, token, session, send the customer, hear back, check the signature, read the result. Every address is a real one. */
const STEPS: { title: string; text: string; code?: string }[] = [
  {
    title: "Keep your credentials on your server",
    text: "Create an application under Applications and keys, then a credential. You get a client ID and a secret. Store both in your other project's server settings (never in a web page or a mobile app): the secret is shown only once.",
  },
  {
    title: "Get an access token",
    text: "Your server trades the client ID and secret for a short-lived token. Do this before each batch of calls, and ask again when it expires.",
    code: [
      `curl -X POST ${BASE}/v1/verify-api/oauth2/token \\`,
      `  -H "Content-Type: application/json" \\`,
      `  -d '{"grant_type":"client_credentials","client_id":"<client id>","client_secret":"<secret>"}'`,
    ].join("\n"),
  },
  {
    title: "Start a verification for one customer",
    text: "Create a session when your customer reaches the point where you need to know who they are. Give your own reference for them and a purpose_code (customer_onboarding, account_recovery, fraud_prevention, regulatory_compliance, employment_screening or tenancy_screening). The answer contains launch_url: that is the page where they photograph a document and take a selfie.",
    code: [
      `curl -X POST ${BASE}/v1/verify-api/verification-sessions \\`,
      `  -H "Authorization: Bearer <access_token>" \\`,
      `  -H "Idempotency-Key: <a new random value>" \\`,
      `  -H "Content-Type: application/json" \\`,
      `  -d '{"customer_reference":"user-1234","country":"GB","purpose_code":"customer_onboarding"}'`,
    ].join("\n"),
  },
  {
    title: "Send your customer to launch_url",
    text: "Redirect the customer's browser to launch_url, or open it in a new tab or an in-app browser. We host the whole experience: the notice, the document photograph, the selfie, and the retakes. The link works once and expires, so create a new session for a new attempt.",
  },
  {
    title: "Receive the result by webhook",
    text: "Add a webhook endpoint (Webhooks tab) that points at your server. When the check is decided you receive verification.api.session_decided; if the customer leaves without finishing you receive verification.api.session_closed.",
  },
  {
    title: "Check the signature, then answer 200",
    text: "Every delivery is signed (HTTP Message Signature, RFC 9421) with a body digest. Verify the signature with the public key listed under More, then Verify signatures. Treat X-Zoiko-Event-ID as an idempotency key, because the same event can arrive twice. Answer with any 2xx status.",
  },
  {
    title: "Read the decision",
    text: "Use the decision in your own flow: approve the customer, ask for another attempt, or send them to your support. The decision says what was checked and the lowest grade it earned.",
    code: [`curl ${BASE}/v1/verify-api/verification-sessions/<session id>/decision \\`, `  -H "Authorization: Bearer <access_token>"`].join("\n"),
  },
];

export function ConnectGuideSection() {
  return (
    <div className="space-y-4">
      <Banner tone="info" title="Add identity verification to another project in seven steps">
        You do not install anything on our side. Your project only needs a server that can make HTTPS calls and receive a webhook. Replace <span className="font-mono">{BASE}</span> with this
        platform&apos;s API address. Build it against the sandbox first: no real person or document is checked there, and the same calls work in production once your go-live is approved.
      </Banner>
      <Card title="The flow">
        <ol className="space-y-5">
          {STEPS.map((s, i) => (
            <li key={s.title} className="flex gap-3">
              <span aria-hidden className="flex size-6 shrink-0 items-center justify-center rounded-full bg-brand-100 text-xs font-semibold text-brand-700">
                {i + 1}
              </span>
              <div className="min-w-0 flex-1 space-y-2 text-sm">
                <p className="font-semibold">{s.title}</p>
                <p className="text-ink-muted">{s.text}</p>
                {s.code ? (
                  <div className="space-y-1">
                    <pre className="overflow-x-auto rounded-lg bg-sunken p-3 font-mono text-xs">{s.code}</pre>
                    <CopyId value={s.code} label="the command" />
                  </div>
                ) : null}
              </div>
            </li>
          ))}
        </ol>
      </Card>
      <Card title="Other things you may want" description="Each of these is a single call.">
        <ul className="list-disc space-y-1 pl-5 text-sm text-ink-muted">
          <li>
            List a customer&apos;s checks: <span className="font-mono">GET /v1/verify-api/verification-sessions</span>
          </li>
          <li>
            Cancel one that is no longer needed: <span className="font-mono">POST /v1/verify-api/verification-sessions/&#123;id&#125;:cancel</span>
          </li>
          <li>
            Check again later: <span className="font-mono">POST /v1/verify-api/verification-sessions/&#123;id&#125;:recheck</span>
          </li>
          <li>
            Practice without a person: <span className="font-mono">POST /v1/verify-api/sandbox/fixtures/&#123;outcome&#125;/sessions</span> (sandbox only)
          </li>
        </ul>
        <p className="mt-3 text-sm">
          <a className="font-medium text-brand-700 underline" href="/docs/verify-api" target="_blank" rel="noreferrer noopener">
            Open the full API reference
          </a>
        </p>
      </Card>
    </div>
  );
}
