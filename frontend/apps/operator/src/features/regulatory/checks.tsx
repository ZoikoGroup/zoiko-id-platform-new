"use client";

import type { OperatorApi, PrivacyOverlayView } from "@zoiko/api-client";
import { Badge, Button, Card, Checkbox, DataTable, EmptyState, PageHeader, Select, TextField, type Column } from "@zoiko/ui";
import { ShieldQuestion } from "lucide-react";
import { useState, type ReactNode } from "react";

import { QueryView, useCellQuery } from "@/lib/op";

import { Hash, Mono, opt, REG, Reasons, upper, Verdict } from "./common";

const MONITORING = ["SANCTIONS_ONGOING", "PEP", "ADVERSE_MEDIA", "TRANSACTION_MONITORING", "BANK_OWNERSHIP", "CRYPTO_TRAVEL_RULE", "CRYPTO_WALLET_SCREENING"];
const RTR_ROUTES = [
  { id: "DIGITAL_IDSP", label: "Certified digital identity provider" },
  { id: "HOME_OFFICE_SHARE_CODE", label: "Home Office share code" },
  { id: "LANDLORD_MANUAL", label: "Landlord manual check" },
];

/** A small "ask, then show the answer" form. The question is only sent when the person presses Check. */
function Check<Q, R>({
  title,
  description,
  fields,
  question,
  ready,
  load,
  render,
  queryKey,
}: {
  title: string;
  description: ReactNode;
  fields: ReactNode;
  question: Q;
  ready: boolean;
  queryKey: string;
  load: (api: OperatorApi, q: Q) => Promise<R>;
  render: (r: R) => ReactNode;
}) {
  const [asked, setAsked] = useState<Q | null>(null);
  const q = useCellQuery<R>(["regulatory", queryKey, asked], (api) => load(api, asked as Q), { enabled: asked !== null });
  return (
    <Card title={title} description={description}>
      <form
        className="flex flex-wrap items-end gap-2"
        aria-label={title}
        onSubmit={(e) => {
          e.preventDefault();
          setAsked(question);
        }}
      >
        {fields}
        <Button type="submit" variant="secondary" isDisabled={!ready}>
          Check
        </Button>
      </form>
      {asked !== null ? <div className="mt-3 text-sm"><QueryView query={q}>{render}</QueryView></div> : null}
    </Card>
  );
}

function PrivacyRoute() {
  const [f, setF] = useState({ country: "", provider: "" });
  const [biometric, setBiometric] = useState(false);
  const [consent, setConsent] = useState(false);
  return (
    <Check
      title="Privacy route"
      description="Would this country's data go to this provider? The same rule the verification start applies."
      queryKey="privacy-route"
      question={{ country: upper(f.country), provider: f.provider.trim(), biometric, consent }}
      ready={f.country.trim().length === 2 && !!f.provider.trim()}
      load={(api, x) => api.regulatory.privacyRouteCheck(x.country, x.provider, x.biometric, x.consent)}
      fields={
        <>
          <TextField label="Country" value={f.country} onChange={(v) => setF((p) => ({ ...p, country: v }))} className="w-24" />
          <TextField label="Provider" value={f.provider} onChange={(v) => setF((p) => ({ ...p, provider: v }))} mono />
          <Checkbox isSelected={biometric} onChange={setBiometric}>
            Biometric
          </Checkbox>
          <Checkbox isSelected={consent} onChange={setConsent}>
            Consent given
          </Checkbox>
        </>
      }
      render={(r) => (
        <p className="flex flex-wrap items-center gap-2">
          <Verdict ok={r.allowed} yes="Allowed" no="Refused" />
          {r.region ? <span>region {r.region}</span> : null}
          {r.mechanism ? <span>via {r.mechanism}</span> : null}
          <Reasons reasons={r.reasons} none="" />
        </p>
      )}
    />
  );
}

function Monitoring() {
  const [country, setCountry] = useState("");
  const [kind, setKind] = useState("SANCTIONS_ONGOING");
  const [sector, setSector] = useState("");
  return (
    <Check
      title="Ongoing monitoring"
      description="May this kind of ongoing monitoring run in this country now? There is no global background monitoring by default: each needs an approval with a legal basis."
      queryKey="monitoring"
      question={{ country: upper(country), kind, sector: opt(upper(sector)) }}
      ready={country.trim().length === 2}
      load={(api, x) => api.regulatory.monitoringCheck(x.country, x.kind, x.sector)}
      fields={
        <>
          <TextField label="Country" value={country} onChange={setCountry} className="w-24" />
          <Select label="Kind" value={kind} onChange={setKind} options={MONITORING.map((m) => ({ id: m, label: m }))} />
          <TextField label="Sector" description="blank = any" value={sector} onChange={setSector} mono />
        </>
      }
      render={(r) => (
        <p className="flex flex-wrap items-center gap-2">
          <Verdict ok={r.allowed} yes="Allowed" no="Not allowed" />
          {r.approval_id ? <Mono>{r.approval_id}</Mono> : null}
          <Reasons reasons={r.reasons} none="" />
        </p>
      )}
    />
  );
}

function RightToRent() {
  const [f, setF] = useState({ country: "GB", subdivision: "GB-ENG", provider: "" });
  const [route, setRoute] = useState("DIGITAL_IDSP");
  return (
    <Check
      title="England Right to Rent"
      description="Does Right to Rent apply to this property, and may this route produce evidence now? The landlord always keeps the statutory responsibility."
      queryKey="right-to-rent"
      question={{ country: upper(f.country), subdivision: opt(upper(f.subdivision)), route, provider: opt(f.provider) }}
      ready={f.country.trim().length === 2}
      load={(api, x) => api.regulatory.rightToRentCheck(x.country, x.route, x.subdivision, x.provider)}
      fields={
        <>
          <TextField label="Country" value={f.country} onChange={(v) => setF((p) => ({ ...p, country: v }))} className="w-24" />
          <TextField label="Subdivision" value={f.subdivision} onChange={(v) => setF((p) => ({ ...p, subdivision: v }))} className="w-28" />
          <Select label="Route" value={route} onChange={setRoute} options={RTR_ROUTES} />
          <TextField label="Provider" description="for the digital route" value={f.provider} onChange={(v) => setF((p) => ({ ...p, provider: v }))} mono />
        </>
      }
      render={(r) => (
        <div className="space-y-1">
          <p className="flex flex-wrap items-center gap-2">
            <Badge tone={r.outcome === "ROUTE_AVAILABLE" ? "ok" : r.outcome === "BLOCKED" ? "danger" : r.outcome === "PRESCRIBED_ROUTE" ? "warn" : "neutral"}>{r.outcome}</Badge>
            {r.route ? <Mono>{r.route}</Mono> : null}
            <Reasons reasons={r.reasons} none="" />
          </p>
          <p className="text-ink-muted">
            Zoiko evidence: {r.zoiko_evidence ? "yes" : "no"} · statutory excuse by Zoiko: never · retention: {r.retention ?? "-"}
          </p>
        </div>
      )}
    />
  );
}

function Overlays() {
  const q = useCellQuery(REG.overlays, (api) => api.regulatory.privacyOverlays());
  const columns: Column<PrivacyOverlayView>[] = [
    { key: "country", header: "Country", pinned: true, render: (o) => <Mono>{`${o.country} v${o.version}`}</Mono> },
    { key: "home", header: "Home regions", render: (o) => o.home_regions.join(", ") },
    { key: "transfer", header: "Transfers", render: (o) => Object.entries(o.transfer_regions).map(([r, m]) => `${r} (${m})`).join(", ") || "none" },
    { key: "bio", header: "Biometrics", render: (o) => <Badge tone={o.biometrics === "PROHIBITED" ? "danger" : o.biometrics === "CONSENT_REQUIRED" ? "warn" : "ok"}>{o.biometrics}</Badge> },
    { key: "dpia", header: "DPIA", render: (o) => (o.dpia_required ? "required" : "-") },
    { key: "problems", header: "Usable", render: (o) => <Reasons reasons={o.problems} none="Yes" /> },
    { key: "hash", header: "Hash", render: (o) => <Hash value={o.overlay_hash} /> },
  ];
  return (
    <Card padded={false} title="Privacy overlays">
      <QueryView query={q}>
        {(d) => (
          <DataTable
            caption="Privacy overlays"
            columns={columns}
            rows={d.items}
            rowKey={(o) => o.country}
            empty={
              <EmptyState icon={ShieldQuestion} title="No privacy overlays">
                No country has an approved privacy overlay, so under enforce no personal data is routed anywhere.
              </EmptyState>
            }
          />
        )}
      </QueryView>
    </Card>
  );
}

export function ChecksPage() {
  return (
    <>
      <PageHeader title="Regulatory checks" description="Ask the same rules the platform applies, before a customer does. Asking changes nothing." />
      <div className="space-y-5">
        <Overlays />
        <PrivacyRoute />
        <Monitoring />
        <RightToRent />
      </div>
    </>
  );
}
