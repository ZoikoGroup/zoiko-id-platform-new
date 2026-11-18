// Screening performance gate (ZID-DTV-WF-06 ENG-19; PROPOSED numbers, see contracts/baseline/slo.yaml screening_gate).
//
// Drives the tenant screening API as a tenant administrator would: eligibility reads, and case opens with a fresh idempotency key. Fails if a p95 budget or the error budget is missed, and fails if any
// response carries something the customer must never see (a list name, a candidate, a score). The numbers below are bound to slo.yaml by backend/app/tests/unit/test_load_gate.py: change them there first.
//
//   k6 run -e ZID_CELL_URL=https://uk-1.api.perf.id.zoiko.com -e ZID_TOKEN=<PERF tenant admin bearer> -e MARKET=GB -e PURPOSE=KYB -e REGION_OK=1 tests/load/screening-gate/screening-gate.js
//   k6 run -e SMOKE=1 ...   # 1 minute at a trickle, to check the wiring before a real run
//
// Use a PERF tenant whose market pack enables a class with only official sources (no provider is called, so nothing is billed). Never point it at production.

import http from "k6/http";
import { check } from "k6";
import { Counter } from "k6/metrics";

// ---- bound to contracts/baseline/slo.yaml (screening_gate) ----
export const GATE = {
  eligibilityPerSecond: 200,
  caseOpensPerSecond: 20,
  soakMinutes: 30,
  maxErrorRate: 0.001,
  p95MsEligibility: 250,
  p95MsCaseOpen: 1500,
};

const SMOKE = __ENV.SMOKE === "1";
const BASE = __ENV.ZID_CELL_URL;
const TOKEN = __ENV.ZID_TOKEN;
const MARKET = __ENV.MARKET || "GB";
const PURPOSE = __ENV.PURPOSE || "KYB";
if (!BASE || !TOKEN) {
  throw new Error("set ZID_CELL_URL (a cell endpoint, never the global plane) and ZID_TOKEN (a PERF tenant administrator bearer)");
}

const leaks = new Counter("customer_response_leaks");

export const options = {
  discardResponseBodies: false,
  scenarios: {
    eligibility: {
      executor: "constant-arrival-rate",
      exec: "eligibility",
      rate: SMOKE ? 5 : GATE.eligibilityPerSecond,
      timeUnit: "1s",
      duration: SMOKE ? "1m" : `${GATE.soakMinutes}m`,
      preAllocatedVUs: SMOKE ? 5 : 100,
      maxVUs: SMOKE ? 10 : 400,
    },
    case_open: {
      executor: "constant-arrival-rate",
      exec: "caseOpen",
      rate: SMOKE ? 1 : GATE.caseOpensPerSecond,
      timeUnit: "1s",
      duration: SMOKE ? "1m" : `${GATE.soakMinutes}m`,
      preAllocatedVUs: SMOKE ? 5 : 100,
      maxVUs: SMOKE ? 10 : 400,
    },
  },
  thresholds: {
    "http_req_duration{path:eligibility}": [`p(95)<${GATE.p95MsEligibility}`],
    "http_req_duration{path:case_open}": [`p(95)<${GATE.p95MsCaseOpen}`],
    http_req_failed: [`rate<${GATE.maxErrorRate}`],
    customer_response_leaks: ["count==0"],
  },
  // NFR-PERF-006: report percentiles, never averages alone.
  summaryTrendStats: ["p(50)", "p(95)", "p(99)", "max", "count"],
};

const HEADERS = { Authorization: `Bearer ${TOKEN}`, "Content-Type": "application/json" };
// What a customer must never be shown, in any response: the list a name came from, a candidate, a match score.
const FORBIDDEN = /OFAC|SDN|UN_SC|EU_CONSOLIDATED|UK_SANCTIONS|"candidates"|"matched_name"|"score"/;

function guard(res) {
  if (FORBIDDEN.test(res.body || "")) leaks.add(1);
}

export function eligibility() {
  const q = `market=${MARKET}&purpose=${PURPOSE}&subject_type=BUSINESS&subject_ref=perf-${__VU}`;
  const res = http.get(`${BASE}/v1/screening/eligibility?${q}`, { headers: HEADERS, tags: { path: "eligibility" } });
  guard(res);
  // A refusal (not eligible) is a valid answer; only transport and server errors count against the gate.
  check(res, { "answered": (x) => x.status < 500 });
}

export function caseOpen() {
  const key = `perf-${__VU}-${__ITER}-${Date.now()}`;
  const body = JSON.stringify({
    market: MARKET,
    purpose: PURPOSE,
    subject_type: "BUSINESS",
    subject_ref: `perf-${__VU}`,
    name: `Perf Subject ${__VU}-${__ITER}`,
  });
  const res = http.post(`${BASE}/v1/screening/cases`, body, { headers: { ...HEADERS, "Idempotency-Key": key }, tags: { path: "case_open" } });
  guard(res);
  check(res, { "answered": (x) => x.status < 500 });
}
