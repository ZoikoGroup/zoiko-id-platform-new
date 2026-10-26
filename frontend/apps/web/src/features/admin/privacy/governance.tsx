"use client";

import type {
  AdmProfile,
  AdmSignificance,
  PrivacyNotice,
  PrivacyProcessor,
  ProcessorRole,
} from "@zoiko/api-client";
import {
  Banner,
  Button,
  Card,
  Checkbox,
  Code,
  DataTable,
  DateTime,
  Dialog,
  EmptyState,
  Select,
  StatusBadge,
  TextField,
  type Column,
} from "@zoiko/ui";
import { Building, FileText, Plus, Workflow } from "lucide-react";
import { useState } from "react";

import { ApprovalStep } from "@/features/onboarding/shared";
import {
  ApiErrorView,
  QueryView,
  listInput,
  useCellMutation,
  useCellQuery,
} from "@/lib/cell";

const MANAGE = "privacy.processing_activity.manage";
const CATEGORIES = ["D0", "D1", "D2", "D3", "D4"];
const CODE = /^[A-Z][A-Z0-9_]{1,62}$/;

function Categories({
  value,
  onChange,
}: {
  value: string[];
  onChange: (v: string[]) => void;
}) {
  return (
    <fieldset className="space-y-2">
      <legend className="text-sm font-medium text-ink">Data categories</legend>
      <div className="flex flex-wrap gap-3">
        {CATEGORIES.map((c) => (
          <Checkbox
            key={c}
            isSelected={value.includes(c)}
            onChange={() =>
              onChange(
                value.includes(c)
                  ? value.filter((x) => x !== c)
                  : [...value, c],
              )
            }
          >
            {c}
          </Checkbox>
        ))}
      </div>
      <p className="text-xs text-ink-muted">
        D0 public · D1 internal · D2 personal · D3 identity/special · D4
        restricted.
      </p>
    </fieldset>
  );
}

/* ------------------------------------------------------------------------- processors */

function RegisterProcessor({ onClose }: { onClose: () => void }) {
  const [id, setId] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState<ProcessorRole>("PROCESSOR");
  const [cats, setCats] = useState<string[]>(["D2"]);
  const [locations, setLocations] = useState("");
  const [mechanism, setMechanism] = useState("");
  const [sla, setSla] = useState("");
  const [contract, setContract] = useState("");
  const add = useCellMutation(
    (api) =>
      api.privacy.registerProcessor({
        processor_id: id.trim(),
        name: name.trim(),
        role,
        data_categories: cats,
        locations: listInput(locations),
        transfer_mechanism: mechanism.trim() || null,
        deletion_sla_days: sla ? Number(sla) : null,
        contract_ref: contract.trim() || null,
      }),
    {
      invalidate: [["privacy", "processors"]],
      success: "Processor registered",
      onSuccess: onClose,
    },
  );
  const valid =
    /^[a-z0-9][a-z0-9_.-]{0,63}$/.test(id.trim()) &&
    name.trim().length >= 2 &&
    cats.length > 0 &&
    listInput(locations).length > 0;
  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && !add.isPending && onClose()}
      title="Register a processor"
      description="A company that processes personal data for you, or receives it. It processes nothing until approved."
      size="lg"
      footer={
        <>
          <Button variant="secondary" onPress={onClose}>
            Cancel
          </Button>
          <Button
            pending={add.isPending}
            isDisabled={!valid}
            onPress={() => add.mutate(undefined)}
          >
            Register
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <TextField
            label="Processor ID"
            isRequired
            mono
            value={id}
            onChange={setId}
            placeholder="email-provider"
          />
          <TextField label="Name" isRequired value={name} onChange={setName} />
          <Select
            label="Role"
            value={role}
            onChange={(v) => setRole(v as ProcessorRole)}
            options={[
              { id: "PROCESSOR", label: "Processor" },
              { id: "SUBPROCESSOR", label: "Sub-processor" },
              { id: "INDEPENDENT_CONTROLLER", label: "Independent controller" },
              { id: "RECIPIENT", label: "Recipient" },
            ]}
          />
          <TextField
            label="Processing locations"
            isRequired
            mono
            value={locations}
            onChange={setLocations}
            placeholder="GB, IE"
            description="Countries, comma separated."
          />
          <TextField
            label="Transfer mechanism"
            value={mechanism}
            onChange={setMechanism}
            placeholder="UK_IDTA"
          />
          <TextField
            label="Deletion SLA (days)"
            inputMode="numeric"
            value={sla}
            onChange={setSla}
          />
        </div>
        <Categories value={cats} onChange={setCats} />
        <TextField
          label="Contract reference"
          value={contract}
          onChange={setContract}
        />
        {add.error ? (
          <ApiErrorView error={add.error} title="Not registered" />
        ) : null}
      </div>
    </Dialog>
  );
}

export function ProcessorsSection() {
  const list = useCellQuery(["privacy", "processors"], (api) =>
    api.privacy.processors(),
  );
  const [adding, setAdding] = useState(false);
  const approve = useCellMutation(
    (api, id: string) => api.privacy.approveProcessor(id),
    { invalidate: [["privacy", "processors"]], success: "Approved" },
  );
  const suspend = useCellMutation(
    (api, id: string) => api.privacy.suspendProcessor(id),
    { invalidate: [["privacy", "processors"]], success: "Suspended" },
  );
  const columns: Column<PrivacyProcessor>[] = [
    {
      key: "name",
      header: "Processor",
      pinned: true,
      render: (p) => <span className="font-medium text-ink">{p.name}</span>,
      sortValue: (p) => p.name,
    },
    {
      key: "role",
      header: "Role",
      render: (p) => p.role.toLowerCase().replace(/_/g, " "),
    },
    {
      key: "cats",
      header: "Data",
      render: (p) => p.data_categories.join(", "),
    },
    { key: "loc", header: "Locations", render: (p) => p.locations.join(", ") },
    {
      key: "status",
      header: "Status",
      render: (p) => (
        <StatusBadge state={p.status} kind="Processor" size="xs" />
      ),
    },
    {
      key: "a",
      header: "",
      align: "end",
      render: (p) => (
        <div className="flex justify-end gap-2">
          {p.status !== "APPROVED" ? (
            <Button
              size="xs"
              pending={
                approve.isPending && approve.variables === p.processor_id
              }
              onPress={() => approve.mutate(p.processor_id)}
            >
              Approve
            </Button>
          ) : (
            <Button
              size="xs"
              variant="ghost"
              pending={
                suspend.isPending && suspend.variables === p.processor_id
              }
              onPress={() => suspend.mutate(p.processor_id)}
            >
              Suspend
            </Button>
          )}
        </div>
      ),
    },
  ];
  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Button
          icon={<Plus aria-hidden className="size-4" />}
          onPress={() => setAdding(true)}
        >
          Register processor
        </Button>
      </div>
      {approve.error ? <ApiErrorView error={approve.error} /> : null}
      {suspend.error ? <ApiErrorView error={suspend.error} /> : null}
      <Card padded={false}>
        <QueryView query={list}>
          {(r) =>
            r.items.length ? (
              <DataTable
                caption="Processors and recipients"
                columns={columns}
                rows={r.items}
                rowKey={(p) => p.processor_id}
              />
            ) : (
              <EmptyState title="No processors" icon={Building}>
                Register every company that processes or receives personal data
                for you, with where it processes it.
              </EmptyState>
            )
          }
        </QueryView>
      </Card>
      {adding ? <RegisterProcessor onClose={() => setAdding(false)} /> : null}
    </div>
  );
}

/* ---------------------------------------------------------------------------- notices */

async function sha256Hex(text: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(text),
  );
  return Array.from(new Uint8Array(digest), (b) =>
    b.toString(16).padStart(2, "0"),
  ).join("");
}

function PublishNotice({ onClose }: { onClose: () => void }) {
  const [code, setCode] = useState("");
  const [audience, setAudience] = useState("data_subjects");
  const [content, setContent] = useState("");
  const [uri, setUri] = useState("");
  const [change, setChange] = useState<string | null>(null);
  const publish = useCellMutation(
    (api, approvalId: string) =>
      api.privacy.publishNotice({
        code: code.trim(),
        audience: audience.trim(),
        content,
        content_uri: uri.trim() || null,
        approval_request_id: approvalId,
      }),
    {
      invalidate: [["privacy", "notices"]],
      success: "Notice published",
      onSuccess: onClose,
    },
  );
  const ready =
    CODE.test(code.trim()) &&
    audience.trim().length >= 2 &&
    content.length >= 20;
  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && !publish.isPending && onClose()}
      title="Publish a privacy notice"
      description="A new version of a notice. The approval is bound to this exact text: change it and a new approval is needed."
      size="lg"
      footer={
        <Button variant="secondary" onPress={onClose}>
          Close
        </Button>
      }
    >
      <div className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <TextField
            label="Notice code"
            isRequired
            mono
            value={code}
            onChange={(v) => {
              setCode(v.toUpperCase());
              setChange(null);
            }}
            placeholder="CUSTOMER_PRIVACY"
          />
          <TextField
            label="Audience"
            isRequired
            value={audience}
            onChange={setAudience}
          />
        </div>
        <TextField
          label="Notice text"
          isRequired
          multiline
          rows={8}
          value={content}
          onChange={(v) => {
            setContent(v);
            setChange(null);
          }}
        />
        <TextField
          label="Published at (URL, optional)"
          mono
          value={uri}
          onChange={setUri}
        />
        {change ? (
          <ApprovalStep
            action={MANAGE}
            resource="privacy_notice:*"
            change={change}
            label="Publish notice"
            onRun={(id) => publish.mutate(id)}
            pending={publish.isPending}
            error={publish.error}
          />
        ) : (
          <Button
            isDisabled={!ready}
            onPress={() =>
              void sha256Hex(content).then((h) =>
                setChange(`${code.trim()}|sha-256:${h}`),
              )
            }
          >
            Prepare for approval
          </Button>
        )}
      </div>
    </Dialog>
  );
}

export function NoticesSection() {
  const list = useCellQuery(["privacy", "notices"], (api) =>
    api.privacy.notices(),
  );
  const [publishing, setPublishing] = useState(false);
  const columns: Column<PrivacyNotice>[] = [
    {
      key: "code",
      header: "Notice",
      pinned: true,
      render: (n) => <Code>{n.notice_code}</Code>,
      sortValue: (n) => n.notice_code,
    },
    { key: "v", header: "Version", render: (n) => `v${n.version}` },
    { key: "aud", header: "Audience", render: (n) => n.audience },
    {
      key: "status",
      header: "Status",
      render: (n) => <StatusBadge state={n.status} kind="Notice" size="xs" />,
    },
    {
      key: "at",
      header: "Effective",
      render: (n) => <DateTime value={n.effective_at} />,
    },
    {
      key: "hash",
      header: "Content hash",
      render: (n) => <Code>{n.content_hash.slice(0, 18)}…</Code>,
      defaultHidden: true,
    },
  ];
  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Button
          icon={<Plus aria-hidden className="size-4" />}
          onPress={() => setPublishing(true)}
        >
          Publish notice
        </Button>
      </div>
      <Card padded={false}>
        <QueryView query={list}>
          {(r) =>
            r.items.length ? (
              <DataTable
                caption="Privacy notices"
                columns={columns}
                rows={r.items}
                rowKey={(n) => n.notice_id}
              />
            ) : (
              <EmptyState title="No notices" icon={FileText}>
                Publish the notices people see where their data is collected;
                every version is kept with its content hash.
              </EmptyState>
            )
          }
        </QueryView>
      </Card>
      {publishing ? (
        <PublishNotice onClose={() => setPublishing(false)} />
      ) : null}
    </div>
  );
}

/* ---------------------------------------------------------------- automated decisions */

function CreateAdm({ onClose }: { onClose: () => void }) {
  const [decision, setDecision] = useState("");
  const [purpose, setPurpose] = useState("");
  const [cats, setCats] = useState<string[]>(["D2"]);
  const [model, setModel] = useState("");
  const [solely, setSolely] = useState(false);
  const [significance, setSignificance] = useState<AdmSignificance>("MATERIAL");
  const [explanation, setExplanation] = useState("");
  const [review, setReview] = useState("");
  const create = useCellMutation(
    (api) =>
      api.privacy.createAdmProfile({
        decision_class: decision.trim(),
        purpose_code: purpose.trim(),
        input_data_categories: cats,
        model_or_rules_ref: model.trim(),
        solely_automated: solely,
        significance_class: significance,
        explanation_profile: explanation.trim(),
        human_review_route: review.trim(),
      }),
    {
      invalidate: [["privacy", "adm"]],
      success: "Profile created",
      onSuccess: onClose,
    },
  );
  const valid =
    decision.trim().length >= 3 &&
    CODE.test(purpose.trim()) &&
    cats.length &&
    model.trim() &&
    explanation.trim().length >= 10 &&
    review.trim().length >= 2;
  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && !create.isPending && onClose()}
      title="Describe an automated decision"
      description="Every automated decision about people needs a profile: what it decides, from which data, how it is explained and how a person can get a human review."
      size="lg"
      footer={
        <>
          <Button variant="secondary" onPress={onClose}>
            Cancel
          </Button>
          <Button
            pending={create.isPending}
            isDisabled={!valid}
            onPress={() => create.mutate(undefined)}
          >
            Create profile
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <TextField
            label="Decision"
            isRequired
            value={decision}
            onChange={setDecision}
            placeholder="payment_release_risk"
          />
          <TextField
            label="Purpose code"
            isRequired
            mono
            value={purpose}
            onChange={(v) => setPurpose(v.toUpperCase())}
            placeholder="FRAUD_PREVENTION"
          />
          <TextField
            label="Model or rules reference"
            isRequired
            mono
            value={model}
            onChange={setModel}
          />
          <Select
            label="Significance"
            value={significance}
            onChange={(v) => setSignificance(v as AdmSignificance)}
            options={[
              { id: "NONE", label: "No significant effect" },
              { id: "MATERIAL", label: "Material effect" },
              {
                id: "LEGAL_OR_SIMILARLY_SIGNIFICANT",
                label: "Legal or similarly significant",
              },
            ]}
          />
        </div>
        <Categories value={cats} onChange={setCats} />
        <Checkbox isSelected={solely} onChange={setSolely}>
          Solely automated (no human involved in the outcome)
        </Checkbox>
        {solely && significance === "LEGAL_OR_SIMILARLY_SIGNIFICANT" ? (
          <Banner tone="warn" title="Extra safeguards apply">
            A solely automated decision with legal or similarly significant
            effect needs an explanation, a human-review route and a legal basis.
          </Banner>
        ) : null}
        <TextField
          label="How the decision is explained"
          isRequired
          multiline
          rows={3}
          value={explanation}
          onChange={setExplanation}
        />
        <TextField
          label="Human review route"
          isRequired
          value={review}
          onChange={setReview}
          placeholder="Support → Decision review team"
        />
        {create.error ? (
          <ApiErrorView error={create.error} title="Not created" />
        ) : null}
      </div>
    </Dialog>
  );
}

export function AdmSection() {
  const list = useCellQuery(["privacy", "adm"], (api) =>
    api.privacy.admProfiles(),
  );
  const [creating, setCreating] = useState(false);
  const columns: Column<AdmProfile>[] = [
    {
      key: "d",
      header: "Decision",
      pinned: true,
      render: (a) => (
        <span className="font-medium text-ink">{a.decision_class}</span>
      ),
    },
    {
      key: "p",
      header: "Purpose",
      render: (a) => <Code>{a.purpose_code}</Code>,
    },
    {
      key: "s",
      header: "Significance",
      render: (a) => a.significance_class.toLowerCase().replace(/_/g, " "),
    },
    {
      key: "auto",
      header: "Solely automated",
      render: (a) => (a.solely_automated ? "Yes" : "No"),
    },
    {
      key: "st",
      header: "Status",
      render: (a) => <StatusBadge state={a.status} kind="Profile" size="xs" />,
    },
  ];
  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Button
          icon={<Plus aria-hidden className="size-4" />}
          onPress={() => setCreating(true)}
        >
          New profile
        </Button>
      </div>
      <Card padded={false}>
        <QueryView query={list}>
          {(r) =>
            r.items.length ? (
              <DataTable
                caption="Automated decision profiles"
                columns={columns}
                rows={r.items}
                rowKey={(a) => a.profile_id}
              />
            ) : (
              <EmptyState
                title="No automated decision profiles"
                icon={Workflow}
              >
                Describe each automated decision about people so it can be
                explained and reviewed by a human.
              </EmptyState>
            )
          }
        </QueryView>
      </Card>
      {creating ? <CreateAdm onClose={() => setCreating(false)} /> : null}
    </div>
  );
}
