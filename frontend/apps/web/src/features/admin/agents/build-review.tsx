"use client";

import type { BuildItem, Person } from "@zoiko/api-client";
import { Button, Card, CopyId, DateTime } from "@zoiko/ui";
import { useState } from "react";

import { PersonLabel } from "../team/person-label";
import { PipelineCredentialsDialog } from "./pipeline-credentials";

/** A link is only a link when it is https: the proof is whatever the registering side wrote, so anything else is shown as plain text. */
const isHttps = (s: string) => /^https:\/\/[^\s]+$/i.test(s);

/**
 * The reason the Builds page exists in production: software the pipeline (or a person) has registered and a SECOND person must check and approve.
 * Each entry shows what a reviewer needs to decide: exactly what it is (the digest), where it came from, the proof of how it was built if one was
 * recorded (and a plain warning if not), and who registered it and when. Nothing here is typed.
 */
export function ApprovalQueue({
  builds,
  person,
  approve,
  approvingId,
  me,
}: {
  builds: BuildItem[];
  person: (id: string) => Person | undefined;
  approve: (b: BuildItem) => void;
  approvingId?: string;
  /** The signed-in person: they cannot approve what they registered themselves, so the button says so instead of failing. */
  me?: string;
}) {
  const waiting = builds.filter((b) => b.review_status === "DRAFT");
  if (waiting.length === 0) return null;
  return (
    <Card
      padded={false}
      title={`Waiting for approval (${waiting.length})`}
      description="Check what each one is, then approve it. A second person must approve: whoever registered a build cannot approve it. Approving lets releases run it; nothing runs before."
    >
      <ul className="divide-y divide-border">
        {waiting.map((b) => (
          <li key={b.build_id} className="flex flex-wrap items-start justify-between gap-3 p-4">
            <div className="min-w-0 space-y-1">
              <p className="text-sm font-medium text-ink">{b.name}</p>
              <p className="text-xs text-ink-muted">
                Digest <CopyId value={b.artifact_digest} label="build digest" />
              </p>
              {b.source_ref ? (
                <p className="break-all font-mono text-xs text-ink-muted">From {b.source_ref}</p>
              ) : (
                <p className="text-xs text-warn-fg">No source was recorded: you cannot tell where this came from.</p>
              )}
              {b.provenance_ref ? (
                isHttps(b.provenance_ref) ? (
                  <p className="text-xs">
                    <a className="text-brand-700 underline" href={b.provenance_ref} target="_blank" rel="noreferrer noopener">
                      How it was built (opens the build record)
                    </a>
                  </p>
                ) : (
                  <p className="break-all font-mono text-xs text-ink-muted">Proof: {b.provenance_ref}</p>
                )
              ) : (
                <p className="text-xs text-warn-fg">No proof of how it was built was recorded.</p>
              )}
              <p className="text-xs text-ink-muted">
                Registered by <PersonLabel person={person(b.registered_by)} fallbackId={b.registered_by} /> <DateTime value={b.registered_at} />
              </p>
            </div>
            {me !== undefined && b.registered_by === me ? (
              <p className="max-w-48 text-xs text-ink-muted">You registered this build, so someone else has to approve it.</p>
            ) : (
              <Button size="sm" pending={approvingId === b.build_id} onPress={() => approve(b)}>
                Approve
              </Button>
            )}
          </li>
        ))}
      </ul>
    </Card>
  );
}

const command = (apiBase: string) => `python3 tools/scripts/register_build.py \\
  --api-base ${apiBase} \\
  --client-id <the application's client id> \\
  --key pipeline.jwk \\
  --name "<what people will pick from a list>" \\
  --digest sha256:<from the build, never typed by hand> \\
  --source ghcr.io/<org>/<agent>:<version> \\
  --allow-existing`;

/**
 * The recommended way to get software into the list: the pipeline that builds it registers it, so nobody types a name or a digest. Shown open
 * until the first build exists, and always available.
 */
export function PipelineGuide({ hasBuilds, apiBase }: { hasBuilds: boolean; apiBase?: string }) {
  const [copied, setCopied] = useState(false);
  const [creating, setCreating] = useState(false);
  const COMMAND = command(apiBase ?? "https://api.<your domain>");
  return (
    <Card padded={false}>
      <details className="p-4" open={!hasBuilds}>
        <summary className="cursor-pointer text-sm font-medium text-ink">Let your build pipeline register builds (recommended)</summary>
        <div className="mt-3 space-y-3 text-sm text-ink-muted">
          <p>
            The pipeline that builds the software already knows its image and digest, so it registers each build itself. Nobody types anything: builds appear above under{" "}
            <strong>Waiting for approval</strong>, and a person checks and approves them.
          </p>
          <ol className="list-decimal space-y-1 ps-5">
            <li>
              Make the pipeline&apos;s credentials in one press (it signs in with a key, no passkey) and keep the private key file as a secret in the pipeline:{" "}
              <Button size="xs" variant="secondary" onPress={() => setCreating(true)}>
                Create pipeline credentials
              </Button>
            </li>
            <li>
              Under <strong>Authority &amp; approvals</strong>, issue that identity (the credentials screen shows it) one grant only: <strong>Register a software build</strong> (Agents and tools). It cannot approve,
              revoke or run anything.
            </li>
            <li>
              Add a step to the pipeline after it pushes the image. The repository&apos;s <code className="font-mono text-xs">tools/ci/register-agent-build.yml</code> does the
              whole thing on every push; or run the script yourself:
            </li>
          </ol>
          <pre className="overflow-x-auto rounded-card border border-border bg-canvas p-3 font-mono text-xs text-ink">{COMMAND}</pre>
          <Button
            size="xs"
            variant="secondary"
            onPress={() => {
              void navigator.clipboard?.writeText(COMMAND).then(() => setCopied(true));
            }}
          >
            {copied ? "Copied" : "Copy the command"}
          </Button>
        </div>
      </details>
      <PipelineCredentialsDialog isOpen={creating} onClose={() => setCreating(false)} />
    </Card>
  );
}
