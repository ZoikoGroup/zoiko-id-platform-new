"use client";

import type { PrivacyRow } from "@zoiko/api-client";
import { Banner, Button, Card, DataTable, Dialog, EmptyState, RiskBadge, Select, StatusBadge, TextField, type Column } from "@zoiko/ui";
import { ClipboardCheck, ListChecks, Plus } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, listInput, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { cellValue, PRIVACY_KEY } from "./shared";

const PURPOSES_KEY = [...PRIVACY_KEY, "purposes"];
const ACTIVITIES_KEY = [...PRIVACY_KEY, "activities"];
const ROLES_KEY = [...PRIVACY_KEY, "roles"];
const RELEASE_KEY = [...PRIVACY_KEY, "release"];
const BASES = ["CONSENT", "CONTRACT", "LEGAL_OBLIGATION", "LEGITIMATE_INTERESTS", "PUBLIC_TASK", "VITAL_INTERESTS", "US_STATE_PERMISSION"];

function col(key: string, header: string, pinned = false): Column<PrivacyRow> {
  return { key, header, pinned, render: (r) => cellValue(r[key]), sortValue: (r) => String(r[key] ?? "") };
}

export function RegistrySection() {
  const purposes = useCellQuery(PURPOSES_KEY, (api) => api.privacy.admin.purposes());
  const activities = useCellQuery(ACTIVITIES_KEY, (api) => api.privacy.admin.activities());
  const roles = useCellQuery(ROLES_KEY, (api) => api.privacy.admin.roleMap());
  const release = useCellQuery(RELEASE_KEY, (api) => api.privacy.admin.releaseCheck());
  const [newPurpose, setNewPurpose] = useState(false);
  const [newActivity, setNewActivity] = useState(false);
  const [assigning, setAssigning] = useState<PrivacyRow | null>(null);

  const activatePurpose = useCellMutation((api, id: string) => api.privacy.admin.activatePurpose(id), {
    invalidate: [PURPOSES_KEY],
    success: "Purpose activated",
  });
  const activateActivity = useCellMutation((api, id: string) => api.privacy.admin.activateActivity(id), {
    invalidate: [ACTIVITIES_KEY, RELEASE_KEY],
    success: "Processing activity activated",
  });

  const purposeCols: Column<PrivacyRow>[] = [
    col("purpose_code", "Purpose", true),
    col("purpose_version", "Version"),
    { key: "status", header: "Status", render: (r) => <StatusBadge state={String(r.status)} kind="Purpose" /> },
    col("allowed_data_categories", "Data categories"),
    col("allowed_recipient_classes", "Recipients"),
    {
      key: "actions",
      header: "Actions",
      align: "end",
      render: (r) =>
        r.status === "DRAFT" ? (
          <Button size="xs" variant="secondary" pending={activatePurpose.isPending} onPress={() => activatePurpose.mutate(String(r.purpose_id))}>
            Review & activate
          </Button>
        ) : null,
    },
  ];
  const activityCols: Column<PrivacyRow>[] = [
    col("activity_code", "Activity", true),
    col("version", "Version"),
    { key: "status", header: "Status", render: (r) => <StatusBadge state={String(r.status)} kind="Processing activity" /> },
    col("lawful_basis", "Basis"),
    col("purpose_codes", "Purposes"),
    col("data_categories", "Data"),
    col("jurisdiction_pack_id", "Pack"),
    col("launch_flow", "Launch flow"),
    {
      key: "actions",
      header: "Actions",
      align: "end",
      render: (r) =>
        r.status === "RETIRED" ? null : (
          <span className="flex justify-end gap-1.5">
            <Button size="xs" variant="secondary" onPress={() => setAssigning(r)}>
              Assign role
            </Button>
            {r.status !== "ACTIVE" ? (
              <Button size="xs" pending={activateActivity.isPending} onPress={() => activateActivity.mutate(String(r.processing_activity_id))}>
                Activate
              </Button>
            ) : null}
          </span>
        ),
    },
  ];
  const roleCols: Column<PrivacyRow>[] = [
    col("zoiko_role", "Zoiko role", true),
    col("processing_activity_id", "Activity"),
    col("jurisdiction_pack_id", "Pack"),
    col("contract_or_dpa_ref", "DPA"),
    col("approval_ref", "Counsel approval"),
    { key: "status", header: "Status", render: (r) => <StatusBadge state={String(r.status)} kind="Role assignment" /> },
    col("effective_from", "Effective"),
  ];

  return (
    <div className="space-y-5">
      <QueryView query={release}>
        {(r) =>
          r.ready ? (
            <Banner tone="ok" icon={ClipboardCheck} title="Release check: every launch flow has an approved role assignment" />
          ) : (
            <Banner tone="warn" title={`Release check: ${r.blockers.length} launch flow(s) without an approved role assignment`}>
              These block controlled production (DEC-F3): {r.blockers.map((b) => b.activity_code).join(", ")}.
            </Banner>
          )
        }
      </QueryView>
      {activateActivity.error ? <ApiErrorView error={activateActivity.error} /> : null}
      {activatePurpose.error ? <ApiErrorView error={activatePurpose.error} /> : null}
      <Card
        padded={false}
        title="Purposes"
        description="Registered, versioned purposes. A purpose is immutable once active; activating needs a reviewer other than its author."
        actions={
          <Button size="sm" icon={<Plus aria-hidden className="size-4" />} onPress={() => setNewPurpose(true)}>
            New purpose
          </Button>
        }
      >
        <QueryView query={purposes}>
          {(d) => (
            <DataTable caption="Purposes" columns={purposeCols} rows={d.items} rowKey={(r) => String(r.purpose_id)} empty={<EmptyState icon={ListChecks} title="No purposes registered" />} />
          )}
        </QueryView>
      </Card>
      <Card
        padded={false}
        title="Processing activities"
        description="The unit of controller/processor role assignment. An activity cannot become active without an approved role assignment; D3 needs a DPIA."
        actions={
          <Button size="sm" icon={<Plus aria-hidden className="size-4" />} onPress={() => setNewActivity(true)}>
            New activity
          </Button>
        }
      >
        <QueryView query={activities}>
          {(d) => (
            <DataTable caption="Processing activities" columns={activityCols} rows={d.items} rowKey={(r) => String(r.processing_activity_id)} empty={<EmptyState icon={ListChecks} title="No processing activities" />} />
          )}
        </QueryView>
      </Card>
      <Card padded={false} title="Role map" description="Counsel-approved controller/processor roles per processing activity (DEC-F3).">
        <QueryView query={roles}>
          {(d) => <DataTable caption="Role map" columns={roleCols} rows={d.items} rowKey={(r) => String(r.assignment_id)} empty={<EmptyState icon={ListChecks} title="No role assignments" />} />}
        </QueryView>
      </Card>
      <PurposeDialog isOpen={newPurpose} onClose={() => setNewPurpose(false)} />
      <ActivityDialog isOpen={newActivity} onClose={() => setNewActivity(false)} />
      <RoleDialog activity={assigning} onClose={() => setAssigning(null)} />
    </div>
  );
}

function PurposeDialog({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const [code, setCode] = useState("");
  const [description, setDescription] = useState("");
  const [categories, setCategories] = useState("D1");
  const [recipients, setRecipients] = useState("");
  const create = useCellMutation(
    (api) =>
      api.privacy.admin.createPurpose({
        code: code.trim(),
        description: description.trim(),
        data_categories: listInput(categories),
        recipient_classes: listInput(recipients),
        compatible_secondary_purposes: [],
        prohibited_uses: [],
        retention_policy_code: null,
      }),
    { invalidate: [PURPOSES_KEY], success: "Draft purpose created", onSuccess: onClose },
  );
  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={(o) => !o && onClose()}
      title="Register a purpose"
      footer={
        <>
          <Button variant="secondary" onPress={onClose}>
            Cancel
          </Button>
          <Button pending={create.isPending} isDisabled={!/^[A-Z][A-Z0-9_]{1,63}$/.test(code) || description.trim().length < 10} onPress={() => create.mutate(undefined)}>
            Create draft
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <TextField label="Purpose code" value={code} onChange={(v) => setCode(v.toUpperCase())} isRequired mono placeholder="VERIFY_IDENTITY" />
        <TextField label="Description" value={description} onChange={setDescription} isRequired multiline rows={3} />
        <TextField label="Allowed data categories" value={categories} onChange={setCategories} mono description="D0-D4, comma separated." />
        <TextField label="Allowed recipient classes" value={recipients} onChange={setRecipients} mono />
        {create.error ? <ApiErrorView error={create.error} /> : null}
      </div>
    </Dialog>
  );
}

function ActivityDialog({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [purposes, setPurposes] = useState("");
  const [categories, setCategories] = useState("D1");
  const [basis, setBasis] = useState("CONTRACT");
  const [pack, setPack] = useState("uk-gdpr");
  const [dpia, setDpia] = useState("");
  const [special, setSpecial] = useState("");
  const [recipients, setRecipients] = useState("");
  const [launch, setLaunch] = useState("yes");
  const create = useCellMutation(
    (api) =>
      api.privacy.admin.createActivity({
        code: code.trim(),
        name: name.trim(),
        purpose_codes: listInput(purposes),
        data_categories: listInput(categories),
        lawful_basis: basis,
        jurisdiction_pack_id: pack,
        dpia_ref: dpia.trim() || null,
        special_category_condition: special.trim() || null,
        recipient_classes: listInput(recipients),
        launch_flow: launch === "yes",
        data_subject_classes: [],
        source_classes: [],
        retention_policy_code: null,
        residency_profile: null,
      }),
    { invalidate: [ACTIVITIES_KEY, RELEASE_KEY], success: "Draft activity created", onSuccess: onClose },
  );
  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={(o) => !o && onClose()}
      title="Register a processing activity"
      size="lg"
      footer={
        <>
          <Button variant="secondary" onPress={onClose}>
            Cancel
          </Button>
          <Button pending={create.isPending} isDisabled={!code || !name || !purposes} onPress={() => create.mutate(undefined)}>
            Create draft
          </Button>
        </>
      }
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <TextField label="Activity code" value={code} onChange={(v) => setCode(v.toUpperCase())} isRequired mono />
        <TextField label="Name" value={name} onChange={setName} isRequired />
        <TextField label="Purpose codes" value={purposes} onChange={(v) => setPurposes(v.toUpperCase())} isRequired mono />
        <TextField label="Data categories" value={categories} onChange={setCategories} mono />
        <Select label="Lawful basis / permission" value={basis} onChange={setBasis} options={BASES.map((b) => ({ id: b, label: b }))} />
        <TextField label="Jurisdiction pack" value={pack} onChange={setPack} mono />
        <TextField label="Recipient classes" value={recipients} onChange={setRecipients} mono />
        <TextField label="DPIA reference (D3)" value={dpia} onChange={setDpia} mono />
        <TextField label="Special-category condition (D3)" value={special} onChange={setSpecial} />
        <Select label="Launch flow" value={launch} onChange={setLaunch} options={[{ id: "yes", label: "Yes: covered by the release check" }, { id: "no", label: "No" }]} />
      </div>
      {create.error ? <ApiErrorView error={create.error} /> : null}
    </Dialog>
  );
}

function RoleDialog({ activity, onClose }: { activity: PrivacyRow | null; onClose: () => void }) {
  const [controller, setController] = useState("");
  const [role, setRole] = useState("PROCESSOR");
  const [pack, setPack] = useState("uk-gdpr");
  const [counsel, setCounsel] = useState("");
  const [dpa, setDpa] = useState("");
  const [approval, setApproval] = useState("");
  const id = String(activity?.processing_activity_id ?? "");
  const assign = useCellMutation(
    (api) =>
      api.privacy.admin.assignRole(id, {
        data_controller_principal_id: controller.trim(),
        zoiko_role: role,
        jurisdiction_pack_id: pack,
        approval_ref: counsel.trim(),
        contract_or_dpa_ref: dpa.trim() || null,
        approval_request_id: approval.trim(),
        processor_principal_ids: [],
        recipient_classes: [],
        valid_to: null,
      }),
    { invalidate: [ROLES_KEY, RELEASE_KEY], success: "Role assignment recorded", onSuccess: onClose },
  );
  return (
    <Dialog
      isOpen={activity !== null}
      onOpenChange={(o) => !o && onClose()}
      title={`Assign the Zoiko role for ${String(activity?.activity_code ?? "")}`}
      description="Legal owns this answer: record counsel's approval reference. A second administrator approves the exact assignment (bound to activity, role, pack and counsel reference)."
      size="lg"
      footer={
        <>
          <Button variant="secondary" onPress={onClose}>
            Cancel
          </Button>
          <Button pending={assign.isPending} isDisabled={controller.length !== 36 || counsel.trim().length < 3 || !approval.trim()} onPress={() => assign.mutate(undefined)}>
            Record assignment
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <RiskBadge risk="R3" />
        <TextField label="Data controller principal ID" value={controller} onChange={setController} isRequired mono />
        <Select
          label="Zoiko role"
          value={role}
          onChange={setRole}
          options={["CONTROLLER", "PROCESSOR", "INDEPENDENT_CONTROLLER", "JOINT_CONTROLLER"].map((r) => ({ id: r, label: r }))}
        />
        <TextField label="Jurisdiction pack" value={pack} onChange={setPack} mono />
        <TextField label="Counsel approval reference" value={counsel} onChange={setCounsel} isRequired mono />
        <TextField label="DPA / contract reference" value={dpa} onChange={setDpa} mono description="Required for the processor role." />
        <TextField label="Approval request ID" value={approval} onChange={setApproval} isRequired mono />
        {assign.error ? <ApiErrorView error={assign.error} /> : null}
      </div>
    </Dialog>
  );
}
