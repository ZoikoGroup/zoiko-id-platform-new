"use client";

import type { ProvisioningSource, SourceIn } from "@zoiko/api-client";
import { Banner, Button, Dialog, RiskBadge, TextField } from "@zoiko/ui";
import { CheckCircle2 } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, useCellMutation } from "@/lib/cell";

import { ConnectionDetails } from "./connection-details";

export const SOURCES_KEY = ["admin", "provisioning", "sources"];

export function CreateSourceDialog({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const [name, setName] = useState("");
  const [clientId, setClientId] = useState("");
  const [approval, setApproval] = useState("");
  const [created, setCreated] = useState<ProvisioningSource | null>(null);

  const create = useCellMutation((api, body: SourceIn) => api.provisioning.createSource(body), {
    invalidate: [SOURCES_KEY],
    success: "Provisioning source registered",
    onSuccess: (r) => setCreated(r),
  });

  function close() {
    if (create.isPending) return;
    setName("");
    setClientId("");
    setApproval("");
    setCreated(null);
    create.reset();
    onClose();
  }

  const valid = name.trim().length > 0 && clientId.trim().length > 0;

  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={(o) => !o && close()}
      title={created ? "Source registered" : "Register SCIM provisioning source"}
      description={created ? undefined : "Binds one OAuth client to a SCIM 2.0 connector for this tenant."}
      size="lg"
      footer={
        created ? (
          <Button onPress={close}>Done</Button>
        ) : (
          <>
            <Button variant="secondary" isDisabled={create.isPending} onPress={close}>
              Cancel
            </Button>
            <Button
              pending={create.isPending}
              isDisabled={!valid}
              onPress={() =>
                create.mutate({ display_name: name.trim(), client_id: clientId.trim(), approval_request_id: approval.trim() || null })
              }
            >
              Register source
            </Button>
          </>
        )
      }
    >
      {created ? (
        <div className="space-y-4">
          <Banner tone="ok" icon={CheckCircle2} title={`${name.trim() || "Source"} is active`} live="polite">
            Configure your identity provider&apos;s SCIM connector with these details.
          </Banner>
          <ConnectionDetails sourceId={created.source_id} clientId={created.client_id} />
        </div>
      ) : (
        <div className="space-y-4">
          <RiskBadge risk="R3" />
          <p className="text-sm text-ink-muted">
            A provisioning source can create, change and suspend every account it provisions. Register the OAuth client first (grant{" "}
            <code className="font-mono">client_credentials</code>, scope <code className="font-mono">scim:provision</code>); a client can
            back only one active source.
          </p>
          <TextField label="Display name" isRequired value={name} onChange={setName} placeholder="Workday HR" />
          <TextField label="OAuth client ID" isRequired value={clientId} onChange={setClientId} mono />
          <TextField
            label="Approval request ID (optional)"
            value={approval}
            onChange={setApproval}
            mono
            description="Needed when tenant policy requires a second administrator. The approval binds to create:<client ID>."
          />
          {create.error ? <ApiErrorView error={create.error} /> : null}
        </div>
      )}
    </Dialog>
  );
}
