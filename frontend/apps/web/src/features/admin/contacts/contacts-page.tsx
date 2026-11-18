"use client";

import type { EmailContact, EmailContactPurpose } from "@zoiko/api-client";
import { Banner, Button, Card, DataTable, EmptyState, PageHeader, Select, TextField, type Column } from "@zoiko/ui";
import { Mail } from "lucide-react";
import { useState, type FormEvent } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

const KEY = ["contacts"];

const PURPOSES: { id: EmailContactPurpose; label: string; help: string }[] = [
  { id: "BILLING", label: "Billing", help: "Invoices, payments, plan changes and usage warnings." },
  { id: "SECURITY", label: "Security", help: "Risk alerts, incidents and privileged-access notices." },
  { id: "OWNER", label: "Owner", help: "Workspace, agent and ownership notices." },
  { id: "TECHNICAL", label: "Technical", help: "Maintenance, outages, webhook and integration problems." },
];

const looksLikeEmail = (v: string) => /^[^@\s<>,;:"\\]{1,64}@[^@\s<>,;:"\\]+\.[A-Za-z]{2,}$/.test(v.trim());

export function ContactsPage() {
  return (
    <>
      <PageHeader
        title="Email contacts"
        description="Where Zoiko iD sends notices for your organization. Addresses are stored encrypted and shown masked. Security, billing, privacy and incident notices always go to the contact for their purpose; optional product updates need a separate opt-in."
      />
      <div className="space-y-4">
        <Organization />
        <Mine />
      </div>
    </>
  );
}

function Organization() {
  const contacts = useCellQuery(KEY, (api) => api.contacts.list());
  const [purpose, setPurpose] = useState<string>("BILLING");
  const [address, setAddress] = useState("");
  const add = useCellMutation((api, v: { purpose: EmailContactPurpose; address: string }) => api.contacts.add(v), {
    invalidate: [KEY],
    success: "Contact added",
    onSuccess: () => setAddress(""),
  });
  const remove = useCellMutation((api, id: string) => api.contacts.remove(id), { invalidate: [KEY], success: "Contact removed" });
  const valid = looksLikeEmail(address);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (valid) add.mutate({ purpose: purpose as EmailContactPurpose, address: address.trim() });
  };
  const help = PURPOSES.find((p) => p.id === purpose)?.help;

  const columns: Column<EmailContact>[] = [
    { key: "purpose", header: "Purpose", pinned: true, render: (c) => PURPOSES.find((p) => p.id === c.purpose)?.label ?? c.purpose, sortValue: (c) => c.purpose },
    { key: "address", header: "Address", render: (c) => <span className="font-mono text-xs">{c.address}</span> },
    {
      key: "remove",
      header: "Actions",
      render: (c) => (
        <Button variant="secondary" onPress={() => remove.mutate(c.contact_id)} isDisabled={remove.isPending} aria-label={`Remove the ${c.purpose.toLowerCase()} contact ${c.address}`}>
          Remove
        </Button>
      ),
    },
  ];

  return (
    <>
      <Card title="Add a contact" description={help}>
        <form onSubmit={submit} className="flex flex-wrap items-end gap-3" aria-label="Add an email contact">
          <Select label="Purpose" options={PURPOSES.map((p) => ({ id: p.id, label: p.label }))} value={purpose} onChange={setPurpose} className="min-w-48" />
          <TextField
            label="Email address"
            type="email"
            value={address}
            onChange={setAddress}
            errorMessage={address && !valid ? "Enter a plain email address, such as billing@yourcompany.com." : undefined}
            className="min-w-72"
          />
          <Button type="submit" isDisabled={!valid || add.isPending}>
            Add contact
          </Button>
        </form>
        {add.isError ? <ApiErrorView error={add.error} title="The contact was not added" onRetry={() => add.reset()} /> : null}
      </Card>
      <QueryView query={contacts}>
        {(d) => (
          <Card title="Organization contacts" padded={false}>
            <DataTable<EmailContact>
              caption="Organization email contacts"
              columns={columns}
              rows={d.items}
              rowKey={(c) => c.contact_id}
              empty={
                <EmptyState title="No contacts yet" icon={Mail}>
                  Without a billing contact, invoices and payment notices cannot be emailed. They stay in the console.
                </EmptyState>
              }
            />
          </Card>
        )}
      </QueryView>
    </>
  );
}

function Mine() {
  const [address, setAddress] = useState("");
  const save = useCellMutation((api, a: string) => api.contacts.setMine(a), {
    success: "Check the new inbox: the change takes effect when you open the link we sent there",
    onSuccess: () => setAddress(""),
  });
  const valid = looksLikeEmail(address);
  return (
    <Card title="My security notices" description="Where notices about your own sign-in methods and sessions go. A new address takes effect only after you confirm it from that inbox; the old address is told.">
      <Banner tone="info" className="mb-3">
        Email is not a sign-in factor here and never approves anything: every link opens the console, where the action is confirmed.
      </Banner>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (valid) save.mutate(address.trim());
        }}
        className="flex flex-wrap items-end gap-3"
        aria-label="Set my security email"
      >
        <TextField label="My email address" type="email" value={address} onChange={setAddress} errorMessage={address && !valid ? "Enter a plain email address." : undefined} className="min-w-72" />
        <Button type="submit" isDisabled={!valid || save.isPending}>
          Save
        </Button>
      </form>
      {save.isError ? <ApiErrorView error={save.error} title="Your address was not saved" onRetry={() => save.reset()} /> : null}
      {save.isSuccess ? <p className="mt-2 text-sm text-ink-muted">Saved as {save.data.address}.</p> : null}
    </Card>
  );
}
