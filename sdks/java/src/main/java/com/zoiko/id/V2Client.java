package com.zoiko.id;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Duration;
import java.util.Base64;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * The /v2 global trust API client (ZID-INT-002, Document 02 v2.0). Same rules as the Python, TypeScript and Go clients: errors are
 * RFC 9457 problems ({@link V2Error}), every POST that makes something takes a REQUIRED idempotency key (the caller's, never generated
 * here), answers are the plain JSON document, and the SDK never holds a provider secret or contacts a source itself.
 */
public final class V2Client {
    /** One HTTP exchange; tests pass a fake. */
    @FunctionalInterface
    public interface Transport {
        Response send(String method, String url, Map<String, String> headers, byte[] body) throws IOException;
    }

    public record Response(int status, byte[] body) {}

    private static final ObjectMapper JSON = new ObjectMapper();

    private final String base;
    private final String token;
    private final Transport transport;

    public V2Client(String baseUrl, String accessToken) {
        this(baseUrl, accessToken, defaultTransport());
    }

    public V2Client(String baseUrl, String accessToken, Transport transport) {
        this.base = baseUrl.replaceAll("/+$", "");
        this.token = accessToken;
        this.transport = transport;
    }

    private static Transport defaultTransport() {
        HttpClient http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(15)).build();
        return (method, url, headers, body) -> {
            HttpRequest.Builder b = HttpRequest.newBuilder(URI.create(url)).timeout(Duration.ofSeconds(15));
            headers.forEach(b::header);
            b.method(method, body == null ? HttpRequest.BodyPublishers.noBody() : HttpRequest.BodyPublishers.ofByteArray(body));
            try {
                HttpResponse<byte[]> r = http.send(b.build(), HttpResponse.BodyHandlers.ofByteArray());
                return new Response(r.statusCode(), r.body());
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new IOException(e);
            }
        };
    }

    // ---------------------------------------------------------------------------------------------------------- plumbing

    private Map<String, Object> call(String method, String path, Object body, String key, byte[] raw, Map<String, String> extra) {
        Map<String, String> headers = new LinkedHashMap<>();
        headers.put("Accept", "application/json, application/problem+json");
        headers.put("Authorization", "Bearer " + token);
        byte[] data = null;
        try {
            if (raw != null) {
                data = raw;
                headers.put("Content-Type", "application/octet-stream");
            } else if (body != null) {
                data = JSON.writeValueAsBytes(body);
                headers.put("Content-Type", "application/json");
            }
        } catch (JsonProcessingException e) {
            throw new V2Error("invalid_request", 0, e.getMessage(), null, false);
        }
        if (key != null) headers.put("Idempotency-Key", key);
        if (extra != null) headers.putAll(extra);
        Response r;
        try {
            r = transport.send(method, path.startsWith("http") ? path : base + path, headers, data);
        } catch (IOException e) {
            throw new V2Error("transport_error", 0, e.getMessage(), null, true);
        }
        Map<String, Object> doc = Map.of();
        if (r.body() != null && r.body().length > 0) {
            try {
                doc = JSON.readValue(r.body(), new com.fasterxml.jackson.core.type.TypeReference<Map<String, Object>>() {});
            } catch (IOException e) {
                doc = Map.of();
            }
        }
        if (r.status() >= 400) {
            throw new V2Error(
                    String.valueOf(doc.getOrDefault("code", "http_error")),
                    r.status(),
                    String.valueOf(doc.getOrDefault("detail", "")),
                    doc.get("trace_id") == null ? null : String.valueOf(doc.get("trace_id")),
                    Boolean.TRUE.equals(doc.get("retryable")));
        }
        return doc;
    }

    private static String need(String key) {
        if (key == null || key.isBlank()) {
            throw new V2Error("invalid_request", 0, "an idempotency key is required: a retry must reuse the same key", null, false);
        }
        return key;
    }

    private Map<String, Object> post(String path, Map<String, Object> body, String key) {
        return call("POST", path, body, need(key), null, null);
    }

    private Map<String, Object> get(String path) {
        return call("GET", path, null, null, null, null);
    }

    private static Map<String, Object> body(Object... kv) {
        Map<String, Object> m = new LinkedHashMap<>();
        for (int i = 0; i < kv.length; i += 2) m.put((String) kv[i], kv[i + 1]);
        return m;
    }

    private static Map<String, Object> with(Map<String, Object> m, Map<String, Object> extra) {
        if (extra != null) m.putAll(extra);
        return m;
    }

    private static String enc(String s) {
        return URLEncoder.encode(s, StandardCharsets.UTF_8);
    }

    // ---------------------------------------------------------------------------------------------------------- discovery

    public Map<String, Object> capabilities() { return get("/v2/capabilities"); }

    /** The other ways to complete a check instead of a selfie or a photographed document. No reason is needed; choosing one is not a failed attempt. */
    public Map<String, Object> captureAlternatives(String country, String reason) {
        return get("/v2/capture-alternatives?country=" + enc(country) + (reason == null ? "" : "&reason=" + enc(reason)));
    }

    public Map<String, Object> coverage(String country, String profession) {
        return get("/v2/coverage?country=" + enc(country) + (profession == null ? "" : "&profession=" + enc(profession)));
    }

    // ---------------------------------------------------------------------------------------------------------- credentials

    /** One credential check; {@code extra} may carry subject_ref, evidence_refs, purpose, consent_ref and consumer. */
    public Map<String, Object> verifyCredential(String credentialType, Map<String, Object> issuerClaim, Map<String, Object> credentialClaim,
            Map<String, String> jurisdiction, String idempotencyKey, Map<String, Object> extra) {
        return post("/v2/credentials/verify", with(body("credential_type", credentialType, "issuer_claim", issuerClaim,
                "credential_claim", credentialClaim, "jurisdiction", jurisdiction), extra), idempotencyKey);
    }

    public Map<String, Object> credential(String ref) { return get("/v2/credentials/" + ref); }

    // ---------------------------------------------------------------------------------------------------------- eligibility

    public Map<String, Object> evaluateEligibility(Map<String, String> jurisdiction, String professionCode, String serviceCode,
            String idempotencyKey, String subjectRef) {
        Map<String, Object> b = body("jurisdiction", jurisdiction, "profession_code", professionCode, "service_code", serviceCode);
        if (subjectRef != null) b.put("subject_ref", subjectRef);
        return post("/v2/eligibility/evaluate", b, idempotencyKey);
    }

    public Map<String, Object> eligibility(String ref) { return get("/v2/eligibility/" + ref); }

    /** Zoikorum engagement eligibility per service, signed for Zoikorum. */
    public Map<String, Object> engagementCheck(String professionalSubjectRef, String engagementRef, String professionCode,
            List<String> requestedServices, Map<String, String> jurisdiction, String idempotencyKey, Map<String, Object> extra) {
        return post("/v2/zoikorum/engagement-checks", with(body("professional_subject_ref", professionalSubjectRef,
                "engagement_ref", engagementRef, "profession_code", professionCode, "requested_services", requestedServices,
                "jurisdiction", jurisdiction), extra), idempotencyKey);
    }

    // ---------------------------------------------------------------------------------------------------------- cases

    public Map<String, Object> openCase(String purpose, Map<String, String> jurisdiction, Map<String, String> workflow,
            List<String> requestedChecks, String idempotencyKey, Map<String, Object> extra) {
        return post("/v2/verifications", with(body("purpose", purpose, "jurisdiction", jurisdiction, "workflow", workflow,
                "requested_checks", requestedChecks), extra), idempotencyKey);
    }

    public Map<String, Object> verificationCase(String id) { return get("/v2/verifications/" + id); }

    public Map<String, Object> submitTask(String caseId, String check, Map<String, Object> body, String idempotencyKey) {
        return post("/v2/verifications/" + caseId + "/tasks/" + check, body, idempotencyKey);
    }

    public Map<String, Object> cancelCase(String id) { return call("POST", "/v2/verifications/" + id + "/cancel", null, null, null, null); }

    // ---------------------------------------------------------------------------------------------------------- evidence

    /** Announce, then send the bytes with the short-lived token. A rejected upload throws {@link V2Error} (for example evidence_rejected_infected). */
    public Map<String, Object> uploadEvidence(byte[] content, String declaredType, String idempotencyKey, String subjectRef) {
        String sha;
        try {
            sha = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(content));
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
        Map<String, Object> announce = body("declared_type", declaredType, "size_bytes", content.length, "sha256", sha);
        if (subjectRef != null) announce.put("subject_ref", subjectRef);
        Map<String, Object> made = post("/v2/evidence/uploads", announce, idempotencyKey);
        Object url = made.get("upload_url");
        Object tok = made.get("upload_token");
        if (url == null || tok == null) {
            throw new V2Error("invalid_response", 0, "the announcement carried no upload_url or upload_token", null, false);
        }
        return call("PUT", String.valueOf(url), null, null, content, Map.of("X-Upload-Token", String.valueOf(tok)));
    }

    public Map<String, Object> evidenceManifest(String ref) { return get("/v2/evidence/" + ref + "/manifest"); }

    public Map<String, Object> deleteEvidence(String ref) { return call("DELETE", "/v2/evidence/" + ref, null, null, null, null); }

    /** Exactly one of {@code evidenceRef} (a stored upload) and {@code content} (checked in memory and discarded). */
    public Map<String, Object> validateDocument(String declaredType, List<String> checks, String evidenceRef, byte[] content,
            Map<String, Object> extra) {
        if ((evidenceRef == null) == (content == null)) {
            throw new V2Error("invalid_request", 0, "send exactly one of evidenceRef and content", null, false);
        }
        Map<String, Object> b = with(body("declared_type", declaredType, "checks", checks), extra);
        if (evidenceRef != null) b.put("evidence_ref", evidenceRef);
        else b.put("content_base64", Base64.getEncoder().encodeToString(content));
        return call("POST", "/v2/documents/validate", b, null, null, null);
    }

    public Map<String, Object> documentResult(String ref) { return get("/v2/documents/" + ref); }

    // ---------------------------------------------------------------------------------------------------------- organizations

    /** A company lookup in the official sources the country's route names; one outcome, no person named. */
    public Map<String, Object> verifyOrganization(String country, String registrationNumber, String legalName, Map<String, Object> extra) {
        return call("POST", "/v2/organizations/verify",
                with(body("country", country, "registration_number", registrationNumber, "legal_name", legalName), extra), null, null, null);
    }

    // ---------------------------------------------------------------------------------------------------------- issuers, presentations, events

    /** The returned nonce is shown ONCE: a replay of this call will not return it again. */
    public Map<String, Object> openIssuerCheck(String issuerRef, String credentialType, String idempotencyKey, Integer days) {
        Map<String, Object> b = body("issuer_ref", issuerRef, "credential_type", credentialType);
        if (days != null) b.put("days", days);
        return post("/v2/issuer-checks", b, idempotencyKey);
    }

    public Map<String, Object> issuerCheck(String ref) { return get("/v2/issuer-checks/" + ref); }

    /** {@code expiresInSeconds} (null = default) can only SHORTEN the request's life (at most 600). */
    public Map<String, Object> requestPresentation(String configurationId, String recipientRef, String purpose, List<String> requestedClaims,
            Map<String, String> jurisdiction, String idempotencyKey, Integer expiresInSeconds, Map<String, Object> extra) {
        Map<String, Object> b = with(body("configuration_id", configurationId, "recipient_ref", recipientRef, "purpose", purpose,
                "requested_claims", requestedClaims, "jurisdiction", jurisdiction), extra);
        if (expiresInSeconds != null) b.put("expires_in_seconds", expiresInSeconds);
        return post("/v2/presentations/requests", b, idempotencyKey);
    }

    public Map<String, Object> presentation(String ref) { return get("/v2/presentations/" + ref); }

    public Map<String, Object> registerWebhook(String url, List<String> eventTypes, String idempotencyKey, Integer maxInflight) {
        Map<String, Object> b = body("url", url, "event_types", eventTypes);
        if (maxInflight != null) b.put("max_inflight", maxInflight);
        return post("/v2/webhook-endpoints", b, idempotencyKey);
    }

    public Map<String, Object> webhookEndpoints() { return get("/v2/webhook-endpoints"); }

    public Map<String, Object> event(String id) { return get("/v2/events/" + id); }

    public Map<String, Object> replayEvent(String id) { return call("POST", "/v2/events/" + id + "/replay", null, null, null, null); }

    /** Every route the client calls, for the parity check against the other clients and the server's published contract. */
    public static final List<String> PATHS = List.of(
            "GET /v2/capabilities", "GET /v2/coverage", "GET /v2/capture-alternatives", "POST /v2/credentials/verify", "GET /v2/credentials/{ref}",
            "POST /v2/eligibility/evaluate", "GET /v2/eligibility/{ref}", "POST /v2/zoikorum/engagement-checks",
            "POST /v2/verifications", "GET /v2/verifications/{id}", "POST /v2/verifications/{id}/tasks/{check}",
            "POST /v2/verifications/{id}/cancel", "POST /v2/evidence/uploads", "PUT /v2/evidence/uploads/{id}/content",
            "GET /v2/evidence/{ref}/manifest", "DELETE /v2/evidence/{ref}", "POST /v2/documents/validate", "GET /v2/documents/{ref}",
            "POST /v2/organizations/verify", "POST /v2/issuer-checks", "GET /v2/issuer-checks/{ref}",
            "POST /v2/presentations/requests", "GET /v2/presentations/{ref}", "POST /v2/webhook-endpoints",
            "GET /v2/webhook-endpoints", "GET /v2/events/{id}", "POST /v2/events/{id}/replay");
}
