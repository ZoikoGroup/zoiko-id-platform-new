package com.zoiko.id;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.LinkedList;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

class SdkTest {
    record Call(String method, String url, Map<String, String> headers, byte[] body) {}

    static final class Fake implements V2Client.Transport {
        final List<Call> calls = new ArrayList<>();
        final LinkedList<V2Client.Response> queue = new LinkedList<>();

        Fake(int status, String body) { queue.add(new V2Client.Response(status, body.getBytes(StandardCharsets.UTF_8))); }

        @Override
        public V2Client.Response send(String method, String url, Map<String, String> headers, byte[] body) {
            calls.add(new Call(method, url, headers, body));
            return queue.size() > 1 ? queue.removeFirst() : queue.getFirst();
        }
    }

    static final ObjectMapper JSON = new ObjectMapper();

    static V2Client client(Fake f) { return new V2Client("https://api.example.test/", "tok-9", f); }

    @Test
    void aPostSendsTheBearerTheKeyAndJsonAndReturnsThePlainDocument() throws Exception {
        Fake f = new Fake(200, "{\"credential_ref\":\"cred_1\"}");
        var out = client(f).verifyCredential("professional_license", Map.of("name", "Board"), Map.of("license_ref", "L-1"),
                Map.of("country", "US"), "k-0001", Map.of("purpose", "professional_onboarding"));
        assertEquals("cred_1", out.get("credential_ref"));
        Call c = f.calls.get(0);
        assertEquals("POST", c.method());
        assertEquals("https://api.example.test/v2/credentials/verify", c.url());
        assertEquals("Bearer tok-9", c.headers().get("Authorization"));
        assertEquals("k-0001", c.headers().get("Idempotency-Key"));
        assertEquals("professional_onboarding", JSON.readValue(c.body(), Map.class).get("purpose"));
    }

    @Test
    void theIdempotencyKeyIsRequiredSoARetryIsAlwaysDeliberate() {
        Fake f = new Fake(200, "{}");
        V2Client c = client(f);
        assertThrows(V2Error.class, () -> c.openCase("p", Map.of("country", "US"), Map.of("profile_id", "x"), List.of("identity"), " ", null));
        assertThrows(V2Error.class, () -> c.evaluateEligibility(Map.of("country", "US"), "p", "s", null, null));
        assertTrue(f.calls.isEmpty(), "nothing may be sent without a key");
    }

    @Test
    void aProblemDocumentBecomesAV2ErrorWithTheDocumentsCode() {
        V2Error e = assertThrows(V2Error.class, () -> client(new Fake(409,
                "{\"code\":\"idempotency_conflict\",\"detail\":\"changed body\",\"trace_id\":\"tr-1\",\"retryable\":false}"))
                .evaluateEligibility(Map.of("country", "US"), "p", "s", "k-1", null));
        assertEquals("idempotency_conflict", e.code());
        assertEquals(409, e.status());
        assertEquals("tr-1", e.traceId());
        V2Error other = assertThrows(V2Error.class, () -> client(new Fake(502, "not json")).capabilities());
        assertEquals("http_error", other.code());
    }

    @Test
    void anUploadAnnouncesTheDigestThenSendsTheBytesWithTheTokenOnly() throws Exception {
        Fake f = new Fake(201, "{\"upload_url\":\"/v2/evidence/uploads/u1/content\",\"upload_token\":\"tk\"}");
        f.queue.add(new V2Client.Response(200, "{\"evidence_ref\":\"ev_1\"}".getBytes(StandardCharsets.UTF_8)));
        byte[] content = "hello".getBytes(StandardCharsets.UTF_8);
        var out = client(f).uploadEvidence(content, "image/png", "k-up", null);
        assertEquals("ev_1", out.get("evidence_ref"));
        Map<?, ?> announce = JSON.readValue(f.calls.get(0).body(), Map.class);
        assertEquals(HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(content)), announce.get("sha256"));
        assertEquals(5, announce.get("size_bytes"));
        Call put = f.calls.get(1);
        assertEquals("PUT", put.method());
        assertEquals("tk", put.headers().get("X-Upload-Token"));
        assertFalse(put.headers().containsKey("Idempotency-Key"));
    }

    @Test
    void validateDocumentNeedsExactlyOneSourceAndBase64sTheContent() throws Exception {
        Fake f = new Fake(200, "{\"document_results\":[]}");
        V2Client c = client(f);
        assertThrows(V2Error.class, () -> c.validateDocument("application/pdf", List.of("integrity"), null, null, null));
        assertThrows(V2Error.class, () -> c.validateDocument("application/pdf", List.of("integrity"), "ev_1", new byte[] {1}, null));
        assertTrue(f.calls.isEmpty());
        c.validateDocument("application/pdf", List.of("integrity"), null, "hi".getBytes(StandardCharsets.UTF_8), null);
        assertEquals("aGk=", JSON.readValue(f.calls.get(0).body(), Map.class).get("content_base64"));
    }

    @Test
    void coverageEscapesItsInputsAndTheOrganizationLookupIsStateless() {
        Fake f = new Fake(200, "{\"outcome\":\"VERIFIED\"}");
        V2Client c = client(f);
        c.coverage("G B", "a&b");
        assertTrue(f.calls.get(0).url().endsWith("?country=G+B&profession=a%26b"));
        c.verifyOrganization("GB", "00123456", "Acme", null);
        assertEquals("https://api.example.test/v2/organizations/verify", f.calls.get(1).url());
        assertFalse(f.calls.get(1).headers().containsKey("Idempotency-Key"));
        assertEquals(27, V2Client.PATHS.size());
    }

    @Test
    void captureAlternativesIsAPlainGetWithAnOptionalReason() {
        Fake f = new Fake(200, "{\"counts_as_failed_attempt\":false}");
        V2Client c = client(f);
        c.captureAlternatives("GB", null);
        c.captureAlternatives("GB", "no camera");
        assertTrue(f.calls.get(0).url().endsWith("/v2/capture-alternatives?country=GB"));
        assertTrue(f.calls.get(1).url().endsWith("/v2/capture-alternatives?country=GB&reason=no+camera"));
    }

    // ------------------------------------------------------------------------------------------------------------ session

    @Test
    void nothingIsCapturedBeforeConsentAndAReviewDecisionNeedsAnAuthority() {
        assertFalse(Session.canMove("NEW", "COLLECTING"));
        assertFalse(Session.canMove("CONSENT_REQUIRED", "COLLECTING"));
        assertEquals("AUTHORITY_REQUIRED",
                assertThrows(Session.SessionError.class, () -> Session.move("REVIEW_REQUIRED", "COMPLETE", " ")).code());
        assertEquals("session.completed", Session.move("REVIEW_REQUIRED", "COMPLETE", "reviewer-1").event());
    }

    @Test
    void anOutageKeepsTheSessionWaitingAndACorruptPayloadNeverAdvances() {
        assertEquals("SOURCE_PENDING", Session.afterSource("UNAVAILABLE"));
        assertEquals("COMPLETE", Session.afterSource("CONFIRMED"));
        assertEquals("REVIEW_REQUIRED", Session.afterSource("UNCLEAR"));
        assertEquals("EXPIRED", Session.afterSource("TIMED_OUT"));
        assertEquals("RETRY_REQUIRED", Session.afterCapture(false));
        assertFalse(Session.STATES.contains("FAILED"));
    }

    @Test
    void withdrawingConsentClosesCaptureAndTerminalStatesStayTerminal() {
        assertEquals("CANCELED", Session.withdrawConsent("COLLECTING").to());
        assertEquals("DECLINED", Session.withdrawConsent("CONSENT_REQUIRED").to());
        assertThrows(Session.SessionError.class, () -> Session.withdrawConsent("COMPLETE"));
        for (String s : List.of("COMPLETE", "REJECTED", "EXPIRED", "DECLINED", "CANCELED", "UNAVAILABLE")) assertTrue(Session.isTerminal(s));
    }
}
