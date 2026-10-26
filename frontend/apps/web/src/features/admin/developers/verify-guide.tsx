"use client";

import { Card, CopyId, KeyValues } from "@zoiko/ui";

import { QueryView, useCellQuery } from "@/lib/cell";

import { SIGNING_KEYS_KEY } from "./shared";

export function VerifyGuideSection() {
  const keys = useCellQuery(SIGNING_KEYS_KEY, (api) => api.webhooks.signingKeys());
  return (
    <div className="space-y-4">
      <Card title="How to verify a delivery" description="Every request carries an HTTP Message Signature (RFC 9421) and a body digest (RFC 9530).">
        <ol className="list-decimal space-y-2 pl-5 text-sm text-ink-muted">
          <li>
            Recompute <code className="font-mono">Content-Digest</code> (<code className="font-mono">sha-256</code>) over the raw body and compare it with the header.
          </li>
          <li>
            Rebuild the signature base from the components listed in <code className="font-mono">Signature-Input</code>: method, authority, target URI, content type, content digest and{" "}
            <code className="font-mono">X-Zoiko-Event-ID</code>.
          </li>
          <li>
            Verify <code className="font-mono">Signature</code> with the public key whose <code className="font-mono">kid</code> equals the signature&apos;s <code className="font-mono">keyid</code>.
          </li>
          <li>
            Treat <code className="font-mono">X-Zoiko-Event-ID</code> as an idempotency key: the same event can arrive more than once (retries and replays).
          </li>
          <li>Answer with a 2xx status. Redirects are never followed; 410 Gone disables the endpoint.</li>
        </ol>
      </Card>
      <Card title="Signing keys" description="Public keys receivers verify with. This list needs no authentication.">
        <QueryView query={keys}>
          {(data) => (
            <div className="space-y-3">
              {data.keys.map((k) => (
                <KeyValues
                  key={String(k.kid)}
                  items={[
                    ["Key ID", <CopyId key="kid" value={String(k.kid)} label="key ID" />],
                    ["Algorithm", String(k.alg ?? "")],
                    [
                      "Public key (JWK)",
                      <code key="jwk" className="block font-mono text-xs break-all">
                        {JSON.stringify(k)}
                      </code>,
                    ],
                  ]}
                />
              ))}
            </div>
          )}
        </QueryView>
      </Card>
    </div>
  );
}
