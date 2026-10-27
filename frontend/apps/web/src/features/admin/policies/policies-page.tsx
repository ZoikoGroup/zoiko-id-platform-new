"use client";

import type {
  PolicyReleaseSummary,
  PolicySimulation,
  PolicyTestCase,
  PolicyValidation,
} from "@zoiko/api-client";
import {
  Badge,
  Banner,
  Button,
  Card,
  Code,
  DataTable,
  DateTime,
  Dialog,
  EmptyState,
  KeyValues,
  PageHeader,
  StatusBadge,
  TextField,
  type Column,
} from "@zoiko/ui";
import { FileCode2, Plus, ShieldCheck } from "lucide-react";
import { useState, type ReactNode } from "react";

import {
  ApiErrorView,
  QueryView,
  useCellMutation,
  useCellQuery,
} from "@/lib/cell";

const KEY = ["policies", "releases"];
const BASE_CONTEXT = {
  tenant_match: true,
  authority_valid: true,
  authority_stale: false,
  security_state: "NORMAL",
  risk_class: 1,
  purpose: "",
};

const TEMPLATE_POLICY = `// Tenant policy narrows what authority already allows; the platform floor always applies on top.
@id("permit-with-authority")
permit (principal, action, resource) when { context.authority_valid };
`;
const TEMPLATE_CASES: PolicyTestCase[] = [
  {
    name: "authority permits",
    principal: "p",
    action: "resource.write",
    resource: "r",
    context: BASE_CONTEXT,
    expect_allow: true,
  },
  {
    name: "no authority denies",
    principal: "p",
    action: "resource.write",
    resource: "r",
    context: { ...BASE_CONTEXT, authority_valid: false },
    expect_allow: false,
  },
];

function parseCases(text: string): {
  cases?: PolicyTestCase[];
  error?: string;
} {
  try {
    const v = JSON.parse(text) as unknown;
    if (!Array.isArray(v) || !v.length)
      return { error: "Give at least one allow and one deny case." };
    return { cases: v as PolicyTestCase[] };
  } catch (e) {
    return { error: e instanceof Error ? e.message : "Not valid JSON." };
  }
}

function Editor({
  initialPolicies,
  initialCases,
  onSave,
  pending,
  error,
  nameField,
}: {
  initialPolicies: string;
  initialCases: PolicyTestCase[];
  onSave: (policies: string, cases: PolicyTestCase[]) => void;
  pending: boolean;
  error: unknown;
  nameField?: ReactNode;
}) {
  const [policies, setPolicies] = useState(initialPolicies);
  const [casesText, setCasesText] = useState(
    JSON.stringify(initialCases, null, 2),
  );
  const parsed = parseCases(casesText);
  return (
    <div className="space-y-4">
      {nameField}
      <TextField
        label="Cedar policies"
        isRequired
        multiline
        rows={10}
        mono
        value={policies}
        onChange={setPolicies}
      />
      <TextField
        label="Test cases (JSON)"
        isRequired
        multiline
        rows={10}
        mono
        value={casesText}
        onChange={setCasesText}
        errorMessage={parsed.error}
        description="Every change needs allow and deny cases (AUTHZ §18.1). Context: tenant_match, authority_valid, authority_stale, security_state, risk_class, purpose."
      />
      <Button
        pending={pending}
        isDisabled={!policies.trim() || !parsed.cases}
        onPress={() => parsed.cases && onSave(policies, parsed.cases)}
      >
        Save draft
      </Button>
      {error ? <ApiErrorView error={error} title="Not saved" /> : null}
    </div>
  );
}

function NewRelease({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (id: string) => void;
}) {
  const [name, setName] = useState("");
  const create = useCellMutation(
    (api, v: { policies: string; cases: PolicyTestCase[] }) =>
      api.policies.createDraft({ name: name.trim(), ...v }),
    {
      invalidate: [KEY],
      success: "Draft created",
      onSuccess: (r) => onCreated(r.release_id),
    },
  );
  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && !create.isPending && onClose()}
      title="New policy release"
      size="lg"
      footer={
        <Button variant="secondary" onPress={onClose}>
          Cancel
        </Button>
      }
    >
      <Editor
        initialPolicies={TEMPLATE_POLICY}
        initialCases={TEMPLATE_CASES}
        pending={create.isPending}
        error={create.error}
        onSave={(policies, cases) =>
          /^[a-z][a-z0-9-]{1,62}$/.test(name.trim()) &&
          create.mutate({ policies, cases })
        }
        nameField={
          <TextField
            label="Policy name"
            isRequired
            mono
            value={name}
            onChange={setName}
            placeholder="payments"
            description="Lower-case letters, digits and hyphens. Each save of a published policy is a new version."
          />
        }
      />
    </Dialog>
  );
}

function Detail({ id, onClose }: { id: string; onClose: () => void }) {
  const key = [...KEY, id];
  const q = useCellQuery(key, (api) => api.policies.release(id));
  const [report, setReport] = useState<PolicyValidation | null>(null);
  const [sim, setSim] = useState<PolicySimulation | null>(null);
  const [simReq, setSimReq] = useState(
    JSON.stringify(
      {
        principal: "p",
        action: "resource.write",
        resource: "r",
        context: BASE_CONTEXT,
      },
      null,
      2,
    ),
  );
  const refresh = [KEY, key];
  const edit = useCellMutation(
    (api, v: { policies: string; cases: PolicyTestCase[] }) =>
      api.policies.editDraft(id, v),
    {
      invalidate: refresh,
      success: "Draft saved",
      onSuccess: () => setReport(null),
    },
  );
  const validate = useCellMutation((api) => api.policies.validate(id), {
    invalidate: refresh,
    onSuccess: setReport,
  });
  const simulate = useCellMutation(
    (api) =>
      api.policies.simulate(
        id,
        JSON.parse(simReq) as {
          principal: string;
          action: string;
          resource: string;
          context: Record<string, unknown>;
        },
      ),
    { onSuccess: setSim },
  );
  const review = useCellMutation((api) => api.policies.review(id), {
    invalidate: refresh,
    success: "Review recorded",
  });
  const publish = useCellMutation((api) => api.policies.publish(id), {
    invalidate: refresh,
    success: "Release published",
  });
  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && onClose()}
      title="Policy release"
      size="lg"
      footer={<Button onPress={onClose}>Close</Button>}
    >
      <QueryView query={q}>
        {(r) => (
          <div className="space-y-4">
            <KeyValues
              columns={3}
              items={[
                [
                  "Status",
                  <StatusBadge
                    key="s"
                    state={r.status}
                    kind="Release"
                    size="xs"
                  />,
                ],
                [
                  "Content hash",
                  <Code key="h">{r.content_hash.slice(0, 16)}…</Code>,
                ],
                [
                  "Reviewers",
                  r.reviewers.length
                    ? `${r.reviewers.length}${r.high_risk ? " of 2" : " of 1"}`
                    : r.high_risk
                      ? "0 of 2"
                      : "0 of 1",
                ],
                [
                  "Published",
                  r.released_at ? (
                    <DateTime key="p" value={r.released_at} />
                  ) : (
                    "—"
                  ),
                ],
                [
                  "Risk",
                  r.high_risk ? (
                    <Badge key="r" tone="danger">
                      R3: two reviewers
                    </Badge>
                  ) : (
                    "Standard"
                  ),
                ],
              ]}
            />
            {r.status === "DRAFT" ? (
              <Editor
                initialPolicies={r.policies}
                initialCases={r.cases}
                onSave={(policies, cases) => edit.mutate({ policies, cases })}
                pending={edit.isPending}
                error={edit.error}
              />
            ) : (
              <pre className="max-h-64 overflow-auto rounded-control bg-canvas p-3 font-mono text-xs">
                {r.policies}
              </pre>
            )}
            <div className="flex flex-wrap gap-2">
              {r.status === "DRAFT" ? (
                <Button
                  icon={<ShieldCheck aria-hidden className="size-4" />}
                  pending={validate.isPending}
                  onPress={() => validate.mutate(undefined)}
                >
                  Validate
                </Button>
              ) : null}
              {r.status === "REVIEW" ? (
                <>
                  <Button
                    variant="secondary"
                    pending={review.isPending}
                    onPress={() => review.mutate(undefined)}
                  >
                    Approve as reviewer
                  </Button>
                  <Button
                    pending={publish.isPending}
                    onPress={() => publish.mutate(undefined)}
                  >
                    Publish
                  </Button>
                </>
              ) : null}
            </div>
            {validate.error ? <ApiErrorView error={validate.error} /> : null}
            {review.error ? <ApiErrorView error={review.error} /> : null}
            {publish.error ? <ApiErrorView error={publish.error} /> : null}
            {report ? (
              report.problems.length ? (
                <Banner tone="danger" title="Blocking checks failed">
                  <ul className="mt-1 list-disc space-y-0.5 pl-5 text-sm">
                    {report.problems.map((p) => (
                      <li key={p}>{p}</li>
                    ))}
                  </ul>
                </Banner>
              ) : (
                <Banner tone="ok" title="All checks passed: ready for review">
                  Newly allowed:{" "}
                  {report.privilege_diff.newly_allowed.join(", ") || "none"} ·
                  Newly denied:{" "}
                  {report.privilege_diff.newly_denied.join(", ") || "none"}
                </Banner>
              )
            ) : null}
            <Card
              title="Simulate a request"
              description="Evaluated under this release and under the active one; nothing changes."
            >
              <div className="space-y-3">
                <TextField
                  label="Request (JSON)"
                  multiline
                  rows={6}
                  mono
                  value={simReq}
                  onChange={setSimReq}
                />
                <Button
                  size="sm"
                  variant="secondary"
                  pending={simulate.isPending}
                  onPress={() => simulate.mutate(undefined)}
                >
                  Simulate
                </Button>
                {simulate.error ? (
                  <ApiErrorView error={simulate.error} />
                ) : null}
                {sim ? (
                  <KeyValues
                    columns={2}
                    items={[
                      [
                        "This release",
                        sim.candidate ? (
                          <StatusBadge
                            key="c"
                            state={sim.candidate.allowed ? "ALLOW" : "DENY"}
                            kind="Decision"
                          />
                        ) : (
                          "—"
                        ),
                      ],
                      [
                        "Active release",
                        sim.active ? (
                          <StatusBadge
                            key="a"
                            state={sim.active.allowed ? "ALLOW" : "DENY"}
                            kind="Decision"
                          />
                        ) : (
                          "same / none"
                        ),
                      ],
                    ]}
                  />
                ) : null}
              </div>
            </Card>
          </div>
        )}
      </QueryView>
    </Dialog>
  );
}

/** Policy administration (UX-001 §10; AUTHZ-001 §18-19): draft, validate, simulate, review, publish. */
export function PoliciesPage() {
  const list = useCellQuery(KEY, (api) => api.policies.releases());
  const [creating, setCreating] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const columns: Column<PolicyReleaseSummary>[] = [
    {
      key: "name",
      header: "Policy",
      pinned: true,
      render: (r) => <span className="font-mono text-sm">{r.policy_name}</span>,
      sortValue: (r) => r.policy_name,
    },
    {
      key: "v",
      header: "Version",
      render: (r) => `v${r.version}`,
      sortValue: (r) => r.version,
    },
    {
      key: "status",
      header: "Status",
      render: (r) => <StatusBadge state={r.status} kind="Release" size="xs" />,
    },
    {
      key: "cases",
      header: "Cases",
      align: "end",
      render: (r) => r.case_count,
    },
    {
      key: "rev",
      header: "Reviewers",
      align: "end",
      render: (r) => r.reviewers.length,
    },
    {
      key: "pub",
      header: "Published",
      render: (r) => <DateTime value={r.released_at} />,
    },
    {
      key: "a",
      header: "",
      align: "end",
      render: (r) => (
        <Button
          size="xs"
          variant="secondary"
          onPress={() => setOpen(r.release_id)}
        >
          Open
        </Button>
      ),
    },
  ];
  return (
    <div className="space-y-6">
      <PageHeader
        icon={FileCode2}
        title="Policies"
        description="Your company's authorization policy releases. A release is tested, reviewed by someone other than its author, and then published; published releases never change."
        actions={
          <Button
            icon={<Plus aria-hidden className="size-4" />}
            onPress={() => setCreating(true)}
          >
            New release
          </Button>
        }
      />
      <Card padded={false}>
        <QueryView query={list}>
          {(r) =>
            r.items.length ? (
              <DataTable
                caption="Policy releases"
                columns={columns}
                rows={r.items}
                rowKey={(x) => x.release_id}
              />
            ) : (
              <EmptyState title="No policy releases" icon={FileCode2}>
                The platform baseline applies until your company publishes its
                own policy.
              </EmptyState>
            )
          }
        </QueryView>
      </Card>
      {creating ? (
        <NewRelease
          onClose={() => setCreating(false)}
          onCreated={(id) => {
            setCreating(false);
            setOpen(id);
          }}
        />
      ) : null}
      {open ? <Detail id={open} onClose={() => setOpen(null)} /> : null}
    </div>
  );
}
