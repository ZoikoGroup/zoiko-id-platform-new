package zoiko

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"testing"
)

type fake struct {
	status int
	body   string
	calls  []*http.Request
	bodies [][]byte
	queue  []fakeAnswer
}

type fakeAnswer struct {
	status int
	body   string
}

func (f *fake) Do(r *http.Request) (*http.Response, error) {
	var b []byte
	if r.Body != nil {
		b, _ = io.ReadAll(r.Body)
	}
	f.calls = append(f.calls, r)
	f.bodies = append(f.bodies, b)
	st, body := f.status, f.body
	if len(f.queue) > 0 {
		st, body = f.queue[0].status, f.queue[0].body
		f.queue = f.queue[1:]
	}
	return &http.Response{StatusCode: st, Body: io.NopCloser(strings.NewReader(body))}, nil
}

func newFake(status int, body string) (*V2Client, *fake) {
	f := &fake{status: status, body: body}
	return NewV2Client("https://api.example.test/", "tok-9", f), f
}

func TestAPostSendsTheBearerTheKeyAndJSONAndReturnsThePlainDocument(t *testing.T) {
	c, f := newFake(200, `{"credential_ref":"cred_1"}`)
	out, err := c.VerifyCredential(context.Background(), "professional_license", Doc{"name": "Board"}, Doc{"license_ref": "L-1"},
		map[string]string{"country": "US"}, "k-0001", Doc{"purpose": "professional_onboarding"})
	if err != nil || out["credential_ref"] != "cred_1" {
		t.Fatalf("got %v %v", out, err)
	}
	r := f.calls[0]
	if r.Method != "POST" || r.URL.String() != "https://api.example.test/v2/credentials/verify" {
		t.Fatalf("bad request %s %s", r.Method, r.URL)
	}
	if r.Header.Get("Authorization") != "Bearer tok-9" || r.Header.Get("Idempotency-Key") != "k-0001" {
		t.Fatalf("bad headers %v", r.Header)
	}
	var sent Doc
	_ = json.Unmarshal(f.bodies[0], &sent)
	if sent["purpose"] != "professional_onboarding" {
		t.Fatalf("extra not sent: %v", sent)
	}
}

func TestTheIdempotencyKeyIsRequiredSoARetryIsAlwaysDeliberate(t *testing.T) {
	c, f := newFake(200, `{}`)
	ctx := context.Background()
	if _, err := c.OpenCase(ctx, "p", map[string]string{"country": "US"}, map[string]string{"profile_id": "x"}, []string{"identity"}, "  ", nil); err == nil {
		t.Fatal("an empty key must be refused")
	}
	if _, err := c.EvaluateEligibility(ctx, map[string]string{"country": "US"}, "p", "s", "", ""); err == nil {
		t.Fatal("an empty key must be refused")
	}
	if len(f.calls) != 0 {
		t.Fatal("nothing may be sent without a key")
	}
}

func TestAProblemDocumentBecomesAV2ErrorWithTheDocumentsCode(t *testing.T) {
	c, _ := newFake(409, `{"code":"idempotency_conflict","detail":"changed body","trace_id":"tr-1","retryable":false}`)
	_, err := c.EvaluateEligibility(context.Background(), map[string]string{"country": "US"}, "p", "s", "k-1", "")
	e, ok := AsV2Error(err)
	if !ok || e.Code != "idempotency_conflict" || e.Status != 409 || e.TraceID != "tr-1" {
		t.Fatalf("got %v", err)
	}
	c, _ = newFake(502, `not json`)
	_, err = c.Capabilities(context.Background())
	if e, ok := AsV2Error(err); !ok || e.Code != "http_error" || e.Status != 502 {
		t.Fatalf("got %v", err)
	}
}

func TestAnUploadAnnouncesTheDigestThenSendsTheBytesWithTheTokenOnly(t *testing.T) {
	c, f := newFake(200, `{}`)
	f.queue = []fakeAnswer{
		{201, `{"upload_url":"/v2/evidence/uploads/u1/content","upload_token":"tk"}`},
		{200, `{"evidence_ref":"ev_1","state":"READY"}`},
	}
	content := []byte("hello")
	out, err := c.UploadEvidence(context.Background(), content, "image/png", "k-up", "")
	if err != nil || out["evidence_ref"] != "ev_1" {
		t.Fatalf("got %v %v", out, err)
	}
	var announce Doc
	_ = json.Unmarshal(f.bodies[0], &announce)
	sum := sha256.Sum256(content)
	if announce["sha256"] != hex.EncodeToString(sum[:]) || announce["size_bytes"].(float64) != 5 {
		t.Fatalf("digest or size wrong: %v", announce)
	}
	put := f.calls[1]
	if put.Method != "PUT" || put.Header.Get("X-Upload-Token") != "tk" || string(f.bodies[1]) != "hello" {
		t.Fatalf("bad content call")
	}
	if put.Header.Get("Idempotency-Key") != "" {
		t.Fatal("the content call carries no idempotency key")
	}
}

func TestAnInfectedUploadRaisesTheServersCode(t *testing.T) {
	c, f := newFake(200, `{}`)
	f.queue = []fakeAnswer{
		{201, `{"upload_url":"/x","upload_token":"tk"}`},
		{422, `{"code":"evidence_rejected_infected","detail":"scan"}`},
	}
	_, err := c.UploadEvidence(context.Background(), []byte("x"), "image/png", "k", "")
	if e, ok := AsV2Error(err); !ok || e.Code != "evidence_rejected_infected" {
		t.Fatalf("got %v", err)
	}
}

func TestValidateDocumentNeedsExactlyOneSourceAndBase64sTheContent(t *testing.T) {
	c, f := newFake(200, `{"document_results":[]}`)
	ctx := context.Background()
	if _, err := c.ValidateDocument(ctx, "application/pdf", []string{"integrity"}, "", nil, nil); err == nil {
		t.Fatal("neither source must be refused")
	}
	if _, err := c.ValidateDocument(ctx, "application/pdf", []string{"integrity"}, "ev_1", []byte("x"), nil); err == nil {
		t.Fatal("both sources must be refused")
	}
	if len(f.calls) != 0 {
		t.Fatal("nothing may be sent")
	}
	if _, err := c.ValidateDocument(ctx, "application/pdf", []string{"integrity"}, "", []byte("hi"), nil); err != nil {
		t.Fatal(err)
	}
	var sent Doc
	_ = json.Unmarshal(f.bodies[0], &sent)
	if sent["content_base64"] != "aGk=" {
		t.Fatalf("content not encoded: %v", sent)
	}
}

func TestOrganizationLookupAndCoverageEscapeTheirInputs(t *testing.T) {
	c, f := newFake(200, `{"outcome":"VERIFIED"}`)
	ctx := context.Background()
	if _, err := c.Coverage(ctx, "G B", "a&b"); err != nil {
		t.Fatal(err)
	}
	if got := f.calls[0].URL.RawQuery; got != "country=G+B&profession=a%26b" {
		t.Fatalf("query %q", got)
	}
	if _, err := c.VerifyOrganization(ctx, "GB", "00123456", "Acme", nil); err != nil {
		t.Fatal(err)
	}
	if f.calls[1].URL.Path != "/v2/organizations/verify" || f.calls[1].Header.Get("Idempotency-Key") != "" {
		t.Fatal("organization lookup is stateless")
	}
}

func TestCaptureAlternativesIsAPlainGetWithAnOptionalReason(t *testing.T) {
	c, f := newFake(200, `{"counts_as_failed_attempt":false}`)
	ctx := context.Background()
	if _, err := c.CaptureAlternatives(ctx, "GB", ""); err != nil {
		t.Fatal(err)
	}
	if _, err := c.CaptureAlternatives(ctx, "GB", "no camera"); err != nil {
		t.Fatal(err)
	}
	if f.calls[0].Method != "GET" || f.calls[0].URL.Path != "/v2/capture-alternatives" || f.calls[0].URL.RawQuery != "country=GB" {
		t.Fatalf("first call %v", f.calls[0].URL)
	}
	if f.calls[1].URL.RawQuery != "country=GB&reason=no+camera" {
		t.Fatalf("query %q", f.calls[1].URL.RawQuery)
	}
}

func TestEveryRouteTheClientCallsIsListed(t *testing.T) {
	if len(Paths) != 27 {
		t.Fatalf("paths changed: %d", len(Paths))
	}
}

// ---- session

func TestNothingIsCapturedBeforeConsentAndAReviewDecisionNeedsAnAuthority(t *testing.T) {
	if CanMove("NEW", "COLLECTING") || CanMove("CONSENT_REQUIRED", "COLLECTING") {
		t.Fatal("capture before consent")
	}
	if _, err := Move("REVIEW_REQUIRED", "COMPLETE", " "); err == nil {
		t.Fatal("authority required")
	}
	if tr, err := Move("REVIEW_REQUIRED", "COMPLETE", "reviewer-1"); err != nil || tr.Event != "session.completed" {
		t.Fatalf("got %v %v", tr, err)
	}
}

func TestAnOutageKeepsTheSessionWaitingAndACorruptPayloadNeverAdvances(t *testing.T) {
	if AfterSource("UNAVAILABLE") != "SOURCE_PENDING" || AfterSource("CONFIRMED") != "COMPLETE" || AfterSource("UNCLEAR") != "REVIEW_REQUIRED" || AfterSource("TIMED_OUT") != "EXPIRED" {
		t.Fatal("source mapping")
	}
	if AfterCapture(false) != "RETRY_REQUIRED" || AfterCapture(true) != "EVIDENCE_RECEIVED" {
		t.Fatal("capture mapping")
	}
	for _, s := range SessionStates {
		if s == "FAILED" {
			t.Fatal("there is no FAILED state")
		}
	}
}

func TestWithdrawingConsentClosesCaptureAndTerminalStatesStayTerminal(t *testing.T) {
	if tr, err := WithdrawConsent("COLLECTING"); err != nil || tr.To != "CANCELED" {
		t.Fatal("withdraw while collecting")
	}
	if tr, err := WithdrawConsent("CONSENT_REQUIRED"); err != nil || tr.To != "DECLINED" {
		t.Fatal("withdraw before consent")
	}
	if _, err := WithdrawConsent("COMPLETE"); err == nil {
		t.Fatal("a finished session is untouched")
	}
	for _, s := range []string{"COMPLETE", "REJECTED", "EXPIRED", "DECLINED", "CANCELED", "UNAVAILABLE"} {
		if !IsTerminal(s) {
			t.Fatalf("%s must be terminal", s)
		}
	}
}
