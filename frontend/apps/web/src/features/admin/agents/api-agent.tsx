"use client";

import type { ApiClientRegistered } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, Dialog, KeyValues, TextField } from "@zoiko/ui";
import { useState } from "react";

import { ApiErrorView, useCellMutation, useCellQuery } from "@/lib/cell";

import { AGENTS_KEY, BUILDS_KEY, splitList } from "./shared";

/** Refuse a private key in the browser too, so a secret is never even sent. The server refuses it again and never repeats it. */
export function jwksProblem(raw: string): { jwks?: { keys: Record<string, string>[] }; error?: string } {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return { error: "That is not valid JSON. Paste the public key set, for example {\"keys\": [ ... ]}." };
  }
  const keys = (parsed as { keys?: unknown }).keys;
  if (!Array.isArray(keys) || keys.length < 1 || keys.length > 5) return { error: "A key set has 1 to 5 keys under \"keys\"." };
  for (const k of keys as Record<string, unknown>[]) {
    if (["d", "p", "q", "dp", "dq", "qi", "oth", "k"].some((m) => m in k)) return { error: "That key contains private material. Paste the PUBLIC key only. Do not share the private half with anyone." };
    if (typeof k.kid !== "string" || !k.kid) return { error: "Every key needs a \"kid\" (a name for it), so a rotation can be traced." };
  }
  return { jwks: parsed as { keys: Record<string, string>[] } };
}

/** Register an API client as a build: its public keys and the audiences it may call. A second person approves it, as with an image. */
export function RegisterApiClientDialog({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const [name, setName] = useState("");
  const [audiences, setAudiences] = useState("");
  const [jwks, setJwks] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [done, setDone] = useState<ApiClientRegistered | null>(null);

  const register = useCellMutation<void, ApiClientRegistered>(
    (api) => {
      const parsed = jwksProblem(jwks);
      if (!parsed.jwks) throw new Error(parsed.error);
      return api.agents.registerApiClient({ name: name.trim(), jwks: parsed.jwks, audiences: splitList(audiences) });
    },
    { invalidate: [BUILDS_KEY, AGENTS_KEY], onSuccess: (r) => setDone(r) },
  );

  function close() {
    if (register.isPending) return;
    setDone(null);
    setFormError(null);
    register.reset();
    onClose();
  }

  function submit() {
    setFormError(null);
    if (name.trim().length < 2) return setFormError("Give the client a name.");
    if (!splitList(audiences).length) return setFormError("Name at least one audience: the exact address this client may call. No wildcards.");
    const p = jwksProblem(jwks);
    if (!p.jwks) return setFormError(p.error ?? "Check the key set.");
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
            <Button pending={register.isPending} onPress={submit}>Register client</Button>
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
            <TextField label="Public key set (JWKS)" isRequired multiline rows={7} value={jwks} onChange={setJwks} mono description="Public keys only. A key with private members is refused." />
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
