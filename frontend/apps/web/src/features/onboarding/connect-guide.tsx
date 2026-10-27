"use client";

import type { IntegrationApplication, OnboardingConnection } from "@zoiko/api-client";
import { Banner, Button, ButtonLink, Meter } from "@zoiko/ui";
import { CheckCircle2, Circle } from "lucide-react";
import { useState } from "react";

import { useCellQuery } from "@/lib/cell";

export const CONNECTION_KEY = ["onboarding", "connection"];

/** Where the live numbers come from: refreshed every few seconds while a person watches shadow. */
export function useConnection() {
  return useCellQuery(CONNECTION_KEY, (api) => api.onboarding.connection(), { refetchInterval: 8_000 });
}

/* ------------------------------------------------------------------------------------ step 5: connect */

/**
 * The connection in plain words, for someone who has never done it. Four steps, each with a clear next action and a
 * check mark when it is done; the code is one click away and already carries this company's own addresses.
 */
export function ConnectGuide({ apps }: { apps: IntegrationApplication[] }) {
  const connection = useConnection();
  const first = apps[0];
  const clientId = apps.find((a) => a.pep_client_id)?.pep_client_id ?? null;
  const c = connection.data;

  return (
    <div className="space-y-4">
      <p className="text-sm text-ink-muted">
        Your application asks Zoiko iD <em>&ldquo;may this person do this?&rdquo;</em> before it lets something happen, and tells us what your
        current system answered. For now we only watch and compare: <strong>nothing changes for your users</strong>.
      </p>
      <ol className="space-y-3">
        <Item done={apps.length > 0} title="Tell us about your application">
          {apps.length > 0 ? (
            <>{apps.length === 1 ? `"${first?.name}" is registered.` : `${apps.length} applications are registered.`}</>
          ) : (
            <>Give it a name and say which details it will always send with each question.</>
          )}
          <ButtonLink href="/admin/applications" size="sm" variant="secondary" className="mt-2">
            {apps.length > 0 ? "Manage applications" : "Register an application"}
          </ButtonLink>
        </Item>
        <Item done={Boolean(clientId)} title="Give it a key to identify itself">
          {clientId ? (
            <>
              Its key (client id) is <code className="rounded bg-sunken px-1 font-mono text-xs">{clientId}</code>. Your application uses it to
              prove who it is.
            </>
          ) : (
            <>Once the application is registered, it gets a key here. Keep its secret safe: we never show it again.</>
          )}
        </Item>
        <Item done={false} title="Send us each access question">
          Add a few lines to your application. Copy the example below and fill in the parts in capitals.
          <Snippets evaluationUrl={c?.evaluation_url} tokenUrl={c?.token_url} clientId={clientId} />
        </Item>
        <Item done={false} title="Watch it work">
          Start shadow (step 7). This page then shows how many questions we have seen and whether anything is missing.
        </Item>
      </ol>
    </div>
  );
}

function Item({ done, title, children }: { done: boolean; title: string; children: React.ReactNode }) {
  return (
    <li className="flex gap-3">
      {done ? <CheckCircle2 aria-label="Done" className="mt-0.5 size-5 shrink-0 text-ok-600" /> : <Circle aria-hidden className="mt-0.5 size-5 shrink-0 text-ink-subtle" />}
      <div className="min-w-0 text-sm text-ink-muted">
        <p className="font-medium text-ink">{title}</p>
        <div className="mt-0.5">{children}</div>
      </div>
    </li>
  );
}

/* A ready-to-copy example with this company's own addresses filled in. Three languages, one click each. */
const LANGS = ["curl", "Node.js", "Python"] as const;

function Snippets({ evaluationUrl, tokenUrl, clientId }: { evaluationUrl?: string; tokenUrl?: string; clientId: string | null }) {
  const [lang, setLang] = useState<(typeof LANGS)[number]>("curl");
  const [copied, setCopied] = useState(false);
  const eval_ = evaluationUrl ?? "https://YOUR-ZOIKO-API/access/v1/evaluation";
  const code = snippet(lang, eval_, tokenUrl ?? "https://YOUR-ZOIKO-API/oauth2/token", clientId ?? "YOUR_APPLICATION_KEY");
  return (
    <details className="mt-2 rounded-md border border-border">
      <summary className="cursor-pointer px-3 py-2 text-sm font-medium text-brand-700">Show the example code</summary>
      <div className="border-t border-border p-3">
        <div className="mb-2 flex flex-wrap items-center gap-2" role="tablist" aria-label="Language">
          {LANGS.map((l) => (
            <Button key={l} size="xs" variant={l === lang ? "primary" : "secondary"} onPress={() => setLang(l)}>
              {l}
            </Button>
          ))}
          <Button
            size="xs"
            variant="secondary"
            onPress={() => {
              void navigator.clipboard?.writeText(code).then(() => setCopied(true));
              setTimeout(() => setCopied(false), 2000);
            }}
          >
            {copied ? "Copied" : "Copy"}
          </Button>
        </div>
        <pre className="max-h-72 overflow-auto rounded-md bg-sunken p-3 text-xs leading-5">
          <code>{code}</code>
        </pre>
        <p className="mt-2 text-xs text-ink-subtle">
          Ask once per protected action. <code>incumbent_decision</code> is what your current system decided (ALLOW or DENY). Add every detail you
          listed when you registered the application, for example <code>purpose</code>.
        </p>
      </div>
    </details>
  );
}

function snippet(lang: (typeof LANGS)[number], url: string, tokenUrl: string, clientId: string): string {
  const body = `{ "subject":  { "type": "principal", "id": "WHO_IS_ASKING" },
  "action":   { "name": "THE_ACTION" },
  "resource": { "type": "document", "id": "WHAT_THEY_WANT" },
  "context":  { "purpose": "WHY", "incumbent_decision": "ALLOW" } }`;
  const note = `Step 1: get an access token for client "${clientId}" from ${tokenUrl}
(client credentials; prove who you are with the key pair you registered).`;
  if (lang === "curl")
    return `# ${note.replaceAll("\n", "\n# ")}
# Step 2: ask the question
curl -X POST ${url} \\
  -H "Authorization: Bearer $ACCESS_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '${body.replaceAll("\n", " ")}'`;
  if (lang === "Node.js")
    return `// ${note.replaceAll("\n", "\n// ")}
// Step 2: ask the question
const res = await fetch("${url}", {
  method: "POST",
  headers: { Authorization: \`Bearer \${accessToken}\`, "Content-Type": "application/json" },
  body: JSON.stringify(${body}),
});
const { decision, context } = await res.json(); // decision is true only for ALLOW`;
  return `# ${note.replaceAll("\n", "\n# ")}
# Step 2: ask the question
import requests
r = requests.post("${url}",
    headers={"Authorization": f"Bearer {access_token}"},
    json=${body.replaceAll("{", "{").replaceAll("}", "}")})
decision = r.json()["decision"]  # True only for ALLOW`;
}

/* ------------------------------------------------------------------------------------- step 7: shadow */

const pct = (v: number) => `${(v * 100).toFixed(v >= 0.9995 || v === 0 ? 0 : 1)}%`;

/** How far shadow has got, in three bars and one sentence about what to do next. */
export function ShadowProgress() {
  const connection = useConnection();
  const s = connection.data?.shadow;
  if (!s) return null;
  const none = s.observations === 0;
  return (
    <div className="space-y-3 rounded-lg border border-border bg-surface p-4">
      <p className="text-sm font-medium text-ink">Watching your application</p>
      <Meter
        label="Questions we have seen"
        value={Math.min(s.observations, s.observations_needed)}
        max={s.observations_needed}
        tone={s.observations >= s.observations_needed ? "ok" : "brand"}
        valueText={`${s.observations} of ${s.observations_needed}`}
      />
      <Meter
        label="Questions with all the details"
        value={s.context_coverage * 100}
        tone={s.context_coverage >= s.context_coverage_needed ? "ok" : "warn"}
        valueText={`${pct(s.context_coverage)} (needs ${pct(s.context_coverage_needed)})`}
      />
      <Meter
        label="Agrees with your current system"
        value={(s.agreement ?? 0) * 100}
        tone={s.agreement !== null && s.agreement >= s.agreement_needed ? "ok" : "warn"}
        valueText={s.agreement === null ? "nothing to compare yet" : `${pct(s.agreement)} (needs ${pct(s.agreement_needed)})`}
      />
      {s.ready ? (
        <Banner tone="ok" title="Ready for the pilot">
          We have seen enough and everything matches. Start the pilot in step 8.
        </Banner>
      ) : none ? (
        <Banner tone="info" title="Nothing has arrived yet">
          Make sure your application is sending its questions to <code className="font-mono text-xs">{connection.data?.evaluation_url}</code> using the
          key from step 5. Each question your application asks counts here within a few seconds.
        </Banner>
      ) : s.context_coverage < s.context_coverage_needed || s.r3_context_coverage < s.r3_context_coverage_needed ? (
        <Banner tone="warn" title="Some questions are missing details">
          Add every detail you listed when you registered the application (for example the purpose) to each question.
        </Banner>
      ) : s.false_allows > 0 || (s.agreement !== null && s.agreement < s.agreement_needed) ? (
        <Banner tone="warn" title="We disagree with your current system on some questions">
          Open Access decisions to see which ones. Nothing was enforced, so no user was affected.
        </Banner>
      ) : (
        <p className="text-xs text-ink-muted">Keep your application running. The pilot opens when all three bars are full.</p>
      )}
    </div>
  );
}

/** Above "Start pilot": the plain reason it is not ready, instead of an error after the click. */
export function PilotReadiness() {
  const s = useConnection().data?.shadow;
  if (!s || s.ready) return null;
  return (
    <Banner tone="info" title="Not ready for the pilot yet">
      Shadow has seen {s.observations} of the {s.observations_needed} questions it needs. Watch the progress under step 7; the pilot opens once
      it is complete.
    </Banner>
  );
}

export type { OnboardingConnection };
