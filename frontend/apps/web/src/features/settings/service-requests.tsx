"use client";

import type { ServiceKind, ServiceRequest } from "@zoiko/api-client";
import { Banner, Button, Card, DataTable, Dialog, EmptyState, Select, StatusBadge, TextField, type Column } from "@zoiko/ui";
import { Handshake, Plus } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

const KEY = ["settings", "service-requests"];

export const SERVICES: { id: ServiceKind; label: string; hint: string }[] = [
  { id: "DEDICATED_ISOLATION", label: "Dedicated isolation / private deployment", hint: "Which environment, region and isolation you need." },
  { id: "SOVEREIGN_SERVICE", label: "Sovereign service configuration", hint: "Which countries or regions the service must stay within." },
  { id: "KEY_CUSTODY", label: "Customer-held keys (BYOK / HYOK)", hint: "Which keys, and who holds them. Never send a key here." },
  { id: "NAMED_SUPPORT", label: "Custom SLA and named support", hint: "Hours, response targets and the people you want named." },
];

export const serviceLabel = (s: ServiceKind): string => SERVICES.find((x) => x.id === s)?.label ?? s;

function AskDialog({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const [service, setService] = useState<ServiceKind>("DEDICATED_ISOLATION");
  const [details, setDetails] = useState("");
  const [contact, setContact] = useState("");
  const create = useCellMutation((api) => api.serviceRequests.create({ service, details: details.trim(), contact: contact.trim() }), {
    invalidate: [KEY],
    success: "Request sent",
    onSuccess: () => {
      setDetails("");
      onClose();
    },
  });
  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={(o) => !o && !create.isPending && onClose()}
      title="Ask for an enterprise service"
      description="These are set up with you by Zoiko iD under your contract. Only plans that include the service can ask for it."
      footer={
        <>
          <Button variant="secondary" isDisabled={create.isPending} onPress={onClose}>
            Cancel
          </Button>
          <Button pending={create.isPending} isDisabled={details.trim().length < 10 || contact.trim().length < 3} onPress={() => create.mutate(undefined)}>
            Send request
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Select label="Service" value={service} onChange={(v) => setService(v as ServiceKind)} options={SERVICES.map((s) => ({ id: s.id, label: s.label, description: s.hint }))} />
        <TextField label="What you need" isRequired multiline rows={5} value={details} onChange={setDetails} description="At least 10 characters. Plain words; no keys or secrets." />
        <TextField label="Who should we contact" isRequired value={contact} onChange={setContact} placeholder="security@yourcompany.com" />
        {create.error ? <ApiErrorView error={create.error} /> : null}
      </div>
    </Dialog>
  );
}

export function ServiceRequestsPanel() {
  const list = useCellQuery(KEY, (api) => api.serviceRequests.list());
  const [asking, setAsking] = useState(false);
  const withdraw = useCellMutation((api, id: string) => api.serviceRequests.withdraw(id), { invalidate: [KEY], success: "Request withdrawn" });
  const columns: Column<ServiceRequest>[] = [
    { key: "service", header: "Service", pinned: true, render: (r) => <span className="font-medium">{serviceLabel(r.service)}</span> },
    { key: "status", header: "Status", render: (r) => <StatusBadge state={r.status} kind="Service request" /> },
    { key: "note", header: "From Zoiko iD", render: (r) => r.note || "—" },
    { key: "updated", header: "Updated", render: (r) => new Date(r.updated_at).toLocaleDateString() },
    {
      key: "do",
      header: "Actions",
      align: "end",
      render: (r) =>
        r.status === "REQUESTED" || r.status === "IN_PROGRESS" ? (
          <Button size="xs" variant="secondary" isDisabled={withdraw.isPending} onPress={() => withdraw.mutate(r.request_id)}>
            Withdraw
          </Button>
        ) : null,
    },
  ];
  return (
    <div className="space-y-4">
      <Banner tone="info" title="Delivered by people, not a switch">
        Dedicated isolation, sovereign service, customer-held keys and named support are arranged with you. Ask here and follow where each stands.
      </Banner>
      <Card
        title="Your requests"
        actions={
          <Button icon={<Plus aria-hidden className="size-4" />} onPress={() => setAsking(true)}>
            New request
          </Button>
        }
        padded={false}
      >
        <QueryView query={list}>
          {(d) => (
            <DataTable
              caption="Enterprise service requests"
              columns={columns}
              rows={d.items}
              rowKey={(r) => r.request_id}
              empty={
                <EmptyState icon={Handshake} title="No requests yet">
                  Ask for the enterprise services your plan includes.
                </EmptyState>
              }
            />
          )}
        </QueryView>
        {withdraw.error ? (
          <div className="p-4">
            <ApiErrorView error={withdraw.error} />
          </div>
        ) : null}
      </Card>
      <AskDialog isOpen={asking} onClose={() => setAsking(false)} />
    </div>
  );
}
