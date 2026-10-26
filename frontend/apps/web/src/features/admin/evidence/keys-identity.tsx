"use client";

import { Banner, Button, Card, KeyValues, JsonView, TextField } from "@zoiko/ui";
import { KeyRound, UserSearch } from "lucide-react";
import { useState } from "react";

import { QueryView, useCellQuery } from "@/lib/cell";
import { useRuntime } from "@/lib/runtime";

import { R3ActionDialog } from "../identity-providers/shared";

const HANDLE = /^ph1\.[0-9]+\.[A-Za-z0-9_-]+$/;

/** The public keys that verify receipts, checkpoints and exported packages without calling the platform. */
export function KeysSection() {
  const keys = useCellQuery(["admin", "evidence", "keys"], (api) => api.evidence.keys());
  return (
    <Card
      title="Evidence verification keys"
      description="Public keys only. A receipt, checkpoint or exported package signed by one of these can be checked offline; past keys stay listed so history keeps verifying."
      actions={
        keys.data ? (
          <Button
            size="sm"
            variant="secondary"
            onPress={() => {
              const blob = new Blob([JSON.stringify(keys.data, null, 2)], { type: "application/json" });
              const a = document.createElement("a");
              a.href = URL.createObjectURL(blob);
              a.download = "zoiko-evidence-keys.json";
              a.click();
              URL.revokeObjectURL(a.href);
            }}
          >
            Download JWKS
          </Button>
        ) : null
      }
    >
      <QueryView query={keys} loadingRows={2}>
        {(k) => (
          <div className="space-y-3">
            <p className="flex items-center gap-2 text-sm text-ink-muted">
              <KeyRound aria-hidden className="size-4" />
              {k.keys.length} public {k.keys.length === 1 ? "key" : "keys"} published.
            </p>
            <JsonView value={k} label="Keys (JWKS)" />
          </div>
        )}
      </QueryView>
    </Card>
  );
}

/** Re-identification: turn a pseudonymous handle from a receipt back into a person. Case-bound, R3, and itself
 * recorded in the chain of custody. The result is held in component state only and is gone when you leave. */
export function ResolveHandleSection() {
  const { api, protect } = useRuntime();
  const [handle, setHandle] = useState("");
  const [purpose, setPurpose] = useState("");
  const [caseRef, setCaseRef] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [resolved, setResolved] = useState<{ handle: string; principal_id: string } | null>(null);

  const valid = HANDLE.test(handle.trim()) && purpose.trim().length >= 3 && caseRef.trim().length > 0;

  return (
    <Card
      title="Re-identify a handle"
      description="Receipts name people by pseudonymous handle. Resolving one is a critical, case-bound action and leaves its own custody record."
    >
      <div className="space-y-4">
        <Banner tone="warn" icon={UserSearch} title="Use only for a real case">
          Every resolution is recorded with its purpose and case reference and can be reviewed by the privacy officer.
        </Banner>
        <TextField
          label="Handle"
          mono
          isRequired
          value={handle}
          onChange={setHandle}
          placeholder="ph1.1.…"
          errorMessage={handle && !HANDLE.test(handle.trim()) ? "A handle looks like ph1.1.abc…" : undefined}
        />
        <TextField label="Purpose" isRequired value={purpose} onChange={setPurpose} description="Why this person has to be identified (at least 3 characters)." />
        <TextField label="Case reference" mono isRequired value={caseRef} onChange={setCaseRef} />
        <div className="flex justify-end">
          <Button isDisabled={!valid} onPress={() => setConfirming(true)}>
            Review and resolve
          </Button>
        </div>
        {resolved ? (
          <KeyValues
            columns={1}
            items={[
              ["Handle", <span key="h" className="break-all font-mono text-xs">{resolved.handle}</span>],
              ["Principal", <span key="p" className="break-all font-mono text-xs">{resolved.principal_id}</span>],
            ]}
          />
        ) : null}
      </div>
      {confirming ? (
        <R3ActionDialog
          isOpen
          onClose={() => setConfirming(false)}
          title="Re-identify this handle"
          intent={<p>The handle is resolved to the person it stands for, for the purpose and case you entered. This is recorded and cannot be taken back.</p>}
          scope={[
            ["Handle", <span key="h" className="break-all font-mono text-xs">{handle.trim()}</span>],
            ["Purpose", purpose.trim()],
            ["Case", <span key="c" className="font-mono text-xs">{caseRef.trim()}</span>],
          ]}
          reversibility="irreversible"
          dependents={[{ label: "Chain of custody", effect: "A re-identification entry is recorded" }]}
          commitLabel="Resolve"
          run={async () => {
            const r = await protect(() => api.evidence.resolveHandle({ handle: handle.trim(), purpose: purpose.trim(), case_ref: caseRef.trim() }));
            setResolved(r);
            return { outcome: "RESOLVED", objectId: r.handle };
          }}
        />
      ) : null}
    </Card>
  );
}
