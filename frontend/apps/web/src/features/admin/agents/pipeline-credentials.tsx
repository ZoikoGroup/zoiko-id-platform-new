"use client";

import type { OAuthClientRecord } from "@zoiko/api-client";
import { Banner, Button, CopyId, Dialog, TextField } from "@zoiko/ui";
import { useState } from "react";

import { ApiErrorView } from "@/lib/cell";
import { downloadText, generateKeyPair } from "@/lib/keypair";
import { useRuntime } from "@/lib/runtime";

type Made = { clientId: string; principalId: string | null; privateKey: string; name: string };

/**
 * Step one of "let your build pipeline register builds", in one press: make the pipeline's key pair in this browser, register its client (a machine
 * client for build pipelines, signed key only, no secret) and hand over the private key file. The private key is shown once and never sent to Zoiko iD;
 * only its public half is registered. The client can do nothing yet: its identity still needs the one grant, "Register a software build".
 */
export function PipelineCredentialsDialog({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const { api, protect } = useRuntime();
  const [name, setName] = useState("Build pipeline");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [made, setMade] = useState<Made | null>(null);

  function close() {
    if (busy) return;
    setError(null);
    setMade(null); // the private key leaves this screen with it
    onClose();
  }

  async function create() {
    setBusy(true);
    setError(null);
    try {
      const keys = await generateKeyPair("pipeline");
      const label = name.trim() || "Build pipeline";
      const client: OAuthClientRecord = await protect(() =>
        api.integration.createOauthClient({
          name: label,
          client_type: "CONFIDENTIAL",
          purpose: "BUILD_PIPELINE",
          jwks: JSON.parse(keys.publicKeys) as { keys: Record<string, unknown>[] },
        }),
      );
      setMade({ clientId: client.client_id, principalId: client.service_principal_id ?? null, privateKey: keys.privateKey, name: label });
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={(o) => !o && close()}
      title="Create pipeline credentials"
      description="The key your build pipeline signs in with. It can register builds and nothing else."
      size="lg"
      footer={
        made ? (
          <Button onPress={close}>Done</Button>
        ) : (
          <>
            <Button variant="secondary" isDisabled={busy} onPress={close}>
              Cancel
            </Button>
            <Button pending={busy} onPress={() => void create()}>
              Create credentials
            </Button>
          </>
        )
      }
    >
      {made ? (
        <div className="space-y-4 text-sm text-ink-muted">
          <Banner tone="warn" title="Save the private key now">
            It is shown only here and never sent to Zoiko iD. Keep it as a secret in your pipeline (for example <code className="font-mono text-xs">ZID_CLIENT_KEY</code>); if it is lost, make new
            credentials.
          </Banner>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" onPress={() => downloadText(`${made.name}-private-key.json`, made.privateKey)}>
              Download the private key
            </Button>
            <Button size="sm" variant="secondary" onPress={() => void navigator.clipboard?.writeText(made.privateKey)}>
              Copy it
            </Button>
          </div>
          <dl className="space-y-1">
            <div className="flex flex-wrap items-center gap-2">
              <dt className="text-ink">Client id</dt>
              <dd>
                <CopyId value={made.clientId} short={false} label="client id" />
              </dd>
            </div>
            {made.principalId ? (
              <div className="flex flex-wrap items-center gap-2">
                <dt className="text-ink">Identity to give the grant to</dt>
                <dd>
                  <CopyId value={made.principalId} short={false} label="grantee principal" />
                </dd>
              </div>
            ) : null}
          </dl>
          <p>
            <strong>One more step.</strong> These credentials can sign in but cannot do anything until they hold a grant. Under <strong>Authority &amp; approvals → Issue grant</strong>, choose the
            identity above as the grantee and tick only <strong>Register a software build</strong> (Agents and tools). It cannot approve, revoke or run anything.
          </p>
        </div>
      ) : (
        <div className="space-y-4">
          <TextField label="Name" value={name} onChange={setName} description="So people recognise it in lists, for example the agent or repository it builds." />
          <p className="text-sm text-ink-muted">
            A key pair is made in this browser. Only its public half is registered; you get the private half once, to keep in your pipeline.
          </p>
          {error ? <ApiErrorView error={error} title="The credentials could not be created" /> : null}
        </div>
      )}
    </Dialog>
  );
}
