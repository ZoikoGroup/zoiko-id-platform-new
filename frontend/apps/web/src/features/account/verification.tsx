"use client";

import {
  ApiError,
  type VerificationAssertion,
  type VerificationSession,
} from "@zoiko/api-client";
import { useQueryClient } from "@tanstack/react-query";
import {
  AssuranceBadge,
  Banner,
  Button,
  Card,
  CopyId,
  DataTable,
  DateTime,
  EmptyState,
  KeyValues,
  PageHeader,
  Select,
  StatusBadge,
  TextField,
  humanize,
  type Column,
} from "@zoiko/ui";
import { Lock, ScanFace } from "lucide-react";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";

import {
  ApiErrorView,
  QueryView,
  useCellMutation,
  useCellQuery,
} from "@/lib/cell";
import { useRuntime } from "@/lib/runtime";

import { DevProvider, DevSetup } from "./dev-provider";

const PENDING: ReadonlySet<string> = new Set([
  "CREATED",
  "IN_PROGRESS",
  "PENDING_EXTERNAL",
  "MANUAL_REVIEW",
]);
const POLL_MS = 4000;

function isScopeError(e: unknown) {
  return e instanceof ApiError && e.code === "SCOPE_INSUFFICIENT";
}

/** SCOPE_INSUFFICIENT gets a plain explanation; everything else the uniform error view. */
function VerificationError({
  error,
  onRetry,
}: {
  error: unknown;
  onRetry?: () => void;
}) {
  if (isScopeError(error)) {
    return (
      <Banner
        tone="warn"
        icon={Lock}
        title="Your sign-in doesn't allow this"
        live="polite"
      >
        This app&apos;s access to your account doesn&apos;t include identity
        verification (
        {(error as ApiError).message.replace(/ is required\.$/, "")}). Sign out
        and sign in again; if it still happens, ask your administrator to enable
        verification for this app.
      </Banner>
    );
  }
  return <ApiErrorView error={error} onRetry={onRetry} />;
}

const STATUS_COPY: Record<string, string> = {
  CREATED:
    "The check has been created and is being sent to the verification provider.",
  IN_PROGRESS: "The verification provider is checking your evidence.",
  PENDING_EXTERNAL:
    "Waiting for the verification provider. Complete any steps the provider sent you; this page updates on its own.",
  MANUAL_REVIEW:
    "A reviewer is looking at your check. This can take a while; you can leave this page.",
  VERIFIED:
    "Your identity was verified. Your assurance level below is up to date.",
  FAILED: "The check could not verify your identity.",
  CANCELED: "The check was cancelled.",
  EXPIRED: "The check expired before it finished.",
};

function claimRows(claims: Record<string, unknown> | undefined) {
  return Object.entries(claims ?? {}).map(([claim, raw]) => {
    const c = (raw ?? {}) as {
      level?: string;
      method?: string;
      valid_until?: string | null;
    };
    return {
      claim,
      level: c.level,
      method: c.method,
      validUntil: c.valid_until ?? null,
    };
  });
}

export function VerificationPage() {
  const { session } = useRuntime();
  const qc = useQueryClient();
  const sub = session?.authenticated ? session.subject.sub : undefined;

  const assurance = useCellQuery(
    ["me", "assurance", sub],
    (api) => api.verification.assurance(sub!),
    { enabled: Boolean(sub) },
  );
  const assertions = useCellQuery(
    ["me", "verification-assertions", sub],
    (api) => api.verification.assertions(sub!),
    {
      enabled: Boolean(sub),
    },
  );
  const profiles = useCellQuery(["verification", "profiles"], (api) =>
    api.verification.profiles(),
  );

  const [profileId, setProfileId] = useState<string | null>(null);
  const [jurisdiction, setJurisdiction] = useState<string | null>(null);
  const [purpose, setPurpose] = useState("account_verification");
  const [verificationId, setVerificationId] = useState<string | null>(null);

  const active = (profiles.data?.items ?? []).filter(
    (p) => p.status === "ACTIVE",
  );
  const profile = active.find((p) => p.profile_id === profileId) ?? null;
  const jurisdictions = profile?.jurisdictions ?? [];

  const start = useCellMutation(
    (
      api,
      body: { profile_id: string; jurisdiction: string; purpose: string },
    ) => api.verification.start({ ...body, subject_principal_id: sub! }),
    {
      success: "Verification started",
      onSuccess: (r) => setVerificationId(r.verification_id),
    },
  );

  const startedStatus =
    start.data?.verification_id === verificationId
      ? start.data?.status
      : undefined;
  const sessionKey = ["verification", "session", verificationId];
  // Last known state (the query re-renders this component whenever it changes).
  const known =
    qc.getQueryData<VerificationSession>(sessionKey)?.status ?? startedStatus;
  const pending = Boolean(verificationId) && (!known || PENDING.has(known));
  const current = useCellQuery(
    sessionKey,
    (api) => api.verification.get(verificationId!),
    {
      enabled: Boolean(verificationId),
      // Poll while the check is in flight; stops once it reaches a final state.
      refetchInterval: pending ? POLL_MS : undefined,
    },
  );
  const status = current.data?.status ?? startedStatus;

  const cancel = useCellMutation(
    (api, id: string) => api.verification.cancel(id),
    {
      invalidate: [sessionKey],
      success: "Verification cancelled",
    },
  );

  // Verifying again supersedes the finished check with a new one; the earlier check stays on record.
  const reverify = useCellMutation((api, id: string) => api.verification.reverify(id), {
    success: "A new verification was started",
    onSuccess: (r) => {
      setVerificationId(r.verification_id);
      cancel.reset();
    },
  });

  // A finished check changes the assurance and assertions: refresh them once.
  useEffect(() => {
    if (status && !PENDING.has(status)) {
      void qc.invalidateQueries({ queryKey: ["me", "assurance"] });
      void qc.invalidateQueries({
        queryKey: ["me", "verification-assertions"],
      });
    }
  }, [status, qc]);

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!profileId || !jurisdiction || !purpose.trim()) return;
    start.mutate({
      profile_id: profileId,
      jurisdiction,
      purpose: purpose.trim(),
    });
  }

  const columns: Column<VerificationAssertion>[] = [
    {
      key: "claim",
      header: "Claim",
      pinned: true,
      render: (a) => humanize(a.claim_type),
      sortValue: (a) => a.claim_type,
    },
    {
      key: "level",
      header: "Assurance",
      render: (a) => (
        <AssuranceBadge kind="Identity" level={a.assurance_level} />
      ),
      sortValue: (a) => a.assurance_level,
    },
    {
      key: "method",
      header: "Method",
      render: (a) => humanize(a.verification_method),
    },
    {
      key: "evidence",
      header: "Evidence",
      render: (a) => a.evidence_types.map(humanize).join(", ") || "—",
      defaultHidden: true,
    },
    {
      key: "status",
      header: "Status",
      render: (a) => <StatusBadge state={a.status} kind="Assertion" />,
      sortValue: (a) => a.status,
    },
    {
      key: "verified",
      header: "Verified",
      render: (a) => <DateTime value={a.verified_at} />,
      sortValue: (a) => a.verified_at ?? "",
    },
    {
      key: "fresh",
      header: "Fresh until",
      render: (a) => <DateTime value={a.fresh_until} style="date" />,
      sortValue: (a) => a.fresh_until ?? "",
    },
    {
      key: "id",
      header: "Assertion",
      render: (a) => <CopyId value={a.assertion_id} label="assertion" />,
      defaultHidden: true,
    },
  ];

  const check: VerificationSession | undefined = current.data ?? start.data;

  return (
    <>
      <PageHeader
        title="Identity verification"
        description="How strongly your identity has been verified, and the checks behind it. Only levels and dates are shown here, never your documents or provider data."
      />

      <div className="grid gap-5 lg:grid-cols-2">
        <Card
          title="Your assurance"
          description="The highest current identity assurance across your valid checks."
        >
          {assurance.isError ? (
            <VerificationError
              error={assurance.error}
              onRetry={() => void assurance.refetch()}
            />
          ) : (
            <QueryView query={assurance} loadingRows={2}>
              {(a) => {
                const rows = claimRows(a.identity_proofing?.claims);
                const level = a.identity_proofing?.level;
                return (
                  <div className="space-y-4">
                    <KeyValues
                      columns={1}
                      items={[
                        [
                          "Identity assurance",
                          <AssuranceBadge
                            key="l"
                            kind="Identity"
                            level={level === "IAL-Z0" ? null : level}
                          />,
                        ],
                      ]}
                    />
                    {rows.length ? (
                      <ul className="divide-y divide-border rounded-control border border-border text-sm">
                        {rows.map((r) => (
                          <li
                            key={r.claim}
                            className="flex flex-wrap items-center justify-between gap-2 px-3 py-2"
                          >
                            <span className="text-ink">
                              {humanize(r.claim)}
                            </span>
                            <span className="flex items-center gap-2 text-xs text-ink-muted">
                              {r.level ? (
                                <AssuranceBadge
                                  kind="Identity"
                                  level={r.level}
                                />
                              ) : null}
                              {r.validUntil ? (
                                <span>
                                  until{" "}
                                  <DateTime value={r.validUntil} style="date" />
                                </span>
                              ) : null}
                            </span>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="text-sm text-ink-muted">
                        No current verified claims. Start a verification to
                        raise your assurance.
                      </p>
                    )}
                  </div>
                );
              }}
            </QueryView>
          )}
        </Card>

        <Card
          title="Verify your identity"
          description="Choose what to verify against. Your organization sets up the available checks."
        >
          {verificationId ? (
            <div className="space-y-4">
              <KeyValues
                columns={1}
                items={[
                  [
                    "Status",
                    <StatusBadge
                      key="s"
                      state={status ?? "CREATED"}
                      kind="Verification"
                    />,
                  ],
                  [
                    "Verification",
                    <CopyId
                      key="v"
                      value={verificationId}
                      label="verification"
                    />,
                  ],
                  ...(check?.outcome
                    ? ([["Outcome", humanize(check.outcome)]] as [
                        string,
                        string,
                      ][])
                    : []),
                  ...(check?.expires_at
                    ? ([
                        [
                          "Expires",
                          <DateTime key="e" value={check.expires_at} />,
                        ],
                      ] as [string, ReactNode][])
                    : []),
                ]}
              />
              <p
                className="text-sm text-ink-muted"
                role="status"
                aria-live="polite"
              >
                {STATUS_COPY[status ?? "CREATED"] ?? humanize(status ?? "")}
              </p>
              {status === "PENDING_EXTERNAL" ? (
                <DevProvider
                  verificationId={verificationId}
                  adapterId={check?.adapter_id ?? start.data?.adapter_id}
                  onDone={() =>
                    void qc.invalidateQueries({ queryKey: sessionKey })
                  }
                />
              ) : null}
              {current.isError ? (
                <VerificationError
                  error={current.error}
                  onRetry={() => void current.refetch()}
                />
              ) : null}
              {cancel.error ? <VerificationError error={cancel.error} /> : null}
              {reverify.error ? <VerificationError error={reverify.error} /> : null}
              <div className="flex flex-wrap justify-end gap-2">
                {pending ? (
                  <Button
                    variant="danger-outline"
                    pending={cancel.isPending}
                    onPress={() => cancel.mutate(verificationId)}
                  >
                    Cancel check
                  </Button>
                ) : (
                  <>
                    {status === "VERIFIED" ? (
                      <Button
                        variant="secondary"
                        pending={reverify.isPending}
                        onPress={() => reverify.mutate(verificationId)}
                      >
                        Verify again
                      </Button>
                    ) : null}
                    <Button
                      variant="secondary"
                      onPress={() => {
                        setVerificationId(null);
                        start.reset();
                        cancel.reset();
                        reverify.reset();
                      }}
                    >
                      Start another check
                    </Button>
                  </>
                )}
              </div>
            </div>
          ) : profiles.isError ? (
            <VerificationError
              error={profiles.error}
              onRetry={() => void profiles.refetch()}
            />
          ) : (
            <QueryView query={profiles} loadingRows={3}>
              {() =>
                active.length === 0 ? (
                  <div className="space-y-3">
                    <EmptyState
                      icon={ScanFace}
                      title="No verification available"
                      compact
                    >
                      Your organization hasn&apos;t set up an identity
                      verification check yet.
                    </EmptyState>
                    <DevSetup />
                  </div>
                ) : (
                  <form onSubmit={submit} className="space-y-4">
                    <Select
                      label="Verification check"
                      isRequired
                      value={profileId}
                      onChange={(v) => {
                        setProfileId(v);
                        const p = active.find((x) => x.profile_id === v);
                        setJurisdiction(
                          p?.jurisdictions?.length === 1
                            ? (p.jurisdictions[0] ?? null)
                            : null,
                        );
                        // The platform's representative check is for proving who acts for the company.
                        if (p?.name === "representative-proofing")
                          setPurpose("representative_proofing");
                      }}
                      options={active.map((p) => ({
                        id: p.profile_id,
                        label: `${p.name} (v${p.version})`,
                        description: `Reaches ${p.level}`,
                      }))}
                    />
                    <Select
                      label="Country of your documents"
                      isRequired
                      isDisabled={!profile}
                      value={jurisdiction}
                      onChange={setJurisdiction}
                      placeholder={profile ? "Select…" : "Choose a check first"}
                      options={jurisdictions.map((j) => ({ id: j, label: j }))}
                    />
                    <TextField
                      label="Purpose"
                      isRequired
                      value={purpose}
                      onChange={setPurpose}
                      mono
                      description="Why you are verifying. Recorded with the result."
                    />
                    {!sub ? (
                      <Banner tone="warn" title="Session not loaded">
                        Your account details are still loading. Try again in a
                        moment.
                      </Banner>
                    ) : null}
                    {start.error ? (
                      <VerificationError error={start.error} />
                    ) : null}
                    <div className="flex justify-end">
                      <Button
                        type="submit"
                        pending={start.isPending}
                        isDisabled={
                          !sub || !profileId || !jurisdiction || !purpose.trim()
                        }
                        icon={<ScanFace aria-hidden className="size-4" />}
                      >
                        Start verification
                      </Button>
                    </div>
                  </form>
                )
              }
            </QueryView>
          )}
        </Card>
      </div>

      <div className="mt-5">
        <Card
          title="Verification history"
          description="Every check recorded for your account."
          padded={false}
        >
          {assertions.isError ? (
            <div className="p-5">
              <VerificationError
                error={assertions.error}
                onRetry={() => void assertions.refetch()}
              />
            </div>
          ) : (
            <QueryView query={assertions}>
              {(data) => (
                <DataTable
                  caption="Your verification assertions"
                  columns={columns}
                  rows={data.items}
                  rowKey={(a) => a.assertion_id}
                  empty={
                    <EmptyState icon={ScanFace} title="No verifications yet">
                      Completed checks appear here.
                    </EmptyState>
                  }
                />
              )}
            </QueryView>
          )}
        </Card>
      </div>
    </>
  );
}
