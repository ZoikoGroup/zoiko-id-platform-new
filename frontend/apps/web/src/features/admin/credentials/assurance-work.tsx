"use client";

import type { UatEvidence, UatFixture, UatRun, UatVerifyResult } from "@zoiko/api-client";
import { Banner, Button, Card, DataTable, EmptyState, StatusBadge, type Column } from "@zoiko/ui";
import { FileCheck2 } from "lucide-react";
import { useRef, useState } from "react";

import { ApiErrorView, useCellMutation, useCellQuery } from "@/lib/cell";

import { AdminQueryView } from "../identity-providers/shared";
import { badge, parseBundle, verifyWords } from "./assurance-status";

const FIXTURES_KEY = ["admin", "assurance", "fixtures"];
const RUNS_KEY = ["admin", "assurance", "runs"];
const ROLES = ["QA_LEAD", "CTO", "SECURITY_GRC", "PRIVACY", "COUNTRY_LEGAL"] as const;

async function sha256Hex(file: Blob): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

const fixtureBadge = (s: string) => (s === "APPROVED" ? "ACTIVE" : s === "REJECTED" ? "REJECTED" : "PENDING");

/** Fixture authoring and review (WF-02). Only the file's digest leaves the browser. A second person approves; an approved fixture is locked. */
export function FixturesCard() {
  const list = useCellQuery(FIXTURES_KEY, (api) => api.assurance.fixtures());
  const input = useRef<HTMLInputElement>(null);
  const [name, setName] = useState("");
  const [rationale, setRationale] = useState("");
  const create = useCellMutation(
    async (api, v: { name: string; file: File }) => api.assurance.createFixture({ name: v.name, content_sha256: await sha256Hex(v.file) }),
    { invalidate: [FIXTURES_KEY], success: "The fixture was recorded as a draft" },
  );
  const review = useCellMutation((api, v: { f: UatFixture; approve: boolean }) => api.assurance.reviewFixture(v.f.fixture_id, { revision: v.f.revision, approve: v.approve, rationale }), {
    invalidate: [FIXTURES_KEY],
    success: (r) => `The revision is now ${r.state.toLowerCase()}`,
  });
  const columns: Column<UatFixture>[] = [
    { key: "name", header: "Fixture", pinned: true, render: (f) => f.name },
    { key: "rev", header: "Revision", render: (f) => f.revision },
    { key: "state", header: "State", render: (f) => (<><StatusBadge state={fixtureBadge(f.state)} size="xs" /> {f.state.toLowerCase()}</>) },
    { key: "hash", header: "Digest", render: (f) => <span className="font-mono text-xs">{f.content_sha256.slice(0, 12)}…</span> },
    {
      key: "actions",
      header: "",
      render: (f) =>
        f.state === "DRAFT" ? (
          <div className="flex gap-2">
            <Button size="sm" variant="secondary" isDisabled={review.isPending || !rationale.trim()} onClick={() => review.mutate({ f, approve: true })}>
              Approve
            </Button>
            <Button size="sm" variant="secondary" isDisabled={review.isPending || !rationale.trim()} onClick={() => review.mutate({ f, approve: false })}>
              Reject
            </Button>
          </div>
        ) : null,
    },
  ];
  return (
    <Card title="Fixtures">
      <div className="space-y-4">
        <form
          className="flex flex-wrap items-end gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            const file = input.current?.files?.[0];
            if (file && name.trim()) create.mutate({ name: name.trim(), file });
          }}
        >
          <label className="text-sm">
            Name
            <input className="ml-2 rounded border px-2 py-1" value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <input ref={input} type="file" aria-label="Choose the fixture file (only its digest is sent)" className="text-sm" />
          <Button type="submit" isDisabled={create.isPending || !name.trim()}>
            Record draft
          </Button>
        </form>
        {create.isError ? <ApiErrorView error={create.error} title="The fixture was not recorded" /> : null}
        <label className="block text-sm">
          Reason for the review (required to approve or reject; the author cannot review their own fixture)
          <input className="mt-1 block w-full rounded border px-2 py-1" value={rationale} onChange={(e) => setRationale(e.target.value)} />
        </label>
        {review.isError ? <ApiErrorView error={review.error} title="The review was not accepted" /> : null}
        <AdminQueryView query={list}>
          {(r) => (r.items.length ? <DataTable caption="Fixtures" columns={columns} rows={r.items} rowKey={(f) => `${f.fixture_id}:${f.revision}`} /> : <EmptyState title="No fixtures yet">Synthetic fixtures appear here with their review state.</EmptyState>)}
        </AdminQueryView>
      </div>
    </Card>
  );
}

/** Case replay (WF-03): each run with its signed manifests and whether its event chain holds. A manifest shows the verdict the evidence supports. */
export function ReplayCard() {
  const runs = useCellQuery(RUNS_KEY, (api) => api.assurance.runs());
  const [audience, setAudience] = useState("auditor");
  const [shown, setShown] = useState<UatEvidence | null>(null);
  const open = useCellMutation((api, runId: string) => api.assurance.evidence(runId, audience), { onSuccess: (r) => setShown(r) });
  const columns: Column<UatRun>[] = [
    { key: "run", header: "Run", pinned: true, render: (r) => <span className="font-mono text-xs">{r.run_id.slice(0, 8)}</span> },
    { key: "env", header: "Environment", render: (r) => r.environment_id },
    { key: "unit", header: "Release unit", render: (r) => [r.release_unit.country, r.release_unit.service].filter(Boolean).join(" · ") || "—" },
    { key: "counts", header: "Events · manifests", render: (r) => `${r.events} · ${r.manifests}` },
    {
      key: "actions",
      header: "",
      render: (r) => (
        <Button size="sm" variant="secondary" isDisabled={open.isPending} onClick={() => open.mutate(r.run_id)}>
          Replay
        </Button>
      ),
    },
  ];
  return (
    <Card title="Case replay">
      <div className="space-y-4">
        <label className="text-sm">
          View as
          <select className="ml-2 rounded border px-2 py-1" value={audience} onChange={(e) => setAudience(e.target.value)}>
            <option value="auditor">Auditor (full)</option>
            <option value="regulator">Regulator (no tester identity)</option>
            <option value="customer">Customer (verdicts only)</option>
          </select>
        </label>
        <AdminQueryView query={runs}>
          {(r) => (r.items.length ? <DataTable caption="Test runs" columns={columns} rows={r.items} rowKey={(x) => x.run_id} /> : <EmptyState title="No runs yet">Runs appear here once an independent tester starts one.</EmptyState>)}
        </AdminQueryView>
        {open.isError ? <ApiErrorView error={open.error} title="The run could not be read" /> : null}
        {shown ? (
          <div className="space-y-2 text-sm">
            <p>
              <StatusBadge state={shown.chain_problems.length ? "REJECTED" : "ACTIVE"} size="xs" />{" "}
              {shown.chain_problems.length ? `The event chain does not hold: ${shown.chain_problems.join(", ").toLowerCase().replaceAll("_", " ")}` : `The event chain holds (${shown.events} events)`}
            </p>
            <ul className="space-y-1">
              {shown.manifests.map((m) => (
                <li key={m.manifest_id}>
                  <span className="font-mono text-xs">{m.case_id}</span> · <StatusBadge state={badge(m.verdict)} size="xs" /> {m.verdict.toLowerCase().replace("_", " ")}
                  {m.problems?.length ? <span className="text-ink-muted"> · {m.problems.join(", ").toLowerCase().replaceAll("_", " ")}</span> : null}
                </li>
              ))}
            </ul>
            <p className="text-xs text-ink-muted">{shown.note}</p>
          </div>
        ) : null}
      </div>
    </Card>
  );
}

/** Evidence builder (WF-09): verify a manifest bundle the way a third party would. A failure says so and locks the releases built on it. */
export function EvidenceBuilderCard() {
  const [text, setText] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [result, setResult] = useState<UatVerifyResult | null>(null);
  const verify = useCellMutation((api, bundle: Parameters<typeof api.assurance.verify>[0]) => api.assurance.verify(bundle), { onSuccess: (r) => setResult(r) });
  return (
    <Card title="Evidence builder">
      <form
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          const parsed = parseBundle(text);
          if (!parsed.ok) {
            setProblem(parsed.error);
            return;
          }
          setProblem(null);
          verify.mutate(parsed.bundle);
        }}
      >
        <label className="block text-sm">
          A stored manifest id, or an exported bundle (manifest, jws and optionally events) as JSON
          <textarea className="mt-1 block h-28 w-full rounded border px-2 py-1 font-mono text-xs" value={text} onChange={(e) => setText(e.target.value)} />
        </label>
        <Button type="submit" icon={<FileCheck2 aria-hidden className="size-4" />} isDisabled={verify.isPending}>
          Verify
        </Button>
      </form>
      {problem ? <p role="alert" className="mt-2 text-sm">{problem}</p> : null}
      {verify.isError ? <ApiErrorView error={verify.error} title="The bundle could not be checked" /> : null}
      {result ? (
        <p className="mt-3 text-sm">
          <StatusBadge state={result.valid ? "ACTIVE" : "REJECTED"} size="xs" /> {verifyWords(result)}
        </p>
      ) : null}
    </Card>
  );
}

/** Release signing and rollback (WF-10). The server checks the role is yours, that you are independent of the build, and that nothing is missing. */
export function ReleaseSigningCard() {
  const [releaseId, setReleaseId] = useState("");
  const [role, setRole] = useState<(typeof ROLES)[number]>("QA_LEAD");
  const [decision, setDecision] = useState<"APPROVE" | "REJECT">("APPROVE");
  const [rationale, setRationale] = useState("");
  const [provider, setProvider] = useState("");
  const [service, setService] = useState("");
  const [reason, setReason] = useState("");
  const sign = useCellMutation((api, id: string) => api.assurance.decide(id, { role, decision, rationale }), {
    success: (r) => `Signed. The release now reads ${r.candidate_status === "GO" ? "go" : "no go"}`,
  });
  const rollback = useCellMutation(
    (api, id: string) => api.assurance.rollback(id, { scope: provider && service ? { provider_id: provider, service_code: service } : { scope: "release" }, reason }),
    { success: (r) => (r.provider_route_closed ? "Rolled back and the provider route was closed" : "Rolled back") },
  );
  const ready = releaseId.trim().length > 0;
  return (
    <Card title="Release decisions">
      <div className="space-y-4">
        <Banner tone="info" icon={FileCheck2}>
          A decision is signed under the role you hold. A product owner, CEO or operator has no route around a failed gate, and a release that is missing evidence stays no go.
        </Banner>
        <label className="block text-sm">
          Release candidate id
          <input className="ml-2 w-80 rounded border px-2 py-1 font-mono text-xs" value={releaseId} onChange={(e) => setReleaseId(e.target.value)} />
        </label>
        <div className="flex flex-wrap items-end gap-3">
          <label className="text-sm">
            Role
            <select className="ml-2 rounded border px-2 py-1" value={role} onChange={(e) => setRole(e.target.value as (typeof ROLES)[number])}>
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {r.toLowerCase().replace("_", " ")}
                </option>
              ))}
            </select>
          </label>
          <label className="text-sm">
            Decision
            <select className="ml-2 rounded border px-2 py-1" value={decision} onChange={(e) => setDecision(e.target.value as "APPROVE" | "REJECT")}>
              <option value="APPROVE">Approve</option>
              <option value="REJECT">Reject</option>
            </select>
          </label>
          <label className="min-w-64 flex-1 text-sm">
            Reason
            <input className="ml-2 w-full rounded border px-2 py-1" value={rationale} onChange={(e) => setRationale(e.target.value)} />
          </label>
          <Button isDisabled={!ready || !rationale.trim() || sign.isPending} onClick={() => sign.mutate(releaseId.trim())}>
            Sign decision
          </Button>
        </div>
        {sign.isError ? <ApiErrorView error={sign.error} title="The decision was not accepted" /> : null}
        <div className="flex flex-wrap items-end gap-3 border-t pt-4">
          <label className="text-sm">
            Provider
            <input className="ml-2 rounded border px-2 py-1" value={provider} onChange={(e) => setProvider(e.target.value)} />
          </label>
          <label className="text-sm">
            Service
            <input className="ml-2 rounded border px-2 py-1" value={service} onChange={(e) => setService(e.target.value)} />
          </label>
          <label className="min-w-64 flex-1 text-sm">
            Reason for the emergency disable
            <input className="ml-2 w-full rounded border px-2 py-1" value={reason} onChange={(e) => setReason(e.target.value)} />
          </label>
          <Button variant="secondary" isDisabled={!ready || reason.trim().length < 3 || rollback.isPending} onClick={() => rollback.mutate(releaseId.trim())}>
            Roll back
          </Button>
        </div>
        {rollback.isError ? <ApiErrorView error={rollback.error} title="The rollback was not accepted" /> : null}
      </div>
    </Card>
  );
}
