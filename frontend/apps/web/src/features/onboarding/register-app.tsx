"use client";

import type { AppEnvironment, AppProtocol, OAuthClientRecord, PepContextField, PepObligation } from "@zoiko/api-client";
import { Banner, Button, Checkbox, Dialog, RadioCards, Select, TextField } from "@zoiko/ui";
import { useState } from "react";

import { ApiErrorView, useCellMutation, useCellQuery } from "@/lib/cell";
import { downloadText, generateKeyPair } from "@/lib/keypair";

import { CLIENTS_KEY } from "./oauth-clients";
import { GATES_KEY } from "./shared";

const APPS_KEY = ["integration", "applications"];

/* -------------------------------------------------------------------------------- plain-words choices */

type Kind = "DECISIONS" | "SIGNIN" | "BOTH";
const KINDS: { id: Kind; label: string; description: string; protocols: AppProtocol[] }[] = [
  {
    id: "DECISIONS",
    label: "It asks Zoiko iD whether someone may do something",
    description: "Recommended to get started. Your system checks with us before an action, such as paying a salary or exporting data.",
    protocols: ["AUTHZEN"],
  },
  {
    id: "SIGNIN",
    label: "People sign in to it with Zoiko iD",
    description: "A website or app where people log in through Zoiko iD.",
    protocols: ["OIDC"],
  },
  { id: "BOTH", label: "Both", description: "People sign in with Zoiko iD and it also asks us for access decisions.", protocols: ["OIDC", "AUTHZEN"] },
];

const ENVIRONMENTS: { id: AppEnvironment; label: string; description: string }[] = [
  { id: "PROD", label: "Production", description: "Real people and real data." },
  { id: "STAGE", label: "Staging", description: "A rehearsal copy of production." },
  { id: "TEST", label: "Test", description: "Automated or manual testing." },
  { id: "DEV", label: "Development", description: "A developer's own machine." },
];

const OBLIGATIONS: { id: PepObligation; label: string; hint: string }[] = [
  { id: "STEP_UP", label: "Ask the person to confirm again", hint: "A fresh passkey check before a sensitive action." },
  { id: "HUMAN_APPROVAL", label: "Wait for a person to approve", hint: "A named human signs off before it goes ahead." },
  { id: "COMMIT_RECHECK", label: "Check again at the last moment", hint: "Re-ask just before the change is saved." },
  { id: "MANDATE_RESERVE", label: "Reserve a spending limit", hint: "Hold part of an agreed limit while the action runs." },
  { id: "TOOL_ALLOWLIST", label: "Only allow approved tools", hint: "Limit which tools an automated agent may use." },
];
const CONTEXT: { id: PepContextField; label: string; hint: string }[] = [
  { id: "purpose", label: "Purpose", hint: "Why the person is doing this, for example “payroll”." },
  { id: "represented_principal_id", label: "Who they act for", hint: "The person or company they represent." },
  { id: "mandate_id", label: "Mandate", hint: "The delegation that allows the action." },
  { id: "transaction_context_id", label: "Transaction", hint: "The business transaction it belongs to." },
  { id: "tool_id", label: "Tool", hint: "Which tool or agent is acting." },
  { id: "value", label: "Amount", hint: "The money value involved." },
];

function toggle<T>(list: T[], v: T): T[] {
  return list.includes(v) ? list.filter((x) => x !== v) : [...list, v];
}

/* ------------------------------------------------------------------------------------------- wizard */

type Step = 1 | 2 | 3 | 4;

/**
 * Register an application in plain steps: what it does, how it identifies itself, what it sends, then a summary. The
 * key it identifies itself with can be made right here, so nobody has to know what an OAuth client or a JWK is first.
 */
export function RegisterApp({ onClose }: { onClose: () => void }) {
  const [step, setStep] = useState<Step>(1);
  const [name, setName] = useState("");
  const [environment, setEnvironment] = useState<AppEnvironment>("PROD");
  const [kind, setKind] = useState<Kind>("DECISIONS");
  const protocols = KINDS.find((k) => k.id === kind)?.protocols ?? ["AUTHZEN"];
  const authzen = protocols.includes("AUTHZEN");

  // how it identifies itself
  const clients = useCellQuery(CLIENTS_KEY, (api) => api.integration.oauthClients());
  // Only a machine client for decisions can be used here: a client people sign in through cannot get a machine token.
  const mine = (clients.data?.items ?? []).filter((c) => c.status === "ACTIVE" && !c.first_party && c.scopes.includes("access:evaluate"));
  const [keySource, setKeySource] = useState<"NEW" | "EXISTING">("NEW");
  const [existing, setExisting] = useState<string | null>(null);
  const [publicKeys, setPublicKeys] = useState("");
  const [privateKey, setPrivateKey] = useState("");
  const [keyBusy, setKeyBusy] = useState(false);
  const [keyError, setKeyError] = useState<string | null>(null);

  // what it sends and enforces
  const [preset, setPreset] = useState<"SIMPLE" | "CUSTOM">("SIMPLE");
  const [obligations, setObligations] = useState<PepObligation[]>(["STEP_UP"]);
  const [context, setContext] = useState<PepContextField[]>(["purpose"]);

  const [done, setDone] = useState<{ appId: string; clientId: string | null; privateKey: string } | null>(null);

  const create = useCellMutation(
    async (api) => {
      let clientId = keySource === "EXISTING" ? existing : null;
      if (authzen && keySource === "NEW") {
        const keys = JSON.parse(publicKeys) as { keys: Record<string, unknown>[] };
        const c: OAuthClientRecord = await api.integration.createOauthClient({
          name: `${name.trim()} (access decisions)`,
          client_type: "CONFIDENTIAL",
          purpose: "DECISIONS", // a machine client: it asks the decision API with its signed key, so it needs no return address
          jwks: keys,
        });
        clientId = c.client_id;
      }
      const app = await api.integration.createApplication({
        name: name.trim(),
        environment,
        protocols,
        ...(authzen ? { pep_client_id: clientId, pep_obligations: obligations, pep_context_fields: context } : {}),
      });
      return { appId: app.application_id, clientId };
    },
    { invalidate: [APPS_KEY, CLIENTS_KEY], success: "Application registered", onSuccess: (r) => setDone({ ...r, privateKey }) },
  );

  async function makeKey() {
    setKeyBusy(true);
    setKeyError(null);
    try {
      const k = await generateKeyPair();
      setPublicKeys(k.publicKeys);
      setPrivateKey(k.privateKey);
    } catch {
      setKeyError("This browser could not create a key. Use a current Chrome, Edge, Firefox or Safari over https.");
    } finally {
      setKeyBusy(false);
    }
  }

  const nameOk = name.trim().length >= 2;
  const identOk = !authzen || (keySource === "EXISTING" ? Boolean(existing) : publicKeys.trim().length > 0);
  const effective = preset === "SIMPLE" ? { o: ["STEP_UP"] as PepObligation[], c: ["purpose"] as PepContextField[] } : { o: obligations, c: context };

  if (done) return <Finished done={done} name={name.trim()} onClose={onClose} />;

  const stepsFor: Step[] = authzen ? [1, 2, 3, 4] : [1, 4];
  const index = stepsFor.indexOf(step);
  const next = stepsFor[index + 1];
  const back = stepsFor[index - 1];
  const canContinue = step === 1 ? nameOk : step === 2 ? identOk : true;

  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && !create.isPending && onClose()}
      title="Add your application"
      description={`Step ${index + 1} of ${stepsFor.length}`}
      size="lg"
      footer={
        <>
          {back ? (
            <Button variant="secondary" isDisabled={create.isPending} onPress={() => setStep(back)}>
              Back
            </Button>
          ) : (
            <Button variant="secondary" onPress={onClose}>
              Cancel
            </Button>
          )}
          {next ? (
            <Button isDisabled={!canContinue} onPress={() => setStep(next)}>
              Continue
            </Button>
          ) : (
            <Button pending={create.isPending} onPress={() => create.mutate(undefined)}>
              Add application
            </Button>
          )}
        </>
      }
    >
      <div className="space-y-4">
        {step === 1 ? (
          <>
            <TextField label="What is your application called?" isRequired value={name} onChange={setName} placeholder="Payroll" description="Any name your team will recognise." />
            <RadioCards
              label="What does it do with Zoiko iD?"
              value={kind}
              onChange={(v) => setKind(v as Kind)}
              options={KINDS.map((k) => ({ id: k.id, label: k.label, description: k.description }))}
            />
            <Select
              label="Where does it run?"
              value={environment}
              onChange={(v) => setEnvironment(v as AppEnvironment)}
              options={ENVIRONMENTS.map((e) => ({ id: e.id, label: e.label, description: e.description }))}
            />
          </>
        ) : null}

        {step === 2 ? (
          <>
            <p className="text-sm text-ink-muted">
              Your application proves who it is with a <strong>key</strong>, a bit like a staff badge. Zoiko iD keeps the public half to check it. Make
              one here in a click, or reuse one you already have.
            </p>
            {mine.length ? (
              <RadioCards
                label="Which key should it use?"
                value={keySource}
                onChange={(v) => setKeySource(v as "NEW" | "EXISTING")}
                options={[
                  { id: "NEW", label: "Make a new key", description: "Recommended." },
                  { id: "EXISTING", label: "Use a key I already have", description: "Pick one from your list." },
                ]}
              />
            ) : null}
            {keySource === "EXISTING" ? (
              <Select
                label="Existing key (client)"
                isRequired
                value={existing}
                onChange={setExisting}
                options={mine.map((c) => ({ id: c.client_id, label: c.client_id, description: c.client_type === "PUBLIC" ? "Browser or mobile" : "Server" }))}
                placeholder="Select…"
              />
            ) : (
              <div className="space-y-4">
                <div className="space-y-2 rounded-card border border-border p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <Button size="sm" pending={keyBusy} onPress={() => void makeKey()}>
                      {publicKeys ? "Make a different key" : "Make a key for me"}
                    </Button>
                    <span className="text-xs text-ink-subtle">Made in this browser. The private half never leaves it.</span>
                  </div>
                  {keyError ? <p className="text-sm text-danger-700">{keyError}</p> : null}
                  {privateKey ? (
                    <>
                      <Banner tone="warn" title="Save the private key now">
                        You will see it only once. Give it to your developers and keep it in a safe place, such as a secrets manager. Anyone with it can
                        act as your application.
                      </Banner>
                      <div className="flex flex-wrap gap-2">
                        <Button size="xs" variant="secondary" onPress={() => void navigator.clipboard?.writeText(privateKey)}>
                          Copy private key
                        </Button>
                        <Button size="xs" variant="secondary" onPress={() => downloadText(`${name.trim() || "application"}-private-key.json`, privateKey)}>
                          Download private key
                        </Button>
                      </div>
                    </>
                  ) : null}
                  <TextField
                    label="Public key (filled in for you)"
                    multiline
                    rows={4}
                    mono
                    value={publicKeys}
                    onChange={setPublicKeys}
                    description="Safe to share. Already have your own? Paste the public key set here instead."
                  />
                </div>
              </div>
            )}
          </>
        ) : null}

        {step === 3 ? (
          <>
            <p className="text-sm text-ink-muted">
              With every question, your application sends a few <strong>details</strong>, and it can carry out extra <strong>safeguards</strong> when
              we ask. The simple choice below is the right start for almost everyone.
            </p>
            <RadioCards
              label="What will it send and enforce?"
              value={preset}
              onChange={(v) => setPreset(v as "SIMPLE" | "CUSTOM")}
              options={[
                { id: "SIMPLE", label: "Keep it simple (recommended)", description: "Sends the purpose of each action and can ask a person to confirm again." },
                { id: "CUSTOM", label: "Let me choose", description: "Pick exactly which details and safeguards apply." },
              ]}
            />
            {preset === "CUSTOM" ? (
              <div className="space-y-4">
                <fieldset className="space-y-2">
                  <legend className="text-sm font-medium text-ink">Details it will always send</legend>
                  <p className="text-xs text-ink-subtle">Everything ticked must be present on every question, or onboarding cannot finish. Fewer is easier.</p>
                  {CONTEXT.map((c) => (
                    <Checkbox key={c.id} isSelected={context.includes(c.id)} onChange={() => setContext((l) => toggle(l, c.id))}>
                      {c.label} <span className="text-xs text-ink-subtle">{c.hint}</span>
                    </Checkbox>
                  ))}
                </fieldset>
                <fieldset className="space-y-2">
                  <legend className="text-sm font-medium text-ink">Safeguards it can carry out</legend>
                  {OBLIGATIONS.map((o) => (
                    <Checkbox key={o.id} isSelected={obligations.includes(o.id)} onChange={() => setObligations((l) => toggle(l, o.id))}>
                      {o.label} <span className="text-xs text-ink-subtle">{o.hint}</span>
                    </Checkbox>
                  ))}
                </fieldset>
              </div>
            ) : null}
          </>
        ) : null}

        {step === 4 ? (
          <div className="space-y-3 text-sm">
            <p className="text-ink-muted">Check this and add it. You can change it later.</p>
            <dl className="grid grid-cols-[10rem_1fr] gap-x-4 gap-y-2 rounded-card border border-border p-4">
              <dt className="text-ink-subtle">Name</dt>
              <dd className="font-medium text-ink">{name.trim()}</dd>
              <dt className="text-ink-subtle">Does</dt>
              <dd>{KINDS.find((k) => k.id === kind)?.label}</dd>
              <dt className="text-ink-subtle">Runs in</dt>
              <dd>{ENVIRONMENTS.find((e) => e.id === environment)?.label}</dd>
              {authzen ? (
                <>
                  <dt className="text-ink-subtle">Key</dt>
                  <dd>{keySource === "EXISTING" ? <span className="font-mono text-xs">{existing}</span> : "A new key, made now"}</dd>
                  <dt className="text-ink-subtle">Always sends</dt>
                  <dd>{effective.c.map((c) => CONTEXT.find((x) => x.id === c)?.label).join(", ") || "nothing extra"}</dd>
                  <dt className="text-ink-subtle">Can carry out</dt>
                  <dd>{effective.o.map((o) => OBLIGATIONS.find((x) => x.id === o)?.label).join(", ") || "no extra safeguards"}</dd>
                </>
              ) : null}
            </dl>
            {privateKey && keySource === "NEW" && authzen ? (
              <Banner tone="warn" title="Did you save the private key?">
                It is only shown once, on the key step. Go back to copy or download it if you have not.
              </Banner>
            ) : null}
            {create.error ? <ApiErrorView error={create.error} title="The application was not added" /> : null}
          </div>
        ) : null}
      </div>
    </Dialog>
  );
}

/** After adding: what happens next, with the one click that does it. */
function Finished({ done, name, onClose }: { done: { appId: string; clientId: string | null; privateKey: string }; name: string; onClose: () => void }) {
  const goLive = useCellMutation(
    async (api) => {
      await api.integration.moveApplication(done.appId, "TESTING");
      return api.integration.moveApplication(done.appId, "ACTIVE");
    },
    { invalidate: [APPS_KEY, GATES_KEY], success: "Your application is active", onSuccess: onClose },
  );
  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && onClose()}
      title={`“${name}” is added`}
      description="One more step before it counts for onboarding."
      footer={
        <>
          <Button variant="secondary" isDisabled={goLive.isPending} onPress={onClose}>
            Later
          </Button>
          <Button pending={goLive.isPending} onPress={() => goLive.mutate(undefined)}>
            Start testing and activate
          </Button>
        </>
      }
    >
      <div className="space-y-3 text-sm text-ink-muted">
        <p>
          A new application starts as a <strong>draft</strong>. Press the button to move it to testing and then active; an active application is what
          lets onboarding start shadow.
        </p>
        {done.clientId ? (
          <p>
            Its key (client id) is <code className="rounded bg-sunken px-1 font-mono text-xs">{done.clientId}</code>. Give it, and the private key you
            saved, to the developers.
          </p>
        ) : null}
        {goLive.error ? <ApiErrorView error={goLive.error} title="It could not be activated" /> : null}
      </div>
    </Dialog>
  );
}
