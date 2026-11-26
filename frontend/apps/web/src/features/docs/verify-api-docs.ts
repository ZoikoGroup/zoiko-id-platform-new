/** The Verify API developer documentation (CR-157; ZID-EXT-IDV-001 epic E13, QA Q48): written so a developer who has never seen the product can finish a first sandbox verification from this page alone.
 *
 * It is data, not prose in a page, so a test can hold it to the facts: every operation named here exists in the published contract, every error code is one the cell can answer, and nothing else is listed.
 * Examples use a placeholder address (YOUR-API-ADDRESS) and placeholder credentials; nothing here is a real secret. */

export type Lang = "curl" | "node" | "python";
export type Example = { lang: Lang; code: string };
export type Block =
  | { kind: "p"; text: string }
  | { kind: "list"; items: string[] }
  | { kind: "note"; tone: "info" | "warn"; title: string; text: string }
  | { kind: "code"; caption: string; examples: Example[] }
  | { kind: "json"; caption: string; code: string }
  | { kind: "table"; head: string[]; rows: string[][] };
export type Section = { id: string; title: string; blocks: Block[] };

export const API = "https://YOUR-API-ADDRESS";

const lines = (...l: string[]): string => l.join("\n");

/** Every operation a partner's server calls, with the scope it needs. The test compares this to the published contract in both directions. */
export const OPERATIONS: { method: "GET" | "POST"; path: string; scope: string; summary: string }[] = [
  { method: "POST", path: "/v1/verify-api/oauth2/token", scope: "(none)", summary: "Exchange your client ID and secret, or a signed assertion from your private key, for a 15-minute bearer token." },
  { method: "GET", path: "/v1/verify-api/whoami", scope: "(any token)", summary: "Show what a token is: application, environment, scopes, expiry." },
  { method: "GET", path: "/v1/verify-api/workflows", scope: "workflows:read", summary: "List the workflows and which ones this application may start." },
  { method: "GET", path: "/v1/verify-api/products", scope: "products:read", summary: "List the checks and where they are available." },
  { method: "POST", path: "/v1/verify-api/verification-sessions", scope: "verifications:create", summary: "Create a verification. Needs an Idempotency-Key. Returns the launch link once." },
  { method: "GET", path: "/v1/verify-api/verification-sessions", scope: "verifications:list", summary: "Page through your verifications, newest first." },
  { method: "GET", path: "/v1/verify-api/verification-sessions/{session_id}", scope: "verifications:read", summary: "Where one verification is." },
  { method: "GET", path: "/v1/verify-api/verification-sessions/{session_id}/decision", scope: "decisions:read", summary: "The minimized decision." },
  { method: "POST", path: "/v1/verify-api/verification-sessions/{session_id}:cancel", scope: "verifications:create", summary: "End a verification that is not finished." },
  { method: "POST", path: "/v1/verify-api/verification-sessions/{session_id}/resume-token", scope: "verifications:resume", summary: "A fresh launch link for a customer who left. The old link stops." },
  { method: "POST", path: "/v1/verify-api/verification-sessions/{session_id}:recheck", scope: "verifications:recheck", summary: "A new verification that follows a finished one. Needs an Idempotency-Key." },
  { method: "POST", path: "/v1/verify-api/sandbox/fixtures/{outcome}/sessions", scope: "verifications:create", summary: "Sandbox: create a verification already at a simulated outcome." },
  { method: "POST", path: "/v1/verify-api/sandbox/sessions/{session_id}:simulate", scope: "verifications:create", summary: "Sandbox: move an open verification to a simulated outcome." },
];

/** Every error the cell can answer on these routes. The test compares this to the cell's own status map. */
export const ERRORS: { code: string; status: number; meaning: string; action: string }[] = [
  { code: "AUTH_INVALID", status: 401, meaning: "The credentials or token are not valid. It is the only answer for a wrong client ID, a wrong secret, an expired or revoked token or a suspended application.", action: "Request a new token. If it keeps failing, check the credential in the console." },
  { code: "TENANT_SCOPE_DENIED", status: 403, meaning: "The token does not carry the scope this call needs.", action: "Give the application the scope in the console, then request a new token." },
  { code: "IP_NOT_ALLOWED", status: 403, meaning: "The application has a list of allowed networks and this call came from outside it. It applies to the token request and to every call made with a token.", action: "Call from a listed network, or change the list in the application's settings." },
  { code: "ENVIRONMENT_MISMATCH", status: 403, meaning: "The session belongs to the other environment (sandbox or production).", action: "Use a token from the environment that made the session." },
  { code: "PRODUCT_NOT_ENTITLED", status: 403, meaning: "This application cannot start this live verification: production is not open for your company, or your approval does not include the check or workflow you asked for (live verification offers the document and selfie workflow with the face match check).", action: "Use the sandbox, or ask for the missing approval." },
  { code: "MARKET_NOT_ENABLED", status: 403, meaning: "Live verification is not available for that country yet: your production approval does not name it, or Zoiko iD has not enabled it. Nothing was created and no customer was asked for anything.", action: "Use a country named in your approval, or ask for it to be added." },
  { code: "WORKFLOW_NOT_ENTITLED", status: 403, meaning: "This application is not allowed to start that workflow.", action: "Choose an allowed workflow (see GET /v1/verify-api/workflows) or change the application's settings." },
  { code: "NOT_FOUND", status: 404, meaning: "No such session for this application. Another application's session also answers this.", action: "Check the session ID and the application." },
  { code: "DECISION_NOT_AVAILABLE", status: 404, meaning: "There is no decision yet: the customer has not finished, or the session ended without one.", action: "Wait for the webhook, or read the session status first." },
  { code: "LAUNCH_INVALID", status: 404, meaning: "A customer's link is wrong, used, expired or replaced. One answer for every case, on purpose.", action: "Send a new link with resume-token." },
  { code: "INVALID_IDEMPOTENCY_REUSE", status: 409, meaning: "That Idempotency-Key was already used for a different request.", action: "Use a new key for a new request." },
  { code: "SESSION_STATE_INVALID", status: 409, meaning: "The session is not in a state that allows this (for example a recheck of one still in progress).", action: "Read the session status and act on it." },
  { code: "VALIDATION_FAILED", status: 422, meaning: "A field is missing or not valid. The message names it.", action: "Fix the request. Nothing was created." },
  { code: "WORKFLOW_VERSION_UNAVAILABLE", status: 422, meaning: "The workflow or version you named is not available.", action: "List workflows and pick one." },
  { code: "RETURN_ORIGIN_NOT_REGISTERED", status: 422, meaning: "return_origin is not one of the addresses registered on the application.", action: "Register the address in the application's settings." },
  { code: "RATE_LIMITED", status: 429, meaning: "Too many attempts. Wrong credentials are limited per client ID; wrong customer links per address.", action: "Wait the number of seconds in the message, then try again." },
];

export const STATES: { state: string; meaning: string }[] = [
  { state: "ready", meaning: "Created. The customer has the link and has not opened it." },
  { state: "opened", meaning: "The customer opened the link." },
  { state: "capture_started / submitted / processing", meaning: "The customer is capturing, has submitted, and the check is running (live service)." },
  { state: "action_required", meaning: "The customer must retry something (live service)." },
  { state: "manual_review", meaning: "With a reviewer. A decision follows." },
  { state: "completed", meaning: "Finished with a result. Read the decision." },
  { state: "inconclusive", meaning: "Ended without an answer. Recheck if you still need one." },
  { state: "expired", meaning: "Not finished in time (24 hours). Recheck or create a new one." },
  { state: "canceled", meaning: "You ended it." },
];

export const SECTIONS: Section[] = [
  {
    id: "overview",
    title: "Overview",
    blocks: [
      { kind: "p", text: "The Verify API lets your own server check who your customers are. Your server asks for a verification, you send your customer to a link, and your server reads a minimized result: an outcome, which checks passed and why. You never receive a photograph, a document image or a score." },
      { kind: "list", items: [
        "Everything runs in the sandbox until your company is approved to go live. In the sandbox no real person or document is checked and the result is chosen by you.",
        "All calls use HTTPS and JSON. Authentication is a short-lived bearer token from your client credentials.",
        "Your server calls the API. Never put your secret in a web page or a mobile app.",
        "The OpenAPI description of exactly these operations is at /v1/verify-api/openapi.json.",
      ] },
    ],
  },
  {
    id: "quickstart",
    title: "Quickstart: your first sandbox verification",
    blocks: [
      { kind: "p", text: "Five steps, about ten minutes. You need a Zoiko iD company account with a sandbox application." },
      { kind: "list", items: [
        "1. In the console, open Developers, create an application and a credential. Copy the client ID and the secret now: the secret is shown once.",
        "2. Exchange them for a token (below).",
        "3. Create a verification with an Idempotency-Key and open the launch link it returns.",
        "4. On the test page, choose the result to send. In your own tests you can skip the page with the sandbox calls.",
        "5. Read the decision.",
      ] },
      { kind: "code", caption: "Step 2: get a token. Tokens last 15 minutes; request a new one when it ends.", examples: [
        { lang: "curl", code: lines(
          "curl -s -X POST " + API + "/v1/verify-api/oauth2/token \\",
          "  -H 'Content-Type: application/json' \\",
          "  -d '{\"client_id\":\"zpc_YOUR_CLIENT_ID\",\"client_secret\":\"zps_YOUR_SECRET\"}'",
        ) },
        { lang: "node", code: lines(
          "const res = await fetch('" + API + "/v1/verify-api/oauth2/token', {",
          "  method: 'POST',",
          "  headers: { 'Content-Type': 'application/json' },",
          "  body: JSON.stringify({ client_id: process.env.ZOIKO_CLIENT_ID, client_secret: process.env.ZOIKO_CLIENT_SECRET }),",
          "});",
          "if (!res.ok) throw new Error('token request failed: ' + res.status);",
          "const { access_token } = await res.json();",
        ) },
        { lang: "python", code: lines(
          "import os, requests",
          "r = requests.post('" + API + "/v1/verify-api/oauth2/token', json={",
          "    'client_id': os.environ['ZOIKO_CLIENT_ID'],",
          "    'client_secret': os.environ['ZOIKO_CLIENT_SECRET'],",
          "})",
          "r.raise_for_status()",
          "token = r.json()['access_token']",
        ) },
      ] },
      { kind: "json", caption: "The token answer", code: lines(
        "{",
        "  \"access_token\": \"zpt_...\",",
        "  \"token_type\": \"Bearer\",",
        "  \"expires_in\": 900,",
        "  \"scope\": \"verifications:create verifications:read ...\",",
        "  \"environment\": \"sandbox\"",
        "}",
      ) },
      { kind: "code", caption: "Step 3: create a verification. Use a new Idempotency-Key for each customer attempt; send the same key again after a timeout and you get the same verification back.", examples: [
        { lang: "curl", code: lines(
          "curl -s -X POST " + API + "/v1/verify-api/verification-sessions \\",
          "  -H \"Authorization: Bearer $TOKEN\" \\",
          "  -H 'Idempotency-Key: order-1001-verify' \\",
          "  -H 'Content-Type: application/json' \\",
          "  -d '{\"customer_reference\":\"customer-1001\",\"country\":\"US\",\"purpose_code\":\"customer_onboarding\"}'",
        ) },
        { lang: "node", code: lines(
          "const created = await fetch('" + API + "/v1/verify-api/verification-sessions', {",
          "  method: 'POST',",
          "  headers: {",
          "    Authorization: 'Bearer ' + access_token,",
          "    'Idempotency-Key': 'order-1001-verify',",
          "    'Content-Type': 'application/json',",
          "  },",
          "  body: JSON.stringify({ customer_reference: 'customer-1001', country: 'US', purpose_code: 'customer_onboarding' }),",
          "});",
          "const session = await created.json(); // session.launch_url is shown once: send it to your customer",
        ) },
        { lang: "python", code: lines(
          "created = requests.post('" + API + "/v1/verify-api/verification-sessions',",
          "    headers={'Authorization': 'Bearer ' + token, 'Idempotency-Key': 'order-1001-verify'},",
          "    json={'customer_reference': 'customer-1001', 'country': 'US', 'purpose_code': 'customer_onboarding'})",
          "session = created.json()  # session['launch_url'] is shown once: send it to your customer",
        ) },
      ] },
      { kind: "json", caption: "The answer (the link is only ever shown here; it expires in 30 minutes)", code: lines(
        "{",
        "  \"session_id\": \"vfs_...\",",
        "  \"status\": \"ready\",",
        "  \"customer_reference\": \"customer-1001\",",
        "  \"mode\": \"sandbox\",",
        "  \"launch_url\": \"https://.../verify/session/zvl_...\",",
        "  \"launch_expires_at\": \"2026-11-24T10:30:00+00:00\"",
        "}",
      ) },
      { kind: "code", caption: "Steps 4 and 5 without a browser: move the sandbox verification to a result, then read the decision.", examples: [
        { lang: "curl", code: lines(
          "curl -s -X POST " + API + "/v1/verify-api/sandbox/sessions/$SESSION_ID:simulate \\",
          "  -H \"Authorization: Bearer $TOKEN\" -H 'Content-Type: application/json' \\",
          "  -d '{\"outcome\":\"approved\"}'",
          "",
          "curl -s " + API + "/v1/verify-api/verification-sessions/$SESSION_ID/decision \\",
          "  -H \"Authorization: Bearer $TOKEN\"",
        ) },
        { lang: "node", code: lines(
          "const base = '" + API + "/v1/verify-api';",
          "await fetch(base + '/sandbox/sessions/' + session.session_id + ':simulate', {",
          "  method: 'POST',",
          "  headers: { Authorization: 'Bearer ' + access_token, 'Content-Type': 'application/json' },",
          "  body: JSON.stringify({ outcome: 'approved' }),",
          "});",
          "const decision = await (await fetch(base + '/verification-sessions/' + session.session_id + '/decision', {",
          "  headers: { Authorization: 'Bearer ' + access_token },",
          "})).json();",
          "// decision.outcome === 'verified'",
        ) },
        { lang: "python", code: lines(
          "h = {'Authorization': 'Bearer ' + token}",
          "base = '" + API + "/v1/verify-api'",
          "requests.post(f\"{base}/sandbox/sessions/{session['session_id']}:simulate\", headers=h, json={'outcome': 'approved'})",
          "decision = requests.get(f\"{base}/verification-sessions/{session['session_id']}/decision\", headers=h).json()",
          "assert decision['outcome'] == 'verified'",
        ) },
      ] },
    ],
  },
  {
    id: "auth",
    title: "Authentication and scopes",
    blocks: [
      { kind: "p", text: "Send the token as Authorization: Bearer <token>. A token carries the scopes your application holds (or a subset you ask for with the scope field of the token request), belongs to one environment, and ends after 15 minutes, or at once if its credential is revoked or its application is suspended." },
      { kind: "list", items: [
        "Rotate a credential without downtime: the new secret works at once and the old one keeps working for the overlap you choose (1 hour to 7 days).",
        "Prefer a private key to a secret: a key credential holds only your public key, so we keep nothing that could leak. Your server signs a short assertion (ES256 or EdDSA) with iss and sub set to the client ID, aud set to the token endpoint address, exp within 5 minutes and a new jti each time. Send it as client_assertion with client_assertion_type urn:ietf:params:oauth:client-assertion-type:jwt-bearer. Each assertion works once.",
        "Limit where calls may come from: list the IP addresses or networks your server uses in the application's settings. Calls from anywhere else are refused (IP_NOT_ALLOWED), even with a valid token.",
        "Wrong credentials always get the same answer (AUTH_INVALID), so nothing says which part was wrong. Repeated failures for one client ID are limited.",
        "Responses that carry a token or a link are sent with Cache-Control: no-store.",
      ] },
    ],
  },
  {
    id: "sdks",
    title: "Client libraries",
    blocks: [
      { kind: "p", text: "Two small client libraries wrap the calls in this guide: sign in with a secret or a private key, start a verification, read the minimized decision, and check a signed webhook. Both are for your server only, never for a web page or a mobile app." },
      { kind: "table", head: ["Language", "Where", "What it covers"], rows: [
        ["Python 3.11+", "sdks/verify-api-python (package zoiko-verify)", "Token (secret or private key, renewed for you), sessions, decisions, rechecks, sandbox helpers, webhook verification."],
        ["TypeScript / Node 20+", "sdks/typescript, module verify", "The same calls and webhook verification, with no runtime dependencies."],
      ] },
      { kind: "note", tone: "info", title: "Not yet on a package registry", text: "These libraries are in the Zoiko iD repository and are not published to PyPI or npm yet. Until they are, vendor the folder or ask your Zoiko iD contact for a build. Everything they do you can also do with the plain calls in this guide." },
      { kind: "list", items: [
        "Both check webhook deliveries against the published signing keys, refuse anything unsigned, stale, altered or signed by a retired key, and return the event id.",
        "Both require your own Idempotency-Key on every call that creates something, and never make one up for you.",
        "A private-key client signs a new single-use assertion for each token request; a secret client sends its secret only to the token endpoint.",
      ] },
    ],
  },
  {
    id: "operations",
    title: "Operations",
    blocks: [
      { kind: "table", head: ["Method", "Path", "Scope", "What it does"], rows: OPERATIONS.map((o) => [o.method, o.path, o.scope, o.summary]) },
      { kind: "note", tone: "info", title: "Fields", text: "Create takes customer_reference (your own ID for the customer, up to 128 characters; it is not an identity), country (two letters), purpose_code, and optionally workflow_id, workflow_version, region, locale, return_origin (one of your registered addresses) and metadata (up to 10 short text pairs). Unknown fields are refused." },
    ],
  },
  {
    id: "sessions",
    title: "Verifications and their states",
    blocks: [
      { kind: "table", head: ["State", "Meaning"], rows: STATES.map((s) => [s.state, s.meaning]) },
      { kind: "list", items: [
        "A verification you do not finish expires after 24 hours; its link lasts 30 minutes. resume-token gives a customer a fresh link and stops the old one.",
        "A recheck is a new verification that follows a finished one. It reuses the original's customer, workflow and market, and leaves the original untouched.",
        "List results are newest first. Pass the next_cursor from one page to get the next; limit is 1 to 100.",
      ] },
    ],
  },
  {
    id: "decision",
    title: "The decision",
    blocks: [
      { kind: "p", text: "The decision is deliberately small. outcome is one of verified, not_verified, inconclusive or review_required. checks says which checks passed or failed. A later decision for the same verification (for example after a review) has a higher decision_version and names the one it replaces in supersedes_decision_id; the earlier one is kept." },
      { kind: "json", caption: "A decision", code: lines(
        "{",
        "  \"session_id\": \"vfs_...\",",
        "  \"decision_id\": \"dec_...\",",
        "  \"status\": \"completed\",",
        "  \"outcome\": \"verified\",",
        "  \"assurance\": { \"scheme\": \"zoikoid-proofing\", \"level\": \"SANDBOX_SIMULATED\" },",
        "  \"checks\": [ { \"method\": \"face_match\", \"result\": \"pass\" } ],",
        "  \"reason_codes\": [],",
        "  \"evidence_receipt_id\": null,",
        "  \"policy_version\": \"pol_sandbox_simulated\",",
        "  \"decision_version\": 1,",
        "  \"supersedes_decision_id\": null,",
        "  \"sandbox\": true",
        "}",
      ) },
      { kind: "note", tone: "warn", title: "Act on the latest version", text: "If you handle decisions from webhooks, keep the highest decision_version you have seen for each session and ignore an older one that arrives late." },
    ],
  },
  {
    id: "sandbox",
    title: "The sandbox",
    blocks: [
      { kind: "p", text: "In the sandbox you choose what the check answers. Nothing real is checked and every result is marked simulated. Sandbox tests are never counted as usage." },
      { kind: "table", head: ["Outcome to send", "Session ends as", "Decision outcome"], rows: [
        ["approved", "completed", "verified"],
        ["declined", "completed", "not_verified"],
        ["inconclusive", "inconclusive", "inconclusive"],
        ["review", "manual_review", "review_required (a later approved or declined supersedes it)"],
        ["expired", "expired", "(no decision)"],
      ] },
      { kind: "p", text: "POST /sandbox/fixtures/{outcome}/sessions creates a verification already at that outcome in one call, with every field optional: ideal for a test suite. POST /sandbox/sessions/{id}:simulate moves one you created." },
    ],
  },
  {
    id: "webhooks",
    title: "Webhooks",
    blocks: [
      { kind: "p", text: "Register and verify an HTTPS endpoint under Developers, Webhooks, and subscribe it to these events. Each is a CloudEvents 1.0 JSON message with the details in data." },
      { kind: "table", head: ["Event type", "When"], rows: [
        ["com.zoiko.id.verification.api.session_decided.v1", "A verification got a decision. data has session_id, application_id, customer_reference, status, outcome, decision_id, decision_version, supersedes_decision_id, reason_codes, policy_version and sandbox."],
        ["com.zoiko.id.verification.api.session_closed.v1", "A verification ended without a decision: canceled or expired. data has session_id, application_id, customer_reference, status and sandbox."],
      ] },
      { kind: "list", items: [
        "Check the signature before you trust a message. Each delivery carries Content-Digest (SHA-256 of the body), Signature-Input and Signature (an RFC 9421 HTTP message signature, algorithm Ed25519) and X-Zoiko-Event-ID. Fetch the public key named by keyid from GET /v1/webhooks/signing-keys. Reject a missing, invalid or old signature.",
        "Deliveries can repeat or arrive out of order. De-duplicate by the event id and keep the highest decision_version per session.",
        "Answer 2xx quickly. Anything else is retried with growing delays for days, and you can replay a failed delivery from the console.",
        "A message from a sandbox verification says so in data.sandbox, so a test can never be mistaken for a live result.",
      ] },
      { kind: "code", caption: "A receiver that checks the body digest, de-duplicates and keeps the newest decision. The signature check itself uses your RFC 9421 library with the published key.", examples: [
        { lang: "node", code: lines(
          "import http from 'node:http';",
          "import crypto from 'node:crypto';",
          "",
          "const seen = new Set();            // event ids already handled (use a database in production)",
          "const newest = new Map();          // session_id -> highest decision_version handled",
          "",
          "http.createServer(async (req, res) => {",
          "  const chunks = [];",
          "  for await (const c of req) chunks.push(c);",
          "  const body = Buffer.concat(chunks);",
          "",
          "  // 1. the body must match the digest that was signed",
          "  const digest = 'sha-256=:' + crypto.createHash('sha256').update(body).digest('base64') + ':';",
          "  if (req.headers['content-digest'] !== digest) { res.writeHead(400).end(); return; }",
          "  // 2. verify Signature / Signature-Input with the key named by keyid (RFC 9421, Ed25519) here",
          "",
          "  const event = JSON.parse(body.toString('utf8'));",
          "  if (seen.has(event.id)) { res.writeHead(200).end(); return; }   // a repeat",
          "  seen.add(event.id);",
          "",
          "  if (event.type.endsWith('session_decided.v1')) {",
          "    const d = event.data;",
          "    if ((newest.get(d.session_id) ?? 0) < d.decision_version) {",
          "      newest.set(d.session_id, d.decision_version);",
          "      // act on d.outcome for d.customer_reference",
          "    }",
          "  }",
          "  res.writeHead(200).end();",
          "}).listen(8080);",
        ) },
      ] },
    ],
  },
  {
    id: "errors",
    title: "Errors",
    blocks: [
      { kind: "p", text: "Errors are JSON with an error object holding a code and a message. Fix a 4xx before retrying; retry a 5xx or a timeout with the same Idempotency-Key." },
      { kind: "table", head: ["Code", "HTTP", "Meaning", "What to do"], rows: ERRORS.map((e) => [e.code, String(e.status), e.meaning, e.action]) },
    ],
  },
  {
    id: "golive",
    title: "Going live",
    blocks: [
      { kind: "p", text: "In the console, Developers, Go live, shows what is left. You need a test verification that passed, one that failed, and a working webhook endpoint. Then you ask, accept the terms and data-processing terms, and our team reviews your company and the countries and checks you want. After approval you can create production applications." },
      { kind: "note", tone: "info", title: "What a live verification is", text: "A customer photographs an identity document and takes a live photograph of their face, and Zoiko iD compares the two. It runs no liveness test and does not check that the document is genuine, so a verified result carries the assurance level FACE_MATCH_NO_LIVENESS and lists one check: face_match. A face that does not match is a retake and, after the allowed tries, inconclusive. It is never reported as not verified, because the check cannot tell a different person from a poor photograph. Neither photograph is kept." },
      { kind: "note", tone: "warn", title: "Live verification is enabled country by country", text: "Your approval names the countries and the check (face_match). Zoiko iD must also have enabled each country. If it has not, creating a session answers MARKET_NOT_ENABLED and nothing is created. The document-only workflow is sandbox only and is refused in production with PRODUCT_NOT_ENTITLED. The sandbox can still return outcomes live never will (not_verified and a review), so handle them; they are marked sandbox." },
      { kind: "list", items: [
        "Your customer's page tells them who is asking, what is photographed, who processes it and that nothing is kept. They must agree before anything is photographed, and can stop at any time.",
        "Sending someone to the page is your job: launch_url comes back once from create. Their result comes to you by webhook and by the decision call. They are told only that the checking is done.",
        "Treat a verified result as evidence for your own decision about the customer, never as the decision, and never as proof that the person is present or the document is real.",
      ] },
    ],
  },
];
