"use client";

import type { FulfilmentItem, RightsRequest } from "@zoiko/api-client";
import {
  Banner,
  Button,
  Card,
  CopyId,
  DataTable,
  DateTime,
  Dialog,
  EmptyState,
  KeyValues,
  Select,
  SidePanel,
  StatusBadge,
  TextField,
  type Column,
} from "@zoiko/ui";
import { Inbox, Plus } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { DeadlineBadge, PRIVACY_KEY, RIGHTS_TYPES, rightsLabel } from "./shared";

const REQUESTS_KEY = [...PRIVACY_KEY, "requests"];

export function RequestsSection() {
  const [status, setStatus] = useState("");
  const requests = useCellQuery([...REQUESTS_KEY, status], (api) => api.privacy.admin.requests(status || undefined));
  const [selected, setSelected] = useState<RightsRequest | null>(null);
  const [opening, setOpening] = useState(false);

  const columns: Column<RightsRequest>[] = [
    { key: "type", header: "Request", pinned: true, render: (r) => rightsLabel(r.request_type), sortValue: (r) => r.request_type },
    { key: "status", header: "Status", render: (r) => <StatusBadge state={r.status} kind="Rights request" /> },
    { key: "verification", header: "Verification", render: (r) => <StatusBadge state={r.identity_verification_state} kind="Verification" size="xs" /> },
    { key: "deadline", header: "Deadline", render: (r) => <DeadlineBadge deadline={r.deadline_at} source={r.deadline_source} consumed={r.deadline_consumed} /> },
    { key: "pack", header: "Jurisdiction", render: (r) => <span className="font-mono text-xs">{r.jurisdiction_pack_id}</span> },
    { key: "received", header: "Received", render: (r) => <DateTime value={r.received_at} />, sortValue: (r) => r.received_at },
  ];

  return (
    <>
      <Card
        padded={false}
        title="Data-rights requests"
        description="Case-managed requests with proportional verification, jurisdiction deadlines and per-system fulfilment evidence. A request never shows complete while a stage is pending."
        actions={
          <div className="flex gap-2">
            <Select
              label="Status"
              hideLabel
              value={status || "ALL"}
              onChange={(v) => setStatus(v === "ALL" ? "" : v)}
              options={[
                { id: "ALL", label: "All" },
                ...["RECEIVED", "SCOPING", "FULFILLING", "REVIEW", "COMPLETED", "PARTIALLY_DENIED", "DENIED"].map((s) => ({ id: s, label: s })),
              ]}
            />
            <Button size="sm" icon={<Plus aria-hidden className="size-4" />} onPress={() => setOpening(true)}>
              Record request
            </Button>
          </div>
        }
      >
        <QueryView query={requests}>
          {(data) => (
            <DataTable
              caption="Data-rights requests"
              columns={columns}
              rows={data.items}
              rowKey={(r) => r.request_id}
              onRowAction={setSelected}
              selectedKey={selected?.request_id ?? null}
              empty={<EmptyState icon={Inbox} title="No rights requests">Requests from data subjects and their agents appear here.</EmptyState>}
            />
          )}
        </QueryView>
      </Card>
      <OnBehalfDialog isOpen={opening} onClose={() => setOpening(false)} />
      <RequestPanel request={selected} onChange={setSelected} onClose={() => setSelected(null)} />
    </>
  );
}

function OnBehalfDialog({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const [type, setType] = useState("ACCESS");
  const [subject, setSubject] = useState("");
  const [pack, setPack] = useState("uk-gdpr");
  const [agent, setAgent] = useState("");
  const packs = useCellQuery([...PRIVACY_KEY, "packs"], (api) => api.privacy.packs(), { enabled: isOpen });
  const open = useCellMutation(
    (api) =>
      api.privacy.admin.openOnBehalf({
        request_type: type,
        subject_principal_id: subject.trim(),
        jurisdiction_pack_id: pack,
        authorized_agent_ref: agent.trim() || null,
        scope: {},
      }),
    { invalidate: [REQUESTS_KEY], success: "Request recorded", onSuccess: () => close() },
  );
  function close() {
    if (open.isPending) return;
    setSubject("");
    setAgent("");
    open.reset();
    onClose();
  }
  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={(o) => !o && close()}
      title="Record a rights request"
      description="For requests received by email, phone or post, or made by an authorized agent. The subject is verified before anything is disclosed."
      footer={
        <>
          <Button variant="secondary" onPress={close}>
            Cancel
          </Button>
          <Button pending={open.isPending} isDisabled={subject.trim().length !== 36} onPress={() => open.mutate(undefined)}>
            Record request
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Select label="Request type" value={type} onChange={setType} options={RIGHTS_TYPES} />
        <TextField label="Subject principal ID" value={subject} onChange={setSubject} isRequired mono />
        <Select
          label="Jurisdiction pack"
          value={pack}
          onChange={setPack}
          options={(packs.data?.items ?? [{ pack_id: "uk-gdpr" }]).map((p) => ({ id: p.pack_id, label: p.pack_id }))}
        />
        <TextField label="Authorized agent reference (optional)" value={agent} onChange={setAgent} mono />
        {open.error ? <ApiErrorView error={open.error} /> : null}
      </div>
    </Dialog>
  );
}

function RequestPanel({ request, onChange, onClose }: { request: RightsRequest | null; onChange: (r: RightsRequest) => void; onClose: () => void }) {
  const [method, setMethod] = useState("document-check");
  const [ackRef, setAckRef] = useState("");
  const [closeOutcome, setCloseOutcome] = useState("COMPLETED");
  const [reason, setReason] = useState("");
  const id = request?.request_id ?? "";
  const opts = { invalidate: [REQUESTS_KEY], onSuccess: onChange };
  const verify = useCellMutation((api) => api.privacy.admin.verify(id, { verified: true, level: 2, method }), { ...opts, success: "Requester verified" });
  const fail = useCellMutation((api) => api.privacy.admin.verify(id, { verified: false, level: 0, method }), { ...opts, success: "Verification recorded as failed" });
  const fulfill = useCellMutation((api) => api.privacy.admin.fulfill(id), { ...opts, success: "Fulfilment executed" });
  const ack = useCellMutation((api, item: FulfilmentItem) => api.privacy.admin.acknowledge(id, item.item_id, ackRef.trim()), {
    ...opts,
    success: "Processor acknowledgement recorded",
  });
  const closeReq = useCellMutation(
    (api) => api.privacy.admin.close(id, { outcome: closeOutcome, reason_code: reason.trim() || "REVIEWED", exemptions: [] }),
    { ...opts, success: "Request closed" },
  );
  const errors = [verify.error, fail.error, fulfill.error, ack.error, closeReq.error].filter(Boolean);
  const terminal = request ? ["COMPLETED", "PARTIALLY_DENIED", "DENIED", "WITHDRAWN"].includes(request.status) : true;

  return (
    <SidePanel isOpen={request !== null} onOpenChange={(o) => !o && onClose()} title={request ? rightsLabel(request.request_type) : ""} subtitle="Rights request">
      {request ? (
        <div className="space-y-5">
          <KeyValues
            items={[
              ["Status", <StatusBadge key="s" state={request.status} kind="Rights request" />],
              ["Verification", <StatusBadge key="v" state={request.identity_verification_state} kind="Verification" />],
              ["Deadline", <DeadlineBadge key="d" deadline={request.deadline_at} source={request.deadline_source} consumed={request.deadline_consumed} />],
              ["Subject", <CopyId key="sub" value={request.subject_principal_id} label="subject ID" />],
              ["Jurisdiction pack", request.jurisdiction_pack_id],
              ["Request ID", <CopyId key="id" value={request.request_id} label="request ID" />],
            ]}
          />
          {request.deadline_source === "COUNSEL_PENDING" ? (
            <Banner tone="warn" title="No legal deadline configured">
              Counsel has not yet set response deadlines in this jurisdiction pack. Track this request manually until they do.
            </Banner>
          ) : null}

          {!terminal && request.identity_verification_state === "PENDING" ? (
            <section className="space-y-2">
              <h3 className="text-sm font-semibold">Verify the requester</h3>
              <p className="text-sm text-ink-muted">Proportional to the request: never require identity documents for opt-outs.</p>
              <TextField label="Method" value={method} onChange={setMethod} />
              <div className="flex gap-2">
                <Button size="sm" pending={verify.isPending} onPress={() => verify.mutate(undefined)}>
                  Mark verified
                </Button>
                <Button size="sm" variant="danger-outline" pending={fail.isPending} onPress={() => fail.mutate(undefined)}>
                  Verification failed
                </Button>
              </div>
            </section>
          ) : null}

          {!terminal && request.identity_verification_state !== "PENDING" && request.status !== "FULFILLING" ? (
            <section className="space-y-2">
              <h3 className="text-sm font-semibold">Fulfil</h3>
              <p className="text-sm text-ink-muted">
                {request.request_type === "ERASURE"
                  ? "Disposes eligible data, keeps held or legally required items restricted, instructs processors and crypto-erases the evidence vault."
                  : "Executes the request against the data map and records evidence for each item."}
              </p>
              <Button size="sm" pending={fulfill.isPending} onPress={() => fulfill.mutate(undefined)}>
                Execute fulfilment (R3)
              </Button>
            </section>
          ) : null}

          {request.fulfilment_items.length ? (
            <section className="space-y-2">
              <h3 className="text-sm font-semibold">Fulfilment items</h3>
              <ul className="divide-y divide-border rounded-card border border-border">
                {request.fulfilment_items.map((item) => (
                  <li key={item.item_id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-sm">
                    <span className="min-w-0">
                      <span className="block truncate font-mono text-xs">{item.item_id}</span>
                      {item.reason ? <span className="text-2xs text-ink-subtle">{item.reason}</span> : null}
                    </span>
                    <span className="flex items-center gap-2">
                      <StatusBadge state={item.state} kind="Fulfilment" size="xs" />
                      {item.state === "PENDING_PROCESSOR_ACK" ? (
                        <Button size="xs" variant="secondary" isDisabled={ackRef.trim().length < 3} pending={ack.isPending} onPress={() => ack.mutate(item)}>
                          Acknowledge
                        </Button>
                      ) : null}
                    </span>
                  </li>
                ))}
              </ul>
              {request.fulfilment_items.some((i) => i.state === "PENDING_PROCESSOR_ACK") ? (
                <TextField label="Processor acknowledgement reference" value={ackRef} onChange={setAckRef} mono />
              ) : null}
            </section>
          ) : null}

          {!terminal && request.status === "REVIEW" ? (
            <section className="space-y-2">
              <h3 className="text-sm font-semibold">Close after review</h3>
              <Select
                label="Outcome"
                value={closeOutcome}
                onChange={setCloseOutcome}
                options={[
                  { id: "COMPLETED", label: "Completed" },
                  { id: "PARTIALLY_DENIED", label: "Partially denied (exemption)" },
                  { id: "DENIED", label: "Denied (exemption)" },
                ]}
              />
              <TextField label="Reason code" value={reason} onChange={setReason} mono placeholder="EXEMPTION_LEGAL_CLAIMS" />
              <Button size="sm" pending={closeReq.isPending} onPress={() => closeReq.mutate(undefined)}>
                Close request
              </Button>
            </section>
          ) : null}
          {errors.map((e, i) => (
            <ApiErrorView key={i} error={e} />
          ))}
        </div>
      ) : null}
    </SidePanel>
  );
}
