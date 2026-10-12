// DEC-B3 decision-service performance gate (ZID-REQ-001 NFR-PERF-001/006, NFR-SCL-002; AT-16).
//
// Drives the AuthZEN 1.0 evaluation endpoint at 2x the Stage A per-cell peak for a 1-hour soak and
// fails if a p95 budget or the error budget is missed. The numbers below are bound to
// contracts/baseline/slo.yaml by backend/app/tests/unit/test_load_gate.py: change them there first.
//
//   k6 run -e ZID_CELL_URL=https://uk-1.api.dev.id.zoiko.com -e ZID_TOKEN=... tests/load/decision-gate/decision-gate.js
//   k6 run -e SMOKE=1 ...   # 1 minute at 50/s to check the wiring before a real run
//
// Runs once the real decision path exists (Phases C-D). Never point it at production.

import http from "k6/http";
import { check } from "k6";

// ---- bound to contracts/baseline/slo.yaml (decision_service_gate, latency_budgets_ms) ----
export const GATE = {
  ratePerSecond: 10000,
  soakMinutes: 60,
  maxErrorRate: 0.0001,
  p95MsR0R1: 40,
  p95MsR2R3: 60,
};

const SMOKE = __ENV.SMOKE === "1";
const BASE = __ENV.ZID_CELL_URL;
const TOKEN = __ENV.ZID_TOKEN;
if (!BASE || !TOKEN) {
  throw new Error("set ZID_CELL_URL (a cell endpoint, never the global plane) and ZID_TOKEN");
}

export const options = {
  discardResponseBodies: true,
  scenarios: {
    decisions: {
      executor: "constant-arrival-rate",
      rate: SMOKE ? 50 : GATE.ratePerSecond,
      timeUnit: "1s",
      duration: SMOKE ? "1m" : `${GATE.soakMinutes}m`,
      preAllocatedVUs: SMOKE ? 20 : 2000,
      maxVUs: SMOKE ? 50 : 8000,
    },
  },
  thresholds: {
    "http_req_duration{path:R0_R1}": [`p(95)<${GATE.p95MsR0R1}`],
    "http_req_duration{path:R2_R3}": [`p(95)<${GATE.p95MsR2R3}`],
    http_req_failed: [`rate<${GATE.maxErrorRate}`],
  },
  // NFR-PERF-006: report percentiles, never averages alone.
  summaryTrendStats: ["p(50)", "p(95)", "p(99)", "max", "count"],
};

// Mixed traffic: actions from contracts/action-catalog/actions.yaml at each risk path.
const REQUESTS = [
  { path: "R0_R1", action: "application.access", resource: { type: "application", id: "app_load_1" } },
  { path: "R0_R1", action: "resource.write", resource: { type: "document", id: "doc_load_1" } },
  { path: "R2_R3", action: "invoice.approve", resource: { type: "invoice", id: "inv_load_1" } },
  { path: "R2_R3", action: "payment.release", resource: { type: "payment", id: "pay_load_1" } },
];

const HEADERS = {
  "Content-Type": "application/json",
  Authorization: `Bearer ${TOKEN}`,
};

export default function () {
  const r = REQUESTS[Math.floor(Math.random() * REQUESTS.length)];
  const subject = `prn_load_${Math.floor(Math.random() * 100000)}`;
  const body = JSON.stringify({
    subject: { type: "principal", id: subject },
    action: { name: r.action },
    resource: r.resource,
    context: {},
  });
  const res = http.post(`${BASE}/access/v1/evaluation`, body, {
    headers: { ...HEADERS, "Idempotency-Key": `${subject}-${__ITER}` },
    tags: { path: r.path, action: r.action },
  });
  // A DENY is a valid decision; only transport and server errors count against the gate.
  check(res, { "decision returned": (x) => x.status === 200 });
}
