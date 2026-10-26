"use client";

import type { DomainMethod, TenantDomain } from "@zoiko/api-client";
import {
  Banner,
  Button,
  Card,
  Code,
  DataTable,
  DateTime,
  Dialog,
  EmptyState,
  PageHeader,
  RadioCards,
  RelativeTime,
  StatusBadge,
  TextField,
  type Column,
} from "@zoiko/ui";
import { Check, Copy, Globe, Plus, RefreshCw } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { DOMAINS_KEY, GATES_KEY } from "./shared";

/** Consumer mail domains are never an enterprise domain (OVERLAY-001 §5); the cell refuses them too. */
const CONSUMER = /^(gmail|googlemail|outlook|hotmail|live|yahoo|icloud|me|aol|proton|protonmail|gmx|yandex|mail)\.[a-z.]+$/i;
const DOMAIN = /^(?=.{3,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/i;

function CopyValue({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div>
      <div className="mb-1 text-xs font-medium text-ink-muted">{label}</div>
      <div className="flex items-stretch gap-2">
        <code className="min-w-0 flex-1 overflow-x-auto rounded-control border border-border-strong bg-canvas px-3 py-2 font-mono text-xs text-ink">
          {value}
        </code>
        <Button
          variant="secondary"
          size="sm"
          onPress={() => void navigator.clipboard?.writeText(value).then(() => setCopied(true))}
          icon={copied ? <Check aria-hidden className="size-4 text-ok-fg" /> : <Copy aria-hidden className="size-4" />}
        >
          {copied ? "Copied" : "Copy"}
        </Button>
      </div>
    </div>
  );
}

/** What to publish so the cell can see the company controls the domain. */
function Challenge({ domain }: { domain: TenantDomain }) {
  const p = domain.publish;
  if (!p) return null;
  return (
    <div className="space-y-3">
      {domain.method === "DNS_TXT" ? (
        <>
          <p className="text-sm text-ink-muted">Add this TXT record at your DNS provider, then choose Verify. DNS changes can take a few minutes.</p>
          <CopyValue label="Record type" value={p.record_type ?? "TXT"} />
          <CopyValue label="Name / host" value={p.name ?? ""} />
          <CopyValue label="Value" value={p.value ?? ""} />
        </>
      ) : (
        <>
          <p className="text-sm text-ink-muted">Serve this text at exactly this HTTPS address (valid certificate, no redirects), then choose Verify.</p>
          <CopyValue label="URL" value={p.url ?? ""} />
          <CopyValue label="Response body" value={p.body ?? ""} />
        </>
      )}
      <p className="text-xs text-ink-muted">
        The challenge expires <DateTime value={domain.expires_at} />. Proving control of a domain does not prove who the company is; that comes from
        the register.
      </p>
    </div>
  );
}

function AddDomain({ onClose }: { onClose: () => void }) {
  const [domain, setDomain] = useState("");
  const [method, setMethod] = useState<DomainMethod>("DNS_TXT");
  const [added, setAdded] = useState<TenantDomain | null>(null);
  const add = useCellMutation((api, v: { domain: string; method: DomainMethod }) => api.tenant.addDomain(v.domain, v.method), {
    invalidate: [DOMAINS_KEY],
    onSuccess: (d) => setAdded(d),
  });
  const d = domain.trim().toLowerCase();
  const problem = !d ? null : !DOMAIN.test(d) ? "Enter a domain such as example.com." : CONSUMER.test(d) ? "Public e-mail domains cannot be claimed." : null;
  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && !add.isPending && onClose()}
      title={added ? `Verify ${added.domain}` : "Add a company domain"}
      description={added ? undefined : "Prove your company controls a domain. Verified domains route your people to this workspace."}
      size="lg"
      footer={
        added ? (
          <Button onPress={onClose}>Done</Button>
        ) : (
          <>
            <Button variant="secondary" isDisabled={add.isPending} onPress={onClose}>
              Cancel
            </Button>
            <Button pending={add.isPending} isDisabled={!d || !!problem} onPress={() => add.mutate({ domain: d, method })}>
              Add domain
            </Button>
          </>
        )
      }
    >
      {added ? (
        <Challenge domain={added} />
      ) : (
        <div className="space-y-4">
          <TextField label="Domain" isRequired mono value={domain} onChange={setDomain} placeholder="example.com" errorMessage={problem ?? undefined} />
          <RadioCards
            label="How to prove control"
            value={method}
            onChange={(v) => setMethod(v as DomainMethod)}
            columns={2}
            options={[
              { id: "DNS_TXT", label: "DNS TXT record", description: "Recommended. Add one record at your DNS provider." },
              { id: "HTTPS_WELL_KNOWN", label: "HTTPS file", description: "Serve a token at a well-known path on the domain." },
            ]}
          />
          {add.error ? <ApiErrorView error={add.error} title="The domain was not added" /> : null}
        </div>
      )}
    </Dialog>
  );
}

/** Domain verification (OVERLAY-001 §5): DNS TXT or HTTPS challenge, re-verified periodically. */
export function DomainsPage() {
  const domains = useCellQuery(DOMAINS_KEY, (api) => api.tenant.domains());
  const [adding, setAdding] = useState(false);
  const [open, setOpen] = useState<TenantDomain | null>(null);
  const verify = useCellMutation((api, id: string) => api.tenant.verifyDomain(id), {
    invalidate: [DOMAINS_KEY, GATES_KEY],
    success: (d) => (d.status === "VERIFIED" ? `${d.domain} verified` : `${d.domain} not verified yet`),
  });
  const revoke = useCellMutation((api, id: string) => api.tenant.revokeDomain(id), { invalidate: [DOMAINS_KEY, GATES_KEY], success: "Domain removed" });

  const columns: Column<TenantDomain>[] = [
    { key: "domain", header: "Domain", pinned: true, render: (d) => <span className="font-mono text-sm">{d.domain}</span>, sortValue: (d) => d.domain },
    { key: "status", header: "Status", render: (d) => <StatusBadge state={d.status} kind="Domain" size="xs" />, sortValue: (d) => d.status },
    { key: "method", header: "Method", render: (d) => (d.method === "DNS_TXT" ? "DNS TXT" : "HTTPS file") },
    { key: "verified", header: "Verified", render: (d) => <RelativeTime value={d.verified_at} /> },
    { key: "reverify", header: "Re-check due", render: (d) => <RelativeTime value={d.reverify_due_at} /> },
    {
      key: "actions",
      header: "",
      align: "end",
      render: (d) => (
        <div className="flex justify-end gap-2">
          {d.status === "PENDING" ? (
            <>
              <Button size="sm" variant="secondary" onPress={() => setOpen(d)}>
                Instructions
              </Button>
              <Button size="sm" pending={verify.isPending && verify.variables === d.domain_id} onPress={() => verify.mutate(d.domain_id)}>
                Verify
              </Button>
            </>
          ) : null}
          {d.status === "STALE" || d.status === "EXPIRED" ? (
            <Button size="sm" variant="secondary" icon={<RefreshCw aria-hidden className="size-4" />} onPress={() => setAdding(true)}>
              Prove again
            </Button>
          ) : null}
          {d.status !== "REVOKED" ? (
            <Button size="sm" variant="ghost" pending={revoke.isPending && revoke.variables === d.domain_id} onPress={() => revoke.mutate(d.domain_id)}>
              Remove
            </Button>
          ) : null}
        </div>
      ),
    },
  ];

  return (
    <div className="space-y-6">
      <PageHeader
        icon={Globe}
        title="Domains"
        description="Company domains you have proved control of. People signing in with these domains are routed to this workspace."
        breadcrumbs={[{ label: "Onboarding", href: "/admin/onboarding" }, { label: "Domains" }]}
        actions={
          <Button icon={<Plus aria-hidden className="size-4" />} onPress={() => setAdding(true)}>
            Add domain
          </Button>
        }
      />
      {verify.error ? <ApiErrorView error={verify.error} title="The domain was not verified" /> : null}
      {revoke.error ? <ApiErrorView error={revoke.error} title="The domain was not removed" /> : null}
      <Card padded={false}>
        <QueryView query={domains}>
          {(r) =>
            r.items.length ? (
              <DataTable caption="Company domains" columns={columns} rows={r.items} rowKey={(d) => d.domain_id} />
            ) : (
              <EmptyState
                title="No domains yet"
                icon={Globe}
                action={
                  <Button icon={<Plus aria-hidden className="size-4" />} onPress={() => setAdding(true)}>
                    Add domain
                  </Button>
                }
              >
                Verify at least one company domain to finish configuring the workspace.
              </EmptyState>
            )
          }
        </QueryView>
      </Card>
      <Banner tone="info" title="Good to know">
        Public e-mail domains (gmail.com, outlook.com and similar) cannot be claimed. Domains are re-checked periodically; if the proof disappears
        the domain becomes <Code>STALE</Code> and must be proved again.
      </Banner>
      {adding ? <AddDomain onClose={() => setAdding(false)} /> : null}
      {open ? (
        <Dialog isOpen onOpenChange={(o) => !o && setOpen(null)} title={`Verify ${open.domain}`} size="lg" footer={<Button onPress={() => setOpen(null)}>Close</Button>}>
          <Challenge domain={open} />
        </Dialog>
      ) : null}
    </div>
  );
}
