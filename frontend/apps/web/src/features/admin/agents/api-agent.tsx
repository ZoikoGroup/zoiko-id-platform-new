"use client";

import type { ApiClientRegistered } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, Dialog, KeyValues, TextField } from "@zoiko/ui";
import { useRef, useState } from "react";

import { ApiErrorView, useCellMutation, useCellQuery } from "@/lib/cell";

import { generateClientKeyPair, normalizePublicKey, type Jwks } from "./api-client-key";
import { AGENTS_KEY, BUILDS_KEY, splitList } from "./shared";

export { jwksProblem } from "./api-client-key";

/** Save the private key as a file on the person's own machine. The page keeps no copy and sends nothing. */
function saveFile(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "application/x-pem-file" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

const OPENSSL = `openssl ecparam -name prime256v1 -genkey -noout -out agent.key   # PRIVATE: keep it, never share it
openssl ec -in agent.key -pubout -out agent.pub                  # PUBLIC: paste the contents of this file`;

/** How a company gets a key to register, with the safe way first. Native details, so it works with a keyboard and a screen reader. */
function KeyGuide() {
  return (
    <details className="rounded-card border border-border bg-sunken/40 p-3 text-sm">
      <summary className="cursor-pointer font-medium text-ink">How do I get a public key?</summary>
      <div className="mt-2 space-y-3 text-ink-muted">
        <p>
          You make the key pair, you keep the private half, and you give us only the public half. We never see or hold the private key, and we cannot recover it. The agent signs with the
          private key every time it asks for a token.
        </p>
        <div>
          <p className="font-medium text-ink">Best for production: a key service</p>
          <p>
            Create a signing key in your own key service (AWS KMS, Google Cloud KMS, Azure Key Vault or an HSM), so the private key never leaves it. Export its public key and paste it here.
            Use an elliptic-curve P-256 or P-384 key, Ed25519, or RSA of 2048 bits or more.
          </p>
        </div>
        <div>
          <p className="font-medium text-ink">With openssl</p>
          <pre className="mt-1 overflow-x-auto rounded-card bg-sunken p-2 font-mono text-2xs text-ink">{OPENSSL}</pre>
          <p className="mt-1">Paste the text of <span className="font-mono text-2xs">agent.pub</span> below as it is. We turn it into a key set and name the key from its fingerprint.</p>
        </div>
        <p>You can also paste a key set or a single public key in JSON. A certificate, a key address, or anything that contains a private key is refused.</p>
      </div>
    </details>
  );
}

/** Register an API client as a build: its public keys and the audiences it may call. A second person approves it, as with an image. */
export function RegisterApiClientDialog({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const [name, setName] = useState("");
  const [audiences, setAudiences] = useState("");
  const [jwks, setJwks] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [done, setDone] = useState<ApiClientRegistered | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const checked = useRef<Jwks | null>(null);

  const register = useCellMutation<void, ApiClientRegistered>(
    (api) => {
      if (!checked.current) throw new Error("Check the key set first.");
      return api.agents.registerApiClient({ name: name.trim(), jwks: checked.current, audiences: splitList(audiences) });
    },
    { invalidate: [BUILDS_KEY, AGENTS_KEY], onSuccess: (r) => setDone(r) },
  );

  function close() {
    if (register.isPending) return;
    setDone(null);
    setFormError(null);
    setNote(null);
    setSaved(null);
    checked.current = null;
    register.reset();
    onClose();
  }

  async function makeKey() {
    setFormError(null);
    try {
      const k = await generateClientKeyPair();
      saveFile(`zoiko-api-client-${k.kid.slice(0, 12)}.pem`, k.privatePem);
      setJwks(JSON.stringify(k.jwks, null, 2));
      setNote(null);
      setSaved(`zoiko-api-client-${k.kid.slice(0, 12)}.pem`);
    } catch (e) {
      setFormError(e instanceof Error ? e.message : "We could not make a key in this browser.");
    }
  }

  async function submit() {
    setFormError(null);
    if (name.trim().length < 2) return setFormError("Give the client a name.");
    if (!splitList(audiences).length) return setFormError("Name at least one audience: the exact address this client may call. No wildcards.");
    const p = await normalizePublicKey(jwks);
    if (!p.jwks) return setFormError(p.error ?? "Check the key set.");
    checked.current = p.jwks;
    setJwks(JSON.stringify(p.jwks, null, 2)); // show exactly what is registered, with any name we added
    setNote(p.note ?? null);
    register.mutate();
  }

  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={(o) => !o && close()}
      size="lg"
      title="Register an API client"
      description="For an agent that calls the platform over its API and runs no container image we can inspect. It registers public keys only."
      footer={
        done ? (
          <Button onPress={close}>Close</Button>
        ) : (
          <>
            <Button variant="secondary" isDisabled={register.isPending} onPress={close}>Cancel</Button>
            <Button pending={register.isPending} onPress={() => void submit()}>Register client</Button>
          </>
        )
      }
    >
      <div className="space-y-4">
        {done ? (
          <Banner tone="ok" title="Registered as a draft build" live="polite">
            <p>Client {done.client_id}. {done.next}</p>
            <p className="mt-1 break-all font-mono text-2xs">{done.artifact_digest}</p>
            <p className="mt-1 text-xs">Key fingerprints: {done.thumbprints.join(", ")}</p>
          </Banner>
        ) : (
          <>
            <Banner tone="info" title="What this proves, and what it does not">
              An API client proves it holds a key, not what code runs behind it. An agent bound to one may hold only tools that read or can be undone, never one that moves
              money or cannot be reversed. Changing a key is a new registration that needs its own approval.
            </Banner>
            <TextField label="Client name" isRequired value={name} onChange={setName} placeholder="Invoice reader" />
            <TextField label="Audiences" isRequired value={audiences} onChange={setAudiences} description="The exact addresses it may call, separated by spaces or commas." mono />
            <KeyGuide />
            <TextField
              label="Public key"
              isRequired
              multiline
              rows={7}
              value={jwks}
              onChange={setJwks}
              mono
              description="A public key set (JWKS), a single public key, or the PEM public key file. Public keys only: anything with a private part is refused."
            />
            <div className="flex flex-wrap items-center gap-2">
              <Button size="sm" variant="secondary" onPress={() => void makeKey()}>Make a key pair here (for a test or a small team)</Button>
              <span className="text-xs text-ink-muted">Your browser makes the key and saves the private half to your computer once. It is never sent to us.</span>
            </div>
            {saved ? (
              <Banner tone="warn" title="Keep your private key safe" live="polite">
                <p>
                  Your browser saved <span className="font-mono text-2xs">{saved}</span>. That file is the agent&apos;s private key. Put it in your secret store now. We do not have a copy and cannot recover it. Anyone
                  holding it can act as this agent. The box above holds only the public half.
                </p>
              </Banner>
            ) : null}
            {note ? <p className="text-xs text-ink-muted">{note}</p> : null}
            {formError ? <Banner tone="danger" title="Check the form" live="polite">{formError}</Banner> : null}
            {register.error ? <ApiErrorView error={register.error} /> : null}
          </>
        )}
      </div>
    </Dialog>
  );
}

/** The agent's API client binding: its fingerprints, its limits, and the two actions. Shown for every agent; empty for an image agent. */
export function ApiBindingCard({ agentId }: { agentId: string }) {
  const key = [...AGENTS_KEY, "api-binding", agentId];
  const binding = useCellQuery(key, (a) => a.agents.apiBinding(agentId));
  const bind = useCellMutation<void, unknown>((api) => api.agents.bindApiClient(agentId), { invalidate: [key, AGENTS_KEY], success: "API client bound" });
  const revoke = useCellMutation<void, unknown>((api) => api.agents.revokeApiBinding(agentId, "OPERATOR_DECISION"), { invalidate: [key, AGENTS_KEY], success: "Binding revoked; a running agent was suspended" });
  const b = binding.data;
  return (
    <Card
      title="API client binding"
      description="For an agent without a container image. It meets the runtime-binding gate in place of a workload binding."
      actions={
        <span className="flex gap-2">
          <Button size="sm" variant="secondary" isDisabled={Boolean(b?.bound)} pending={bind.isPending} onPress={() => bind.mutate()}>Bind approved client</Button>
          <Button size="sm" variant="danger" isDisabled={!b?.binding || b.binding.status === "REVOKED"} pending={revoke.isPending} onPress={() => revoke.mutate()}>Revoke binding</Button>
        </span>
      }
    >
      {b?.binding ? (
        <div className="space-y-3">
          <KeyValues
            columns={2}
            items={[
              ["Status", <Badge key="s" tone={b.binding.status === "ACTIVE" ? "ok" : b.binding.status === "SUSPENDED" ? "warn" : "neutral"}>{b.binding.status.toLowerCase()}</Badge>],
              ["Client", <span key="c" className="font-mono text-xs">{b.binding.client_id}</span>],
              ["Audiences", b.binding.audiences.join(", ")],
              ["Key fingerprints", <span key="t" className="break-all font-mono text-2xs">{b.binding.thumbprints.join(", ")}</span>],
              ["Valid until", b.binding.valid_to.slice(0, 10)],
              ["Revoked because", b.binding.revoke_reason],
            ]}
          />
          {b.limits ? <p className="text-xs text-ink-muted">{b.limits.note} Risk class up to {b.limits.max_risk_class}; side effects: {b.limits.side_effects.join(", ").toLowerCase()}.</p> : null}
        </div>
      ) : (
        <p className="text-sm text-ink-muted">Not bound. Register an API client under Agents, have a second person approve it, create the release from it, register the agent, then bind it here.</p>
      )}
      {bind.error ? <ApiErrorView error={bind.error} /> : null}
      {revoke.error ? <ApiErrorView error={revoke.error} /> : null}
    </Card>
  );
}
