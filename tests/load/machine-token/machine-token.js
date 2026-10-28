// Machine-identity token gate (ZID-MACHINE-001 §25 and acceptance test MACH-26).
//
// Drives the workload token exchange (POST /v1/machine/workload-token) at 2x the Stage A token-issuance rate and
// fails if the p95 budget or the error budget is missed. The numbers below are bound to
// contracts/baseline/slo.yaml (machine_identity_gate) and contracts/baseline/capacity.yaml by
// backend/app/tests/unit/test_load_gate.py: change them there first.
//
//   k6 run -e ZID_CELL_URL=https://uk-1.api.perf.id.zoiko.com -e ZID_SVID_CERT=client.pem -e ZID_SVID_KEY=client.key \
//          -e ZID_ENVIRONMENT_ID=prod -e ZID_ARTIFACT_DIGEST=sha256:... tests/load/machine-token/machine-token.js
//   k6 run -e SMOKE=1 ...   # 1 minute at 50/s to check the wiring before a real run
//
// The workload is identified by its SVID, presented as the TLS client certificate (the mesh-verified peer identity),
// never by a header the caller sets. Use a workload registered for the PERF tenant. Never point it at production.

import http from "k6/http";
import { check } from "k6";

// ---- bound to contracts/baseline/slo.yaml (machine_identity_gate) and capacity.yaml (token_issuance_per_s) ----
export const GATE = {
  ratePerSecond: 20000,
  soakMinutes: 60,
  maxErrorRate: 0.0001,
  p95MsTokenExchange: 150,
};

const SMOKE = __ENV.SMOKE === "1";
const BASE = __ENV.ZID_CELL_URL;
const ENVIRONMENT = __ENV.ZID_ENVIRONMENT_ID;
const DIGEST = __ENV.ZID_ARTIFACT_DIGEST;
if (!BASE || !ENVIRONMENT || !DIGEST || !__ENV.ZID_SVID_CERT || !__ENV.ZID_SVID_KEY) {
  throw new Error("set ZID_CELL_URL (a cell endpoint, never the global plane), ZID_ENVIRONMENT_ID, ZID_ARTIFACT_DIGEST, ZID_SVID_CERT and ZID_SVID_KEY");
}

export const options = {
  discardResponseBodies: true,
  tlsAuth: [{ domains: [new URL(BASE).hostname], cert: open(__ENV.ZID_SVID_CERT), key: open(__ENV.ZID_SVID_KEY) }],
  scenarios: {
    exchanges: {
      executor: "constant-arrival-rate",
      rate: SMOKE ? 50 : GATE.ratePerSecond,
      timeUnit: "1s",
      duration: SMOKE ? "1m" : `${GATE.soakMinutes}m`,
      preAllocatedVUs: SMOKE ? 20 : 2000,
      maxVUs: SMOKE ? 50 : 8000,
    },
  },
  thresholds: {
    "http_req_duration{path:token_exchange}": [`p(95)<${GATE.p95MsTokenExchange}`],
    http_req_failed: [`rate<${GATE.maxErrorRate}`],
  },
  // NFR-PERF-006: report percentiles, never averages alone.
  summaryTrendStats: ["p(50)", "p(95)", "p(99)", "max", "count"],
};

const BODY = JSON.stringify({ environment_id: ENVIRONMENT, artifact_digest: DIGEST, attestation: 2 });

export default function () {
  const res = http.post(`${BASE}/v1/machine/workload-token`, BODY, {
    headers: { "Content-Type": "application/json" },
    tags: { path: "token_exchange" },
  });
  // A refusal is a valid answer for a bad workload; only transport and server errors count against the gate.
  check(res, { "token issued": (x) => x.status === 200 });
}
