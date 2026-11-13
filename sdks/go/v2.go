// Package zoiko is the /v2 global trust API client (ZID-INT-002, Document 02 v2.0). Standard library only; the HTTP client is injectable.
//
// Same rules as the Python and TypeScript clients: errors are RFC 9457 problems (V2Error), every POST that makes something takes a REQUIRED
// idempotency key (the caller's, never generated here), answers are the plain JSON document, and the SDK never holds a provider secret or
// contacts a source itself: it calls this platform, nothing else.
package zoiko

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// Doc is a JSON object.
type Doc = map[string]any

// V2Error is an RFC 9457 problem. Code is the document's public code (for example "unsupported_market", "idempotency_conflict").
type V2Error struct {
	Code      string
	Status    int
	Detail    string
	TraceID   string
	Retryable bool
}

func (e *V2Error) Error() string {
	if e.Detail != "" {
		return e.Code + ": " + e.Detail
	}
	return e.Code
}

// HTTPDoer is what the client needs from net/http; tests pass a fake.
type HTTPDoer interface {
	Do(*http.Request) (*http.Response, error)
}

// V2Client calls the /v2 API.
type V2Client struct {
	base  string
	token string
	http  HTTPDoer
}

// NewV2Client builds a client. Pass nil for the default HTTP client (15 second timeout).
func NewV2Client(baseURL, accessToken string, doer HTTPDoer) *V2Client {
	if doer == nil {
		doer = &http.Client{Timeout: 15 * time.Second}
	}
	return &V2Client{base: strings.TrimRight(baseURL, "/"), token: accessToken, http: doer}
}

type call struct {
	method, path string
	body         any
	raw          []byte
	key          string
	headers      map[string]string
}

func (c *V2Client) do(ctx context.Context, k call) (Doc, error) {
	var data []byte
	ctype := ""
	switch {
	case k.raw != nil:
		data, ctype = k.raw, "application/octet-stream"
	case k.body != nil:
		b, err := json.Marshal(k.body)
		if err != nil {
			return nil, err
		}
		data, ctype = b, "application/json"
	}
	target := k.path
	if !strings.HasPrefix(target, "http") {
		target = c.base + k.path
	}
	req, err := http.NewRequestWithContext(ctx, k.method, target, bytes.NewReader(data))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Accept", "application/json, application/problem+json")
	req.Header.Set("Authorization", "Bearer "+c.token)
	if ctype != "" {
		req.Header.Set("Content-Type", ctype)
	}
	if k.key != "" {
		req.Header.Set("Idempotency-Key", k.key)
	}
	for h, v := range k.headers {
		req.Header.Set(h, v)
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	payload, err := io.ReadAll(io.LimitReader(resp.Body, 8<<20))
	if err != nil {
		return nil, err
	}
	doc := Doc{}
	if len(payload) > 0 {
		if json.Unmarshal(payload, &doc) != nil {
			doc = Doc{}
		}
	}
	if resp.StatusCode >= 400 {
		e := &V2Error{Code: "http_error", Status: resp.StatusCode}
		if s, ok := doc["code"].(string); ok {
			e.Code = s
		}
		e.Detail, _ = doc["detail"].(string)
		e.TraceID, _ = doc["trace_id"].(string)
		e.Retryable, _ = doc["retryable"].(bool)
		return nil, e
	}
	return doc, nil
}

var errKey = &V2Error{Code: "invalid_request", Detail: "an idempotency key is required: a retry must reuse the same key"}

func needKey(key string) error {
	if strings.TrimSpace(key) == "" {
		return errKey
	}
	return nil
}

func (c *V2Client) post(ctx context.Context, path string, body any, key string) (Doc, error) {
	if err := needKey(key); err != nil {
		return nil, err
	}
	return c.do(ctx, call{method: "POST", path: path, body: body, key: key})
}

func (c *V2Client) get(ctx context.Context, path string) (Doc, error) {
	return c.do(ctx, call{method: "GET", path: path})
}

func merge(base Doc, extra Doc) Doc {
	for k, v := range extra {
		base[k] = v
	}
	return base
}

// ---- discovery

func (c *V2Client) Capabilities(ctx context.Context) (Doc, error) {
	return c.get(ctx, "/v2/capabilities")
}

func (c *V2Client) Coverage(ctx context.Context, country, profession string) (Doc, error) {
	q := "?country=" + url.QueryEscape(country)
	if profession != "" {
		q += "&profession=" + url.QueryEscape(profession)
	}
	return c.get(ctx, "/v2/coverage"+q)
}

// CaptureAlternatives lists the other ways to complete a check instead of a selfie or a photographed document. No reason is needed and
// choosing one is not a failed attempt; show the answer to the person, never decide for them. reason is optional ("" omits it).
func (c *V2Client) CaptureAlternatives(ctx context.Context, country, reason string) (Doc, error) {
	q := "?country=" + url.QueryEscape(country)
	if reason != "" {
		q += "&reason=" + url.QueryEscape(reason)
	}
	return c.get(ctx, "/v2/capture-alternatives"+q)
}

// ---- credentials

// VerifyCredential runs one credential check. extra may carry subject_ref, evidence_refs, purpose, consent_ref and consumer.
func (c *V2Client) VerifyCredential(ctx context.Context, credentialType string, issuerClaim, credentialClaim Doc, jurisdiction map[string]string, key string, extra Doc) (Doc, error) {
	body := merge(Doc{"credential_type": credentialType, "issuer_claim": issuerClaim, "credential_claim": credentialClaim, "jurisdiction": jurisdiction}, extra)
	return c.post(ctx, "/v2/credentials/verify", body, key)
}

func (c *V2Client) Credential(ctx context.Context, ref string) (Doc, error) {
	return c.get(ctx, "/v2/credentials/"+ref)
}

// ---- eligibility

func (c *V2Client) EvaluateEligibility(ctx context.Context, jurisdiction map[string]string, professionCode, serviceCode, key, subjectRef string) (Doc, error) {
	body := Doc{"jurisdiction": jurisdiction, "profession_code": professionCode, "service_code": serviceCode}
	if subjectRef != "" {
		body["subject_ref"] = subjectRef
	}
	return c.post(ctx, "/v2/eligibility/evaluate", body, key)
}

func (c *V2Client) Eligibility(ctx context.Context, ref string) (Doc, error) {
	return c.get(ctx, "/v2/eligibility/"+ref)
}

// EngagementCheck is the Zoikorum engagement eligibility per service, signed for Zoikorum.
func (c *V2Client) EngagementCheck(ctx context.Context, professionalSubjectRef, engagementRef, professionCode string, requestedServices []string, jurisdiction map[string]string, key string, extra Doc) (Doc, error) {
	body := merge(Doc{"professional_subject_ref": professionalSubjectRef, "engagement_ref": engagementRef, "profession_code": professionCode, "requested_services": requestedServices, "jurisdiction": jurisdiction}, extra)
	return c.post(ctx, "/v2/zoikorum/engagement-checks", body, key)
}

// ---- cases

func (c *V2Client) OpenCase(ctx context.Context, purpose string, jurisdiction, workflow map[string]string, requestedChecks []string, key string, extra Doc) (Doc, error) {
	body := merge(Doc{"purpose": purpose, "jurisdiction": jurisdiction, "workflow": workflow, "requested_checks": requestedChecks}, extra)
	return c.post(ctx, "/v2/verifications", body, key)
}

func (c *V2Client) Case(ctx context.Context, id string) (Doc, error) {
	return c.get(ctx, "/v2/verifications/"+id)
}

func (c *V2Client) SubmitTask(ctx context.Context, caseID, check string, body Doc, key string) (Doc, error) {
	return c.post(ctx, "/v2/verifications/"+caseID+"/tasks/"+check, body, key)
}

func (c *V2Client) CancelCase(ctx context.Context, id string) (Doc, error) {
	return c.do(ctx, call{method: "POST", path: "/v2/verifications/" + id + "/cancel"})
}

// ---- evidence

// UploadEvidence announces, then sends the bytes with the short-lived token. The SHA-256 and size are computed here so they match exactly.
// A rejected upload returns a *V2Error (for example "evidence_rejected_infected").
func (c *V2Client) UploadEvidence(ctx context.Context, content []byte, declaredType, key, subjectRef string) (Doc, error) {
	sum := sha256.Sum256(content)
	announce := Doc{"declared_type": declaredType, "size_bytes": len(content), "sha256": hex.EncodeToString(sum[:])}
	if subjectRef != "" {
		announce["subject_ref"] = subjectRef
	}
	made, err := c.post(ctx, "/v2/evidence/uploads", announce, key)
	if err != nil {
		return nil, err
	}
	u, _ := made["upload_url"].(string)
	t, _ := made["upload_token"].(string)
	if u == "" || t == "" {
		return nil, &V2Error{Code: "invalid_response", Detail: "the announcement carried no upload_url or upload_token"}
	}
	return c.do(ctx, call{method: "PUT", path: u, raw: content, headers: map[string]string{"X-Upload-Token": t}})
}

func (c *V2Client) EvidenceManifest(ctx context.Context, ref string) (Doc, error) {
	return c.get(ctx, "/v2/evidence/"+ref+"/manifest")
}

func (c *V2Client) DeleteEvidence(ctx context.Context, ref string) (Doc, error) {
	return c.do(ctx, call{method: "DELETE", path: "/v2/evidence/" + ref})
}

// ValidateDocument takes exactly one of evidenceRef (a stored upload) and content (checked in memory and discarded).
func (c *V2Client) ValidateDocument(ctx context.Context, declaredType string, checks []string, evidenceRef string, content []byte, extra Doc) (Doc, error) {
	if (evidenceRef == "") == (content == nil) {
		return nil, &V2Error{Code: "invalid_request", Detail: "send exactly one of evidenceRef and content"}
	}
	body := merge(Doc{"declared_type": declaredType, "checks": checks}, extra)
	if evidenceRef != "" {
		body["evidence_ref"] = evidenceRef
	} else {
		body["content_base64"] = base64.StdEncoding.EncodeToString(content)
	}
	return c.do(ctx, call{method: "POST", path: "/v2/documents/validate", body: body})
}

func (c *V2Client) DocumentResult(ctx context.Context, ref string) (Doc, error) {
	return c.get(ctx, "/v2/documents/"+ref)
}

// ---- organizations

// VerifyOrganization looks a company up in the official sources the country's route names; one outcome, no person named.
func (c *V2Client) VerifyOrganization(ctx context.Context, country, registrationNumber, legalName string, extra Doc) (Doc, error) {
	body := merge(Doc{"country": country, "registration_number": registrationNumber, "legal_name": legalName}, extra)
	return c.do(ctx, call{method: "POST", path: "/v2/organizations/verify", body: body})
}

// ---- issuers, presentations, events

// OpenIssuerCheck: the returned nonce is shown ONCE; a replay of this call will not return it again.
func (c *V2Client) OpenIssuerCheck(ctx context.Context, issuerRef, credentialType, key string, days int) (Doc, error) {
	body := Doc{"issuer_ref": issuerRef, "credential_type": credentialType}
	if days > 0 {
		body["days"] = days
	}
	return c.post(ctx, "/v2/issuer-checks", body, key)
}

func (c *V2Client) IssuerCheck(ctx context.Context, ref string) (Doc, error) {
	return c.get(ctx, "/v2/issuer-checks/"+ref)
}

// RequestPresentation: expiresInSeconds (0 = default) can only SHORTEN the request's life (at most 600).
func (c *V2Client) RequestPresentation(ctx context.Context, configurationID, recipientRef, purpose string, requestedClaims []string, jurisdiction map[string]string, key string, expiresInSeconds int, extra Doc) (Doc, error) {
	body := merge(Doc{"configuration_id": configurationID, "recipient_ref": recipientRef, "purpose": purpose, "requested_claims": requestedClaims, "jurisdiction": jurisdiction}, extra)
	if expiresInSeconds > 0 {
		body["expires_in_seconds"] = expiresInSeconds
	}
	return c.post(ctx, "/v2/presentations/requests", body, key)
}

func (c *V2Client) Presentation(ctx context.Context, ref string) (Doc, error) {
	return c.get(ctx, "/v2/presentations/"+ref)
}

func (c *V2Client) RegisterWebhook(ctx context.Context, endpoint string, eventTypes []string, key string, maxInflight int) (Doc, error) {
	body := Doc{"url": endpoint, "event_types": eventTypes}
	if maxInflight > 0 {
		body["max_inflight"] = maxInflight
	}
	return c.post(ctx, "/v2/webhook-endpoints", body, key)
}

func (c *V2Client) WebhookEndpoints(ctx context.Context) (Doc, error) {
	return c.get(ctx, "/v2/webhook-endpoints")
}

func (c *V2Client) Event(ctx context.Context, id string) (Doc, error) {
	return c.get(ctx, "/v2/events/"+id)
}

func (c *V2Client) ReplayEvent(ctx context.Context, id string) (Doc, error) {
	return c.do(ctx, call{method: "POST", path: "/v2/events/" + id + "/replay"})
}

// Paths is every route the client calls, for the parity check against the other clients and the server's published contract.
var Paths = []string{
	"GET /v2/capabilities",
	"GET /v2/coverage",
	"GET /v2/capture-alternatives",
	"POST /v2/credentials/verify",
	"GET /v2/credentials/{ref}",
	"POST /v2/eligibility/evaluate",
	"GET /v2/eligibility/{ref}",
	"POST /v2/zoikorum/engagement-checks",
	"POST /v2/verifications",
	"GET /v2/verifications/{id}",
	"POST /v2/verifications/{id}/tasks/{check}",
	"POST /v2/verifications/{id}/cancel",
	"POST /v2/evidence/uploads",
	"PUT /v2/evidence/uploads/{id}/content",
	"GET /v2/evidence/{ref}/manifest",
	"DELETE /v2/evidence/{ref}",
	"POST /v2/documents/validate",
	"GET /v2/documents/{ref}",
	"POST /v2/organizations/verify",
	"POST /v2/issuer-checks",
	"GET /v2/issuer-checks/{ref}",
	"POST /v2/presentations/requests",
	"GET /v2/presentations/{ref}",
	"POST /v2/webhook-endpoints",
	"GET /v2/webhook-endpoints",
	"GET /v2/events/{id}",
	"POST /v2/events/{id}/replay",
}

// AsV2Error unwraps err to a *V2Error when it is one.
func AsV2Error(err error) (*V2Error, bool) {
	var e *V2Error
	if errors.As(err, &e) {
		return e, true
	}
	return nil, false
}
